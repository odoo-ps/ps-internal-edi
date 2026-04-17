from odoo.addons.edi_base.tests.test_edi_common import TestEDICommonBase
from odoo.tests import tagged


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdi2Steps(TestEDICommonBase):
    @classmethod
    def setUpClass(cls, integration_flow="in"):
        super().setUpClass()
        cls.integration = cls.Integration.with_context(autocommit=True, no_exception_log=True).create({
            "name": "Import Partner",
            "type": "api",
            "integration_flow": integration_flow,
            "synchronization_content_type": "csv",
            "connection_id": cls.folder_connection.id,
            "active": False,
        })
        cls.new_cr.commit()

    def test_edi_2steps_cron_creation(self):
        # 2-Steps queue cron should not exist before marking the integration as using the 2-steps process
        self.assertFalse(self.integration.edi_table_cron_id)

        # Mark integration as using the 2-Steps
        self.integration.use_edi_table = True
        self.assertTrue(self.integration.edi_table_cron_id)
        self.assertEqual(
            self.integration.edi_table_cron_id.name,f"Process EDI 2-steps queue for {self.integration.name}"
        )
