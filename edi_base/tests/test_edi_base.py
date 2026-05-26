from odoo import Command
from odoo.exceptions import ValidationError

from .test_edi_common import TestEDICommonBase


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
                "connection_id": self.folder_connection.id,
                "has_sub_integration": True,
                "sub_integration_ids": [
                    Command.create(
                        {
                            "name": "Test edi integration",
                            "type": "api",
                            "integration_flow": "out",
                            "connection_id": self.folder_connection.id,
                            "record_filter_id": record_filter_id.id,
                            "active": False,
                        }
                    )
                ],
            }
        )
        self.assertEqual(1, len(edi_parent_integration.sub_integration_ids))


class TestEdiEndpoint(TestEDICommonBase):
    def test_endpoint_create(self):
        """Create an endpoint and verify it's linked to its connection"""
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Test Endpoint",
                "connection_id": self.folder_connection.id,
                "method": "POST",
                "path": "/api/v1/test",
            }
        )
        self.assertEqual(endpoint.connection_id, self.folder_connection)
        self.assertIn(endpoint, self.folder_connection.endpoint_ids)

    def test_endpoint_constraint_mismatch(self):
        """endpoint_id must belong to integration's connection_id"""
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
            }
        )
        with self.assertRaises(ValidationError):
            self.new_env["edi.integration"].create(
                {
                    "name": "Test integration with wrong endpoint",
                    "type": "api",
                    "integration_flow": "out",
                    "connection_id": self.folder_connection.id,
                    "endpoint_id": endpoint.id,
                }
            )

    def test_endpoint_on_integration(self):
        """endpoint_id can be set when it belongs to the integration's connection"""
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Valid Endpoint",
                "connection_id": self.folder_connection.id,
                "method": "POST",
                "path": "/api/v1/orders",
            }
        )
        integration = self.new_env["edi.integration"].create(
            {
                "name": "Test integration with endpoint",
                "type": "api",
                "integration_flow": "out",
                "connection_id": self.folder_connection.id,
                "endpoint_id": endpoint.id,
            }
        )
        self.assertEqual(integration.endpoint_id, endpoint)
        self.assertIn(integration, endpoint.integration_ids)

    def test_endpoint_optional(self):
        """endpoint_id is optional: integration without endpoint is valid"""
        integration = self.new_env["edi.integration"].create(
            {
                "name": "Test integration without endpoint",
                "type": "api",
                "integration_flow": "out",
                "connection_id": self.folder_connection.id,
            }
        )
        self.assertFalse(integration.endpoint_id)
