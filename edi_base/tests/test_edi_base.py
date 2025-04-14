from odoo import Command

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
