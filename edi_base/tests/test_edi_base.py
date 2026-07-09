import base64
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo import Command, api, fields
from odoo.exceptions import ValidationError
from odoo.service.model import call_kw, get_public_method
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from ..decorators import IntegrationCheck
from ..models.edi_connection import API_TOKEN_EXPIRY_FALLBACK
from .test_edi_common import TestEDICommonBase


@tagged("post_install", "-at_install")
class TestEdiBase(TestEDICommonBase):
    def test_sub_integration_ids(self):
        """
        Test if sub_integration_ids has inactive children
        """
        record_filter_id = self.new_env["ir.filters"].create({"name": "Test ir filters", "model_id": "res.partner"})
        edi_parent_integration = self.new_env["edi.integration"].create(
            {
                "name": "Test edi parent integration",
                "type": "api",
                "integration_flow": "out",
                "connection_id": self.mock_connection.id,
                "has_sub_integration": True,
                "sub_integration_ids": [
                    Command.create(
                        {
                            "name": "Test edi integration",
                            "type": "api",
                            "integration_flow": "out",
                            "connection_id": self.mock_connection.id,
                            "record_filter_id": record_filter_id.id,
                            "active": False,
                        }
                    )
                ],
            }
        )
        self.assertEqual(1, len(edi_parent_integration.sub_integration_ids))


@tagged("post_install", "-at_install")
class TestEdiCall(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.integration = cls.new_env["edi.integration"].create(
            {
                "name": "Test Call Integration",
                "type": "api",
                "integration_flow": "out",
                "connection_id": cls.mock_connection.id,
                "active": False,
            }
        )
        cls.new_cr.commit()

    def test_write_received_dict(self):
        """_write_received serializes a dict response into received_content"""
        sync = self.env["edi.synchronization"].create({"name": "Test Sync Dict", "integration_id": self.integration.id})
        response = {"status": "ok", "count": 3}
        sync._write_received(response)
        self.assertTrue(sync.received_content)
        self.assertEqual(json.loads(sync.received_content), response)

    def test_write_received_str(self):
        """_write_received stores a plain string response as-is"""
        sync = self.env["edi.synchronization"].create({"name": "Test Sync Str", "integration_id": self.integration.id})
        sync._write_received("OK")
        self.assertEqual(sync.received_content, "OK")

    def test_write_received_none(self):
        """_write_received with None stores an empty string"""
        sync = self.env["edi.synchronization"].create({"name": "Test Sync None", "integration_id": self.integration.id})
        sync._write_received(None)
        self.assertEqual(sync.received_content, "")


@tagged("post_install", "-at_install")
class TestEdiAuth(TestEDICommonBase):
    def test_reset_token_clears_fields(self):
        """reset_token clears token and token_expires"""

        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Auth Conn", "type": "api", "credential_type": "key_secret", "auth_method": "http_oauth2"})
        )
        conn.write({"cached_token": "abc", "cached_token_expires": fields.Datetime.now()})
        self.assertTrue(conn.cached_token)
        conn.reset_cached_token()
        self.assertFalse(conn.cached_token)
        self.assertFalse(conn.cached_token_expires)

    def test_get_token_requires_token_path(self):
        """_api_get_token raises ValidationError when token_path is not configured"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "No Token Endpoint Conn",
                    "type": "api",
                    "credential_type": "key_secret",
                    "auth_method": "http_oauth2",
                    "url": "https://fake.url",
                    # token_path intentionally omitted
                }
            )
        )
        with self.assertRaises(ValidationError):
            conn._api_get_token()

    def test_get_token_uses_cache(self):
        """_api_get_token returns cached token when still valid"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Cache Token Conn",
                    "type": "api",
                    "credential_type": "key_secret",
                    "auth_method": "http_oauth2",
                }
            )
        )
        future = fields.Datetime.now() + timedelta(hours=1)
        conn.write({"cached_token": "cached_token", "cached_token_expires": future})
        result = conn._api_get_token()
        self.assertEqual(result, "cached_token")

    def test_api_get_session_api_key(self):
        """bearer auth sets Authorization: Bearer <key> on the session"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "ApiKey Conn",
                    "type": "api",
                    "credential_type": "single_key",
                    "auth_method": "http_bearer",
                    "key": "my_secret_key",
                }
            )
        )
        with conn._api_get_session() as session:
            self.assertEqual(session.headers.get("Authorization"), "Bearer my_secret_key")

    def test_api_get_session_basic(self):
        """http_basic auth sets session.auth with username/password"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Basic Conn",
                    "type": "api",
                    "credential_type": "user_pass",
                    "auth_method": "http_basic",
                    "username": "user",
                    "password": "pass",
                }
            )
        )
        with conn._api_get_session() as session:
            self.assertEqual(session.auth, ("user", "pass"))

    def test_api_get_session_bearer_custom_header(self):
        """http_bearer respects key_header_name and key_format overrides"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Custom Header Conn",
                    "type": "api",
                    "credential_type": "single_key",
                    "auth_method": "http_bearer",
                    "key": "MY_KEY",
                    "key_header_name": "X-API-Key",
                    "key_format": "{}",
                }
            )
        )
        with conn._api_get_session() as session:
            self.assertEqual(session.headers.get("X-API-Key"), "MY_KEY")
            self.assertNotIn("Authorization", session.headers)

    def test_api_get_session_bearer_no_key_raises(self):
        """http_bearer without a key raises ValidationError"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {"name": "No Key Conn", "type": "api", "credential_type": "single_key", "auth_method": "http_bearer"}
            )
        )
        with self.assertRaises(ValidationError):
            conn._api_get_session()

    def test_api_get_session_basic_no_username_raises(self):
        """http_basic without a username raises ValidationError"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {"name": "No User Conn", "type": "api", "credential_type": "user_pass", "auth_method": "http_basic"}
            )
        )
        with self.assertRaises(ValidationError):
            conn._api_get_session()

    def test_api_get_token_payload_client_credentials(self):
        """key_secret credential_type produces a client_credentials grant payload"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "CC Payload Conn",
                    "type": "api",
                    "credential_type": "key_secret",
                    "auth_method": "http_oauth2",
                    "client_id": "cid",
                    "client_secret": "csec",
                    "scope": "read",
                }
            )
        )
        payload = conn._api_get_token_payload()
        self.assertEqual(payload["grant_type"], "client_credentials")
        self.assertEqual(payload["client_id"], "cid")
        self.assertEqual(payload["client_secret"], "csec")
        self.assertEqual(payload["scope"], "read")
        self.assertNotIn("username", payload)

    def test_api_get_token_payload_password_grant(self):
        """user_key_secret and user_pass credential_types produce a password grant payload"""
        for cred_type, extra in [
            ("user_key_secret", {"client_id": "cid", "client_secret": "csec"}),
            ("user_pass", {}),
        ]:
            conn = (
                self.new_env["edi.connection"]
                .with_context(mail_create_nolog=True)
                .create(
                    {
                        "name": f"PG Payload Conn {cred_type}",
                        "type": "api",
                        "credential_type": cred_type,
                        "auth_method": "http_oauth2",
                        "username": "usr",
                        "password": "pwd",
                        **extra,
                    }
                )
            )
            payload = conn._api_get_token_payload()
            self.assertEqual(payload["grant_type"], "password", f"Failed for {cred_type}")
            self.assertEqual(payload["username"], "usr")
            self.assertEqual(payload["password"], "pwd")

    def test_api_parse_token_response_expires_in_fallback(self):
        """_api_parse_token_response uses API_TOKEN_EXPIRY_FALLBACK when expires_in is absent"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {"name": "Fallback Conn", "type": "api", "credential_type": "key_secret", "auth_method": "http_oauth2"}
            )
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "tok"}
        token, expires_in = conn._api_parse_token_response(mock_resp)
        self.assertEqual(token, "tok")
        self.assertEqual(expires_in, API_TOKEN_EXPIRY_FALLBACK)


@tagged("post_install", "-at_install")
class TestEdiApiCallBehavior(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mock_connection.write({"url": "https://fake.url", "credential_type": "none", "auth_method": "public"})
        cls.public_conn = (
            cls.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Public Conn",
                    "type": "api",
                    "credential_type": "none",
                    "auth_method": "public",
                    "url": "https://fake.url",
                }
            )
        )
        cls.integration_xml = cls.new_env["edi.integration"].create(
            {
                "name": "XML Integration",
                "type": "api",
                "integration_flow": "in",
                "connection_id": cls.mock_connection.id,
                "path": "/api",
                "method": "get",
                "response_content_type": "xml",
                "active": False,
            }
        )
        cls.new_cr.commit()

    def test_api_call_http_error_raises_http_error(self):
        """_api_call raises HTTPError on HTTP 4xx/5xx (caller is responsible for converting it)"""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Client Error")

        with (
            patch.object(requests.Session, "get", return_value=mock_response),
            self.assertRaises(requests.exceptions.HTTPError)
        ):
            self.mock_connection._api_call(path="/api", method="get")

    def test_api_call_connection_error_raises_validation_error(self):
        """_api_call raises ValidationError on network-level errors"""
        with (
            patch.object(requests.Session, "get", side_effect=requests.exceptions.ConnectionError("timeout")),
            self.assertRaises(ValidationError)
        ):
            self.mock_connection._api_call(path="/api", method="get")

    def test_api_session_public_no_auth_header(self):
        """Public auth type creates a plain session without Authorization header"""
        with self.public_conn._api_get_session() as session:
            self.assertNotIn("Authorization", session.headers)

    def test_api_wrap_response_xml_pretty_print(self):
        """_api_wrap_response pretty-prints valid XML when response_content_type='xml'"""
        raw_xml = "<root><child>value</child></root>"
        items = self.integration_xml._api_wrap_response(raw_xml)
        self.assertEqual(len(items), 1)
        self.assertIn("\n", items[0]["content"])
        self.assertIn("<child>value</child>", items[0]["content"])
        self.assertNotIn("<?xml", items[0]["content"])
        self.assertEqual(items[0]["filename"], self.integration_xml.name)

    def test_api_wrap_response_xml_invalid_fallback(self):
        """_api_wrap_response returns raw content unchanged on invalid XML"""
        raw = "not xml at all"
        items = self.integration_xml._api_wrap_response(raw)
        self.assertEqual(items[0]["content"], raw)


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiApiPipeline(TestEDICommonBase):
    """Test API pipeline (IN/OUT flows) using path/method on integration"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mock_connection.write({"url": "https://fake.url", "credential_type": "none", "auth_method": "public"})
        cls.new_cr.commit()

    def _mock_http_response(self, json_data=None, text=None, status_code=200):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.raise_for_status.return_value = None
        if json_data is not None:
            mock_resp.text = json.dumps(json_data)
        else:
            mock_resp.text = text or ""
        return mock_resp

    def test_pipeline_out(self):
        """OUT integration with path+method uses _api_call and logs received_content on the sync"""
        filter_ = self.new_env["ir.filters"].create(
            {
                "name": "EDI Pipeline Test Filter",
                "model_id": "res.partner",
                "domain": '[["name","ilike","EDI PIPE TEST"]]',
            }
        )

        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner Pipeline",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "connection_id": self.mock_connection.id,
                "path": "/api/v1/partners",
                "method": "post",
                "record_filter_id": filter_.id,
                "active": False,
            }
        )
        self.new_cr.commit()

        self.addCleanup(self._cleanup_out_test, edi, filter_)

        now = fields.Datetime.now()
        self.new_env["res.partner"].create([{"name": f"EDI PIPE TEST {i}"} for i in range(3)])
        self.new_env.cr.commit()

        mock_resp = self._mock_http_response(json_data={"status": "ok"})
        with patch.object(type(edi), "_get_content", return_value="id,name\n1,Test\n"), patch(
            "requests.Session.request", return_value=mock_resp
        ):
            edi.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertEqual(integration.path, "/api/v1/partners")
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.sent_content, "sent_content (CSV payload) should be logged")
            self.assertTrue(sync.received_content, "received_content (API response) should be logged")

    def test_pipeline_out_http_error(self):
        """HTTP error body must be stored in received_content even when the API returns 4xx/5xx"""
        filter_ = self.new_env["ir.filters"].create(
            {
                "name": "EDI Pipeline HTTP Error Filter",
                "model_id": "res.partner",
                "domain": '[["name","ilike","EDI PIPE TEST http_error"]]',
            }
        )
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner Pipeline HTTP Error",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "connection_id": self.mock_connection.id,
                "path": "/api/v1/partners",
                "method": "post",
                "record_filter_id": filter_.id,
                "active": False,
            }
        )
        self.new_cr.commit()
        self.addCleanup(self._cleanup_out_test, edi, filter_)

        now = fields.Datetime.now()
        self.new_env["res.partner"].create({"name": "EDI PIPE TEST http_error"})
        self.new_env.cr.commit()

        error_body = '{"error": "Unprocessable Entity"}'
        mock_resp = MagicMock()
        mock_resp.text = error_body
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_resp)

        with patch.object(type(edi), "_get_content", return_value="id,name\n1,Test\n"), patch(
            "requests.Session.post", return_value=mock_resp
        ):
            edi.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "fail")

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "fail")
            self.assertEqual(
                sync.received_content,
                error_body,
                "HTTP error response body must be stored in received_content",
            )

    def test_pipeline_in(self):
        """IN integration with GET path uses _api_call and logs received_content on the sync"""
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Data Pipeline",
                "type": "api",
                "integration_flow": "in",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "store_received_content": True,
                "store_sent_content": True,
                "connection_id": self.mock_connection.id,
                "path": "/api/v1/data",
                "method": "get",
                "active": False,
            }
        )
        self.new_cr.commit()

        self.addCleanup(self._cleanup_in_test, edi)

        now = fields.Datetime.now()

        mock_resp = self._mock_http_response(text="name,id\n")
        with patch("requests.Session.request", return_value=mock_resp):
            edi.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "done")
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.received_content, "received_content (API response) should be logged")

    @mute_logger("odoo.models.unlink")
    def _cleanup_out_test(self, edi, filter_):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.synchronization"].search([("integration_id", "=", edi.id)]).unlink()
            env["res.partner"].search([("name", "ilike", "EDI PIPE TEST")]).unlink()
            edi.with_env(env).write({"record_filter_id": False})
            edi.with_env(env).unlink()
            filter_.with_env(env).unlink()

    @mute_logger("odoo.models.unlink")
    def _cleanup_in_test(self, edi):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.synchronization"].search([("integration_id", "=", edi.id)]).unlink()
            edi.with_env(env).unlink()

    @mute_logger("odoo.models.unlink")
    def _cleanup_auth_test(self, edi, filter_, partner_pattern):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.synchronization"].search([("integration_id", "=", edi.id)]).unlink()
            env["res.partner"].search([("name", "ilike", partner_pattern)]).unlink()
            edi.with_env(env).write({"record_filter_id": False})
            edi.with_env(env).unlink()
            filter_.with_env(env).unlink()

    def test_auth_api_key(self):
        """bearer auth sends Authorization: Bearer <key> header on every request"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "API Key Conn",
                    "type": "api",
                    "credential_type": "single_key",
                    "auth_method": "http_bearer",
                    "url": "https://fake.url",
                    "key": "secret123",
                }
            )
        )
        filter_ = self.new_env["ir.filters"].create(
            {
                "name": "ApiKey Filter",
                "model_id": "res.partner",
                "domain": '[["name","ilike","EDI APIKEY TEST"]]',
            }
        )
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export ApiKey",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "connection_id": conn.id,
                "path": "/api/export",
                "method": "post",
                "record_filter_id": filter_.id,
                "active": False,
            }
        )
        self.new_env["res.partner"].create([{"name": "EDI APIKEY TEST 1"}])
        self.new_cr.commit()
        self.addCleanup(self._cleanup_auth_test, edi, filter_, "EDI APIKEY TEST")

        captured = {}

        def fake_send(session_self_, prepared, **kwargs):
            captured["prepared"] = prepared
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            mock_resp.text = '{"ok": true}'
            return mock_resp

        with patch.object(type(edi), "_get_content", return_value="id,name\n1,Test\n"), patch(
            "requests.Session.send", fake_send
        ):
            edi.process_integration()

        prep = captured.get("prepared")
        self.assertIsNotNone(prep)
        self.assertEqual(prep.headers.get("Authorization"), "Bearer secret123")

    def test_auth_basic(self):
        """basic auth sends Authorization: Basic <base64(user:pass)> header on every request"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Basic Auth Conn",
                    "type": "api",
                    "credential_type": "user_pass",
                    "auth_method": "http_basic",
                    "url": "https://fake.url",
                    "username": "testuser",
                    "password": "testpass",
                }
            )
        )
        filter_ = self.new_env["ir.filters"].create(
            {
                "name": "Basic Filter",
                "model_id": "res.partner",
                "domain": '[["name","ilike","EDI BASIC TEST"]]',
            }
        )
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Basic",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "connection_id": conn.id,
                "path": "/api/export",
                "method": "post",
                "record_filter_id": filter_.id,
                "active": False,
            }
        )
        self.new_env["res.partner"].create([{"name": "EDI BASIC TEST 1"}])
        self.new_cr.commit()
        self.addCleanup(self._cleanup_auth_test, edi, filter_, "EDI BASIC TEST")

        captured = {}

        def fake_send(session_self_, prepared, **kwargs):
            captured["prepared"] = prepared
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            mock_resp.text = '{"ok": true}'
            return mock_resp

        with patch.object(type(edi), "_get_content", return_value="id,name\n1,Test\n"), patch(
            "requests.Session.send", fake_send
        ):
            edi.process_integration()

        prep = captured.get("prepared")
        self.assertIsNotNone(prep)
        expected = "Basic " + base64.b64encode(b"testuser:testpass").decode()
        self.assertEqual(prep.headers.get("Authorization"), expected)

    def test_auth_oauth2_pipeline(self):
        """oauth2 auth fetches a token then uses it as Bearer in the resource call"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "OAuth2 Conn",
                    "type": "api",
                    "credential_type": "key_secret",
                    "auth_method": "http_oauth2",
                    "url": "https://fake.url",
                    "token_path": "/oauth/token",
                    "client_id": "my_client",
                    "client_secret": "my_secret",
                }
            )
        )
        filter_ = self.new_env["ir.filters"].create(
            {
                "name": "OAuth2 Filter",
                "model_id": "res.partner",
                "domain": '[["name","ilike","EDI OAUTH2 TEST"]]',
            }
        )
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export OAuth2",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "connection_id": conn.id,
                "path": "/resource",
                "method": "post",
                "record_filter_id": filter_.id,
                "active": False,
            }
        )
        self.new_env["res.partner"].create([{"name": "EDI OAUTH2 TEST 1"}])
        self.new_cr.commit()
        self.addCleanup(self._cleanup_auth_test, edi, filter_, "EDI OAUTH2 TEST")

        token_resp = MagicMock()
        token_resp.raise_for_status.return_value = None
        token_resp.json.return_value = {"access_token": "MY_OAUTH2_TOKEN", "expires_in": 3600}

        captured = {}

        def fake_send(session_self_, prepared, **kwargs):
            captured["prepared"] = prepared
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            mock_resp.text = '{"ok": true}'
            return mock_resp

        with patch.object(type(edi), "_get_content", return_value="id,name\n1,Test\n"), patch(
            "odoo.addons.edi_base.models.edi_connection.requests.post", return_value=token_resp
        ), patch("requests.Session.send", fake_send):
            edi.process_integration()

        prep = captured.get("prepared")
        self.assertIsNotNone(prep)
        self.assertEqual(prep.headers.get("Authorization"), "Bearer MY_OAUTH2_TOKEN")

    def test_in_query_payload(self):
        """_build_in_payload is sent as POST body and logged as sent_content on the sync"""
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Query Payload",
                "type": "api",
                "integration_flow": "in",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "store_sent_content": True,
                "store_received_content": True,
                "connection_id": self.mock_connection.id,
                "path": "/api/query",
                "method": "post",
                "active": False,
            }
        )
        self.new_cr.commit()
        self.addCleanup(self._cleanup_in_test, edi)

        QUERY = {"from": "2024-01-01", "to": "2024-12-31"}
        captured = {}

        def fake_send(session_self_, prepared, **kwargs):
            captured["body"] = json.loads(prepared.body)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status.return_value = None
            mock_resp.text = "name,id\n"
            return mock_resp

        with patch.object(type(edi), "_build_in_payload", return_value=QUERY), patch(
            "requests.Session.send", fake_send
        ):
            edi.process_integration()

        self.assertEqual(captured.get("body"), QUERY)

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            sync = new_env["edi.synchronization"].search([("integration_id", "=", edi.id)])
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.sent_content, "sent_content should contain the query payload JSON")
            self.assertIn("2024-01-01", sync.sent_content)

    def test_in_json_split_multi_items(self):
        """_api_wrap_response splitting a response into N items creates N synchronizations"""
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Split Items",
                "type": "api",
                "integration_flow": "in",
                "synchronization_creation": 1,
                "synchronization_content_type": "csv",
                "store_received_content": True,
                "connection_id": self.mock_connection.id,
                "path": "/api/items",
                "method": "get",
                "active": False,
            }
        )
        self.new_cr.commit()
        self.addCleanup(self._cleanup_in_test, edi)

        ITEMS = [{"code": "T001"}, {"code": "T002"}, {"code": "T003"}]

        def fake_wrap(self_, raw_text):
            items = json.loads(raw_text)
            return [{"filename": f"item_{item['code']}.csv", "content": "name,id\n"} for item in items]

        mock_resp = self._mock_http_response(text=json.dumps(ITEMS))
        with patch.object(type(edi), "_api_wrap_response", fake_wrap), patch(
            "requests.Session.request", return_value=mock_resp
        ):
            edi.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            sync = new_env["edi.synchronization"].search([("integration_id", "=", edi.id)], order="id asc")
            self.assertEqual(len(sync), 3, "Each wrapped item should produce its own synchronization")
            for s in sync:
                self.assertEqual(s.state, "done")
            filenames = sync.mapped("filename")
            self.assertIn("item_T001.csv", filenames)
            self.assertIn("item_T002.csv", filenames)
            self.assertIn("item_T003.csv", filenames)


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiOAuth2(TestEDICommonBase):
    def test_public_method_rpc_descriptor(self):
        """get_public_method must return a plain callable, not the IC descriptor instance.

        Regression: IC.__get__(None, cls) used to return self, causing call_kw to invoke
        IC.__call__(recs) (the decorator constructor) instead of the actual method body.
        """
        method = get_public_method(self.mock_connection, "test")
        self.assertNotIsInstance(method, IntegrationCheck)
        self.assertTrue(callable(method))
        self.assertEqual(method.__name__, "test")

    @patch("odoo.addons.edi_base.models.edi_connection.requests.post")
    def test_test_via_rpc_path(self, mock_post):
        """test() called via Odoo's RPC dispatcher (call_kw) reaches the correct API implementation."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "FAKE_TOKEN_RPC", "expires_in": 3600}
        mock_post.return_value = mock_response

        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "OAuth2 RPC Path Conn",
                    "type": "api",
                    "credential_type": "user_key_secret",
                    "auth_method": "http_oauth2",
                    "url": "https://fake.url",
                    "token_path": "/token",
                    "client_id": "test_client",
                    "client_secret": "test_secret",
                    "username": "test_user",
                    "password": "test_password",
                }
            )
        )
        self.new_cr.commit()

        with patch.object(type(conn.env["bus.bus"]), "_sendone") as mock_sendone:
            call_kw(self.new_env["edi.connection"], "test", [[conn.id]], {})
        mock_sendone.assert_called_once()
        _, notif_type, payload = mock_sendone.call_args[0]
        self.assertEqual(notif_type, "simple_notification")
        self.assertIn("Authentication succeeded", payload["message"])

    @patch("odoo.addons.edi_base.models.edi_connection.requests.post")
    def test_get_token_full_flow(self, mock_post):
        """_api_get_token fetches and caches a token; test() reports authentication success"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "FAKE_TOKEN_123", "expires_in": 3600}
        mock_post.return_value = mock_response

        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "OAuth2 Full Flow Conn",
                    "type": "api",
                    "credential_type": "user_key_secret",
                    "auth_method": "http_oauth2",
                    "url": "https://fake.url",
                    "token_path": "/token",
                    "client_id": "test_client",
                    "client_secret": "test_secret",
                    "username": "test_user",
                    "password": "test_password",
                }
            )
        )
        self.new_cr.commit()

        conn.reset_cached_token()
        token = conn._api_get_token()
        self.assertEqual(token, "FAKE_TOKEN_123")
        self.assertEqual(conn.cached_token, "FAKE_TOKEN_123")

        with patch.object(type(conn.env["bus.bus"]), "_sendone") as mock_sendone:
            conn.test()
        mock_sendone.assert_called_once()
        _, notif_type, payload = mock_sendone.call_args[0]
        self.assertEqual(notif_type, "simple_notification")
        self.assertIn("Authentication succeeded", payload["message"])
        self.assertEqual(payload["type"], "success")
