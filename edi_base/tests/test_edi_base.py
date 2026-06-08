import json
from unittest.mock import MagicMock, patch

import requests

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

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
class TestEdiEndpoint(TestEDICommonBase):
    def test_endpoint_create(self):
        """Create an endpoint and verify it's linked to its connection"""
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Test Endpoint",
                "connection_id": self.mock_connection.id,
                "method": "POST",
                "url": "https://fake.url/endpoints/endpoint",
            }
        )
        self.assertEqual(endpoint.connection_id, self.mock_connection)
        self.assertIn(endpoint, self.mock_connection.api_endpoint_ids)

    def test_endpoint_constraint_mismatch(self):
        """api_endpoint_id must belong to integration's connection_id"""
        other_connection = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Other Connection",
                    "type": "api",
                }
            )
        )
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Endpoint on other connection",
                "connection_id": other_connection.id,
                "method": "GET",
                "url": "https://fake.url/endpoints/endpoint",
            }
        )
        with self.assertRaises(ValidationError):
            self.new_env["edi.integration"].create(
                {
                    "name": "Test integration with wrong endpoint",
                    "type": "api",
                    "integration_flow": "out",
                    "connection_id": self.mock_connection.id,
                    "api_endpoint_id": endpoint.id,
                }
            )

    def test_endpoint_on_integration(self):
        """api_endpoint_id can be set when it belongs to the integration's connection"""
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Valid Endpoint",
                "connection_id": self.mock_connection.id,
                "method": "POST",
                "url": "https://fake.url/endpoints/endpoint",
            }
        )
        integration = self.new_env["edi.integration"].create(
            {
                "name": "Test integration with endpoint",
                "type": "api",
                "integration_flow": "out",
                "connection_id": self.mock_connection.id,
                "api_endpoint_id": endpoint.id,
            }
        )
        self.assertEqual(integration.api_endpoint_id, endpoint)
        self.assertIn(integration, endpoint.integration_ids)

    def test_endpoint_optional(self):
        """api_endpoint_id is optional: integration without endpoint is valid"""
        integration = self.new_env["edi.integration"].create(
            {
                "name": "Test integration without endpoint",
                "type": "api",
                "integration_flow": "out",
                "connection_id": self.mock_connection.id,
            }
        )
        self.assertFalse(integration.api_endpoint_id)


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
        from odoo import fields as odoo_fields

        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Auth Conn", "type": "api", "api_auth_type": "oauth2"})
        )
        conn.write({"api_token": "abc", "api_token_expires": odoo_fields.Datetime.now()})
        self.assertTrue(conn.api_token)
        conn.api_reset_token()
        self.assertFalse(conn.api_token)
        self.assertFalse(conn.api_token_expires)

    def test_token_endpoint_constraint(self):
        """token_endpoint_id must belong to the same connection"""
        other_conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Other Conn", "type": "api"})
        )
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Token EP",
                "connection_id": other_conn.id,
                "method": "POST",
                "url": "https://fake.url/endpoints/token",
            }
        )
        with self.assertRaises(ValidationError):
            self.mock_connection.write({"api_token_endpoint_id": endpoint.id})

    def test_get_token_requires_token_endpoint(self):
        """_get_token raises ValidationError when no token_endpoint_id is configured"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "No Token EP Conn", "type": "api", "api_auth_type": "oauth2"})
        )
        with self.assertRaises(ValidationError):
            conn._api_get_token()

    def test_get_token_uses_cache(self):
        """_get_token returns cached token when still valid"""
        from datetime import timedelta

        from odoo import fields as odoo_fields

        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Cache Token Conn", "type": "api", "api_auth_type": "oauth2"})
        )
        future = odoo_fields.Datetime.now() + timedelta(hours=1)
        conn.write({"api_token": "cached_token", "api_token_expires": future})
        result = conn._api_get_token()
        self.assertEqual(result, "cached_token")


@tagged("post_install", "-at_install")
class TestEdiApiCallBehavior(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.endpoint = cls.new_env["edi.endpoint"].create(
            {
                "name": "Test Endpoint",
                "connection_id": cls.mock_connection.id,
                "method": "GET",
                "url": "https://fake.url/api",
            }
        )
        cls.public_conn = (
            cls.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Public Conn", "type": "api", "api_auth_type": "public"})
        )
        cls.public_endpoint = cls.new_env["edi.endpoint"].create(
            {
                "name": "Public EP",
                "connection_id": cls.public_conn.id,
                "method": "GET",
                "url": "https://fake.url/public",
            }
        )
        cls.integration_xml = cls.new_env["edi.integration"].create(
            {
                "name": "XML Integration",
                "type": "api",
                "integration_flow": "in",
                "connection_id": cls.mock_connection.id,
                "api_endpoint_id": cls.endpoint.id,
                "response_content_type": "xml",
                "active": False,
            }
        )
        cls.new_cr.commit()

    def test_api_call_http_error_raises_validation_error(self):
        """_api_call raises ValidationError on HTTP 4xx/5xx"""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Client Error")

        with patch.object(requests.Session, "get", return_value=mock_response):
            with self.assertRaises(ValidationError):
                self.mock_connection._api_call(endpoint=self.endpoint)

    def test_api_call_connection_error_raises_validation_error(self):
        """_api_call raises ValidationError on network-level errors"""
        with patch.object(requests.Session, "get", side_effect=requests.exceptions.ConnectionError("timeout")):
            with self.assertRaises(ValidationError):
                self.mock_connection._api_call(endpoint=self.endpoint)

    def test_api_session_public_no_auth_header(self):
        """Public auth type creates a plain session without Authorization header"""
        with self.public_conn._api_get_session(self.public_endpoint) as session:
            self.assertNotIn("Authorization", session.headers)

    def test_api_wrap_response_xml_pretty_print(self):
        """_api_wrap_response pretty-prints valid XML when response_content_type='xml'"""
        raw_xml = "<root><child>value</child></root>"
        items = self.integration_xml._api_wrap_response(raw_xml)
        self.assertEqual(len(items), 1)
        self.assertIn("\n", items[0]["content"])
        self.assertIn("<child>value</child>", items[0]["content"])
        self.assertEqual(items[0]["filename"], self.endpoint.name)

    def test_api_wrap_response_xml_invalid_fallback(self):
        """_api_wrap_response returns raw content unchanged on invalid XML"""
        raw = "not xml at all"
        items = self.integration_xml._api_wrap_response(raw)
        self.assertEqual(items[0]["content"], raw)
