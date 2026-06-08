from ast import literal_eval
from unittest.mock import patch

from odoo import api, fields
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.edi_base.tests.test_edi_common import TestEDICommonBase


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdi2StepsInCases(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.integration = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Partner",
                "type": "api_2steps",
                "integration_flow": "in",
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "active": False,
                "use_edi_table": True,
            }
        )
        cls.new_cr.commit()

    def setUp(self):
        super().setUp()
        self._in_data = []
        patcher = patch.object(
            type(self.mock_connection),
            "_fetch_synchronizations",
            lambda conn_self, *a, **kw: list(self._in_data),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._clean_partners)

    def _clean_synchronizations(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.table.record"].search([]).unlink()
        super()._clean_synchronizations()

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env["res.partner"].search([("name", "like", "Partner Test")]).unlink()

    def test_import_partner(self):
        """Use an integration that import partners from file"""
        now = fields.Datetime.now()

        content = ["name,id\n", "Partner Test 1,partner_test_1\n", "Partner Test 2,partner_test_2\n"]
        self._in_data = [{"filename": "partner.csv", "content": "".join(content)}]

        self.integration.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 1, "The integration should create 1 table record")

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 0, "The integration should create 0 partners in the first step")

            self.assertCountEqual(
                literal_eval(table_records[:1].content),
                [
                    {"name": "Partner Test 1"},
                    {"name": "Partner Test 2"},
                ],
                "The content of the table record should be the content of 2 partners",
            )
            self.assertEqual(table_records[:1].state, "new", "The table record should be in 'new' state")

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "done", "The integration should have succeed")
            self.assertTrue(integration.last_success_date, "The integration should have the last success date set")
            self.assertGreaterEqual(
                integration.last_success_date, now, "The integration should be updated after the initial date"
            )

            sync = table_records.updated_by_sync_ids

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertTrue(sync.received_content, "The synchronization's content should be set")
            self.assertEqual(sync.received_content, "".join(content), "The content differs")

        # Let's trigger the second step
        self.integration._process_edi_table(self.integration.id)
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].browse(table_records.ids)
            self.assertEqual(
                table_records[:1].state, "done", "The table record should be in state 'done' after processing"
            )

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 2, "The integration should create 2 partners in the second step")

            sync = table_records.processed_by_sync_ids

            self.assertEqual(len(sync), 1, "The queue processing should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertTrue(sync.received_content, "The synchronization's content should be set")
            self.assertEqual(
                sync.received_content, "[{'name': 'Partner Test 1'}, {'name': 'Partner Test 2'}]", "The content differs"
            )

    def test_import_partner_report_error(self):
        """Use an integration that import partner from file with wrong record"""
        now = fields.Datetime.now()

        content = ["name,id\n", ",partner_test_1\n", "Partner Test 2,partner_test_2\n"]
        self._in_data = [{"filename": "partner.csv", "content": "".join(content)}]

        self.integration.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 1, "The integration should create 1 table record")

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 0, "The integration should create 0 partners in the first step")

            self.assertCountEqual(
                literal_eval(table_records[:1].content),
                [{"name": "Partner Test 2"}],
                "The content of the table record should be the content of 1 partner",
            )
            self.assertEqual(table_records[:1].state, "new", "The table record should be in 'new' state")

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "done", "The integration should have succeed")
            self.assertGreaterEqual(
                integration.last_success_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.integration.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertTrue(sync.received_content, "The synchronization's content should be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertEqual(
                sync.error_ids.description,
                "No value for field name, name is required \n ['', 'partner_test_1']",
                "The synchronization's error description differ",
            )

    @mute_logger("odoo.sql_db")
    def test_import_partner_crash(self):
        now = fields.Datetime.now()

        self._in_data = [{"filename": "partner.csv", "content": "raise"}]

        self.integration.process_integration()
        self.integration._process_edi_table(self.integration.id)

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 1, "The integration should have created 1 table record")

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "fail", "The integration should have failed")
            self.assertGreaterEqual(
                integration.last_failure_date, now, "The integration should be updated after the initial date"
            )

            sync = table_records.processed_by_sync_ids

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
            self.assertEqual(sync.received_content, "[{'name': False}]", "The synchronization's content should be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertIn(
                """new row for relation "res_partner" violates check constraint "res_partner_check_name"\n""",
                sync.error_ids.description,
                "The synchronization's error description should be set",
            )

    @mute_logger("odoo.sql_db")
    def test_import_partner_crash_raise(self):
        now = fields.Datetime.now()

        self._in_data = [{"filename": "partner.csv", "content": "raise"}]

        self.integration.process_integration()
        with self.assertRaisesRegex(
            Exception, 'new row for relation "res_partner" violates check constraint "res_partner_check_name"'
        ):
            self.integration.with_context(edi_raise_error=True)._process_edi_table(self.integration.id)

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 1, "The integration should not created 1 table record")

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 0, "The integration shouldn't create any partners")

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "fail", "The integration should have failed")
            self.assertGreaterEqual(
                integration.last_failure_date, now, "The integration should be updated after the initial date"
            )

            sync = table_records.processed_by_sync_ids

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
            self.assertEqual(sync.received_content, "[{'name': False}]", "The synchronization's content should be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertIn(
                """new row for relation "res_partner" violates check constraint "res_partner_check_name"\n""",
                sync.error_ids.description,
                "The synchronization's error description should be set",
            )

        self.env.cr._default_log_exceptions = True
