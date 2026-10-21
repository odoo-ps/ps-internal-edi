# Part of Odoo. See LICENSE file for full copyright and licensing details.
from datetime import timedelta
from unittest.mock import patch

import requests

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, tagged

from odoo.addons.base.tests.common import BaseCommon
from odoo.addons.edi_base.tools import EdiTransportError


def _response(status=200, content=b"{}", json_data=None, url="https://api.example.com/v1/orders"):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = content
    if json_data is not None:
        response._content = requests.compat.json.dumps(json_data).encode()
    return response


@tagged("post_install", "-at_install")
class TestEdiConnection(BaseCommon):
    _test_user_groups = ("base.group_system",)  # connections are managed by administrators only

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.env["edi.connection"].create(
            {
                "name": "ACME",
                "transport": "http",
                "url": "https://api.example.com/v1",
            }
        )

    def _patch_request(self, *responses):
        """Patch the HTTP session; return the mock to inspect the calls and the sessions."""
        sessions = []

        def request(session, method, url, **kwargs):
            sessions.append(session)
            return responses[min(len(sessions), len(responses)) - 1]

        mock = patch.object(requests.Session, "request", autospec=True, side_effect=request)
        return mock, sessions

    def _patch_token(self, *json_data):
        return patch.object(
            requests, "post", autospec=True, side_effect=[_response(json_data=data) for data in json_data]
        )

    def test_check_url(self):
        with self.assertRaises(ValidationError):
            self.connection.url = "ftp://api.example.com"
        with self.assertRaises(ValidationError):
            self.connection.url = "https://"

    def test_check_auth_type(self):
        for auth_type in ("none", "basic", "api_key", "oauth2"):
            self.connection.auth_type = auth_type
        with self.assertRaises(ValidationError):  # defined in edi_base, but not accepted by HTTP
            self.connection.auth_type = "ssh_key"

    def test_onchange_transport_resets_auth_type(self):
        with Form(self.env["edi.connection"]) as form:
            form.name = "SFTP"
            form.url = "https://files.example.com"
            form.auth_type = "ssh_key"  # not accepted by http: reset when the transport is (re)set
            form.transport = "http"
            self.assertEqual(form.auth_type, "none")

    def test_auth_field_names(self):
        expected = {
            ("none", "client_credentials"): [],
            ("basic", "client_credentials"): ["username", "password"],
            ("ssh_key", "client_credentials"): ["username", "ssh_private_key", "ssh_passphrase"],
            ("oauth2", "password"): [
                "oauth2_grant_type",
                "oauth2_token_url",
                "oauth2_client_id",
                "oauth2_client_secret",
                "oauth2_scope",
                "oauth2_token_expiry",
                "username",
                "password",
            ],
        }
        connection = self.env["edi.connection"].new({"name": "New", "url": "https://x.example.com"})
        for (auth_type, grant_type), field_names in expected.items():
            connection.auth_type, connection.oauth2_grant_type = auth_type, grant_type
            self.assertEqual(connection.auth_field_names or [], field_names, auth_type)

    def test_receive_no_auth(self):
        mock, sessions = self._patch_request(_response(content=b'{"orders": []}'))
        with mock as request, self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "orders", params={"since": "2026-01-01"})
        request.assert_called_once_with(
            sessions[0], "get", "https://api.example.com/v1/orders", timeout=30, params={"since": "2026-01-01"}
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, b'{"orders": []}')
        self.assertEqual(messages[0].name, "orders")
        self.assertNotIn("Authorization", sessions[0].headers)

    def test_send_basic(self):
        self.connection.write({"auth_type": "basic", "username": "odoo", "password": "secret"})
        mock, sessions = self._patch_request(_response(status=201, content=b"created"))
        with mock as request, self.connection._edi_connect() as session:
            answer = self.connection._edi_send(
                session, "orders", b"<order/>", method="put", content_type="application/xml"
            )
        request.assert_called_once_with(
            sessions[0],
            "put",
            "https://api.example.com/v1/orders",
            timeout=30,
            params=None,
            data=b"<order/>",
            headers={"Content-Type": "application/xml"},
        )
        self.assertEqual(sessions[0].auth, ("odoo", "secret"))
        self.assertEqual(answer.content, b"created")
        self.assertEqual(answer.meta["status"], 201)

    def test_api_key_header_and_query(self):
        self.connection.write({"auth_type": "api_key", "api_key": "K3Y"})
        mock, sessions = self._patch_request(_response())
        with mock, self.connection._edi_connect() as session:
            self.connection._edi_receive(session, "orders")
        self.assertEqual(sessions[0].headers["Authorization"], "Bearer K3Y")

        self.connection.write({"api_key_location": "query", "api_key_name": "apikey", "api_key_prefix": False})
        mock, sessions = self._patch_request(_response())
        with mock, self.connection._edi_connect() as session:
            self.connection._edi_receive(session, "orders")
        self.assertEqual(sessions[0].params, {"apikey": "K3Y"})
        self.assertNotIn("Authorization", sessions[0].headers)

    def test_http_error(self):
        mock, _sessions = self._patch_request(_response(status=422, content=b'{"error": "invalid sku"}'))
        with mock, self.connection._edi_connect() as session, self.assertRaises(EdiTransportError) as error:
            self.connection._edi_send(session, "orders", b"{}")
        self.assertEqual(error.exception.content, b'{"error": "invalid sku"}')

    def test_network_error(self):
        with (
            patch.object(requests.Session, "request", side_effect=requests.exceptions.ConnectTimeout("timeout")),
            self.connection._edi_connect() as session,
            self.assertRaises(EdiTransportError),
        ):
            self.connection._edi_receive(session, "orders")

    def test_oauth2_token_cached(self):
        self.connection.write(
            {
                "auth_type": "oauth2",
                "oauth2_token_url": "https://auth.example.com/token",
                "oauth2_client_id": "client",
                "oauth2_client_secret": "secret",
                "oauth2_scope": "orders",
            }
        )
        mock, sessions = self._patch_request(_response())
        with self._patch_token({"access_token": "T1", "expires_in": 3600}) as post, mock:
            for _i in range(2):
                with self.connection._edi_connect() as session:
                    self.connection._edi_receive(session, "orders")
        post.assert_called_once_with(
            "https://auth.example.com/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "client",
                "client_secret": "secret",
                "scope": "orders",
            },
            cert=None,
            timeout=30,
        )
        self.assertEqual(sessions[1].headers["Authorization"], "Bearer T1")
        self.assertEqual(self.connection.oauth2_access_token, "T1")

        # changing the credentials drops the cached token
        self.connection.oauth2_client_secret = "new secret"
        self.assertFalse(self.connection.oauth2_access_token)

    def test_oauth2_password_grant_and_expiry(self):
        self.connection.write(
            {
                "auth_type": "oauth2",
                "oauth2_grant_type": "password",
                "oauth2_token_url": "https://auth.example.com/token",
                "username": "odoo",
                "password": "secret",
                "oauth2_access_token": "OLD",
                "oauth2_token_expiry": fields.Datetime.now() - timedelta(seconds=1),
            }
        )
        with self._patch_token({"access_token": "T2"}) as post:
            self.assertEqual(self.connection._oauth2_get_token(), "T2")
        self.assertEqual(
            post.call_args.kwargs["data"], {"grant_type": "password", "username": "odoo", "password": "secret"}
        )

    def test_oauth2_retry_once_on_401(self):
        self.connection.write(
            {
                "auth_type": "oauth2",
                "oauth2_token_url": "https://auth.example.com/token",
                "oauth2_access_token": "REVOKED",
                "oauth2_token_expiry": fields.Datetime.now() + timedelta(hours=1),
            }
        )
        mock, sessions = self._patch_request(_response(status=401), _response(content=b"ok"))
        with self._patch_token({"access_token": "FRESH"}), mock, self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "orders")
        self.assertEqual(messages[0].content, b"ok")
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[1].headers["Authorization"], "Bearer FRESH")

    def test_action_test(self):
        mock, _sessions = self._patch_request(_response(status=404))
        with mock:
            action = self.connection.action_test()
        self.assertEqual(action["params"]["type"], "success")

        self.connection.write({"auth_type": "api_key", "api_key": "WRONG"})
        mock, _sessions = self._patch_request(_response(status=401))
        with mock, self.assertRaises(UserError):
            self.connection.action_test()
