# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools.binary import BinaryBytes

from odoo.addons.base.tests.common import BaseCommon
from odoo.addons.edi_base.models.edi_connection import EdiConnection
from odoo.addons.edi_base.tools import EdiTransportError


def _response(status=200, json_data=None, content=b"{}"):
    response = requests.Response()
    response.status_code = status
    response._content = requests.compat.json.dumps(json_data).encode() if json_data is not None else content
    return response


def _make_certificate(not_after):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "odoo-edi-test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return certificate, key


@tagged("post_install", "-at_install")
class TestEdiConnectionAuth(BaseCommon):
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
        cls.not_after = datetime(2030, 1, 1, tzinfo=UTC)
        cls.certificate, cls.key = _make_certificate(cls.not_after)
        cls.cert_pem = cls.certificate.public_bytes(serialization.Encoding.PEM)
        cls.key_pem = cls.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )

    def _receive(self):
        """Open a session and do one request, return the (session, request kwargs) used."""
        calls = []

        def request(session, method, url, **kwargs):
            calls.append((session, dict(kwargs, cert=session.cert)))
            return _response()

        with (
            patch.object(requests.Session, "request", autospec=True, side_effect=request),
            self.connection._edi_connect() as session,
        ):
            self.connection._edi_receive(session, "orders")
            cert = session.cert
        self.assertEqual(calls[0][0], session)
        return session, calls[0][1], cert

    # ------------------------------------------------------------
    # Extra headers
    # ------------------------------------------------------------

    def test_http_headers(self):
        self.connection.write(
            {
                "http_use_headers": True,
                "http_headers": "Ocp-Apim-Subscription-Key: abc:123\n\n  X-Api-Version : 2  \n",
                "auth_type": "api_key",
                "api_key": "K3Y",
            }
        )
        session, _kwargs, _cert = self._receive()
        self.assertEqual(session.headers["Ocp-Apim-Subscription-Key"], "abc:123")
        self.assertEqual(session.headers["X-Api-Version"], "2")
        self.assertEqual(session.headers["Authorization"], "Bearer K3Y")

        # headers kept but not sent when disabled
        self.connection.http_use_headers = False
        session, _kwargs, _cert = self._receive()
        self.assertNotIn("X-Api-Version", session.headers)

    def test_http_headers_invalid(self):
        for headers in ("X-Api-Version 2", "Bad Name: value", ": value"):
            with self.subTest(headers=headers), self.assertRaises(ValidationError):
                self.connection.write({"http_use_headers": True, "http_headers": headers})

    # ------------------------------------------------------------
    # Client certificate (mutual TLS)
    # ------------------------------------------------------------

    def test_client_certificate_pem(self):
        self.connection.write(
            {
                "tls_client_auth": True,
                "tls_client_certificate": BinaryBytes(self.cert_pem + self.key_pem),
            }
        )
        self.assertEqual(self.connection.tls_client_certificate_expiry, self.not_after.replace(tzinfo=None))
        _session, kwargs, (cert_path, key_path) = self._receive()
        self.assertEqual(kwargs["cert"], (cert_path, key_path))
        # the files only live as long as the session
        self.assertFalse(os.path.exists(cert_path) or os.path.exists(key_path))

    def test_client_certificate_files_content(self):
        encrypted_key = self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"secret"),
        )
        self.connection.write(
            {
                "tls_client_auth": True,
                "tls_client_certificate": BinaryBytes(self.cert_pem),
                "tls_client_key": BinaryBytes(encrypted_key),
                "tls_client_password": "secret",
            }
        )
        with self.connection._tls_client_certificate_files() as (cert_path, key_path):
            self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)
            with open(cert_path, "rb") as cert_file, open(key_path, "rb") as key_file:
                self.assertEqual(cert_file.read(), self.cert_pem)
                self.assertEqual(key_file.read(), self.key_pem)  # decrypted for the TLS library

    def test_client_certificate_pkcs12(self):
        p12 = pkcs12.serialize_key_and_certificates(
            b"odoo", self.key, self.certificate, None, serialization.BestAvailableEncryption(b"secret")
        )
        self.connection.write(
            {
                "tls_client_auth": True,
                "tls_client_certificate": BinaryBytes(p12),
                "tls_client_password": "secret",
            }
        )
        _session, kwargs, _cert = self._receive()
        self.assertTrue(kwargs["cert"])

        with self.assertRaises(ValidationError):
            self.connection.tls_client_password = "wrong"

    def test_client_certificate_invalid(self):
        with self.assertRaises(ValidationError):  # missing key
            self.connection.write({"tls_client_auth": True, "tls_client_certificate": BinaryBytes(self.cert_pem)})
        with self.assertRaises(ValidationError):  # not a certificate
            self.connection.write({"tls_client_auth": True, "tls_client_certificate": BinaryBytes(b"garbage")})
        with self.assertRaises(ValidationError):  # nothing uploaded
            self.connection.write({"tls_client_auth": True})

    # ------------------------------------------------------------
    # Custom authentication
    # ------------------------------------------------------------

    def test_custom_auth(self):
        self.connection.write({"auth_type": "custom", "auth_custom_code": "acme_hmac", "api_key": "K3Y"})
        with self.assertRaises(EdiTransportError):  # no implementation
            self._receive()

        def _http_authenticate_custom(connection, session):
            if connection.auth_custom_code != "acme_hmac":
                return super(EdiConnection, connection)._http_authenticate_custom(session)
            session.headers["X-Signature"] = f"signed with {connection.api_key}"

        with patch.object(EdiConnection, "_http_authenticate_custom", _http_authenticate_custom):
            session, _kwargs, _cert = self._receive()
        self.assertEqual(session.headers["X-Signature"], "signed with K3Y")

    # ------------------------------------------------------------
    # OAuth2 authorization code
    # ------------------------------------------------------------

    def _setup_authorization_code(self):
        self.connection.write(
            {
                "auth_type": "oauth2",
                "oauth2_grant_type": "authorization_code",
                "oauth2_authorize_url": "https://auth.example.com/authorize?tenant=acme",
                "oauth2_token_url": "https://auth.example.com/token",
                "oauth2_client_id": "client",
                "oauth2_client_secret": "secret",
                "oauth2_scope": "orders offline_access",
            }
        )

    def _authorize(self):
        """Simulate the redirection to the remote system; return the query parameters it receives."""
        action = self.connection.action_oauth2_authorize()
        self.assertTrue(action["url"].startswith("https://auth.example.com/authorize?tenant=acme&"))
        return {key: values[0] for key, values in parse_qs(urlsplit(action["url"]).query).items()}

    def test_oauth2_authorization_code(self):
        self._setup_authorization_code()
        with self.assertRaises(EdiTransportError):  # not connected yet
            self.connection._oauth2_get_token()

        params = self._authorize()
        self.assertEqual(params["response_type"], "code")
        self.assertEqual(params["redirect_uri"], f"{self.connection.get_base_url()}/edi/oauth2/callback")
        self.assertEqual(params["scope"], "orders offline_access")
        verifier = self.connection.oauth2_code_verifier
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual((params["code_challenge"], params["code_challenge_method"]), (challenge, "S256"))

        token = {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600}
        with patch.object(requests, "post", return_value=_response(json_data=token)) as post:
            self.connection._oauth2_authorization_callback(params["state"], "THE_CODE")
        self.assertEqual(
            post.call_args.kwargs["data"],
            {
                "client_id": "client",
                "client_secret": "secret",
                "grant_type": "authorization_code",
                "code": "THE_CODE",
                "redirect_uri": params["redirect_uri"],
                "code_verifier": verifier,
            },
        )
        self.assertEqual(self.connection.oauth2_access_token, "A1")
        self.assertEqual(self.connection.oauth2_refresh_token, "R1")
        self.assertFalse(self.connection.oauth2_code_verifier)

        # the state is one-time
        with self.assertRaises(UserError):
            self.connection._oauth2_authorization_callback(params["state"], "THE_CODE")

    def test_oauth2_authorization_code_bad_state(self):
        self._setup_authorization_code()
        params = self._authorize()
        state = params["state"]
        for bad_state in (f"{self.connection.id}.forged", state[:-1], ""):
            with self.subTest(state=bad_state), self.assertRaises(UserError):
                self.connection._oauth2_authorization_callback(bad_state, "THE_CODE")
        # bound to the user who started the authorization
        other_admin = self.env["res.users"].create(
            {
                "name": "Other Admin",
                "login": "other_admin",
                "group_ids": [(4, self.env.ref("base.group_system").id)],
            }
        )
        with self.assertRaises(UserError):
            self.connection.with_user(other_admin)._oauth2_authorization_callback(state, "THE_CODE")

    def test_oauth2_refresh_token(self):
        self._setup_authorization_code()
        self.connection.sudo().write({"oauth2_refresh_token": "R1"})
        # no rotation: the refresh token is kept
        with patch.object(requests, "post", return_value=_response(json_data={"access_token": "A2"})) as post:
            self.assertEqual(self.connection._oauth2_get_token(), "A2")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(post.call_args.kwargs["data"]["refresh_token"], "R1")
        self.assertEqual(self.connection.oauth2_refresh_token, "R1")

        # rotation
        self.connection._oauth2_reset_token()
        token = {"access_token": "A3", "refresh_token": "R2"}
        with patch.object(requests, "post", return_value=_response(json_data=token)):
            self.connection._oauth2_get_token()
        self.assertEqual(self.connection.oauth2_refresh_token, "R2")

        # revoked: the user has to connect again
        self.connection._oauth2_reset_token()
        with (
            patch.object(requests, "post", return_value=_response(400, json_data={"error": "invalid_grant"})),
            self.assertRaisesRegex(EdiTransportError, "click on Connect again"),
        ):
            self.connection._oauth2_get_token()

    def test_oauth2_refresh_token_reset(self):
        self._setup_authorization_code()
        self.connection.sudo().write({"oauth2_refresh_token": "R1"})
        self.connection.oauth2_client_secret = "rotated secret"  # same client: consent still valid
        self.assertEqual(self.connection.oauth2_refresh_token, "R1")
        self.connection.oauth2_scope = "orders invoices"  # new consent needed
        self.assertFalse(self.connection.oauth2_refresh_token)
