import csv
import json
from ast import literal_eval
from io import StringIO
from unittest.mock import patch

from odoo import api, fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.edi_base.tests.test_edi_common import TestEDICommonBase


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdi2StepsOutCases(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):
        cls.maxDiff = None
        super().setUpClass()
        cls.Partner = cls.new_env["res.partner"]
        cls.filter = cls.new_env["ir.filters"].create(
            {"name": "Export Partner", "model_id": "res.partner", "domain": '[["name", "ilike", "EDI TEST"]]'}
        )
        cls.integration = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner all",
                "type": "api_2steps",
                "integration_flow": "out",
                "synchronization_creation": 0,  # all
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "record_filter_id": cls.filter.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
                "use_edi_table": True,
            }
        )
        cls.edi_one = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner one",
                "type": "api_2steps",
                "integration_flow": "out",
                "synchronization_creation": 1,  # one
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "record_filter_id": cls.filter.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
                "use_edi_table": True,
            }
        )
        cls.edi_multi = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner multi",
                "type": "api_2steps",
                "integration_flow": "out",
                "synchronization_creation": 3,  # multi
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "record_filter_id": cls.filter.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
                "use_edi_table": True,
            }
        )
        cls.new_cr.commit()

        cls.country = cls.env.ref("base.be")

        cls.addClassCleanup(cls._clean_filters, cls)

    @mute_logger("odoo.models.unlink")
    def _clean_filters(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            # NOTE: We need to unset the filter on the integrations since the `ondelete`
            #       policy is defined as `restrict`, thus raising an error.
            integrations = self.integration | self.edi_one | self.edi_multi
            filters = integrations.record_filter_id
            integrations.with_env(env).write({"record_filter_id": False})
            filters.with_env(env).unlink()

    def _clean_synchronizations(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.table.record"].search([]).unlink()
        super()._clean_synchronizations()

    def setUp(self):
        super().setUp()
        self._sent_items = []
        patcher = patch.object(
            type(self.mock_connection),
            "_send_synchronization",
            lambda conn_self, filename, content, *a, **kw: self._sent_items.append((filename, content)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._clean_partners)

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        self.new_env["res.partner"].search([("name", "like", "EDI TEST")]).unlink()
        self.new_env.cr.commit()

    def test_export_partner(self):
        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": f"EDI TEST {str(i).zfill(3)}"} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.integration.process_integration()

        self.assertEqual(self._sent_items, [], "No file should be exported in the first step of the integration")

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 1, "The integration should create 1 table record")

            self.assertCountEqual(
                literal_eval(table_records[:1].content),
                [{"id": partner.id, "name": partner.name} for partner in partners],
                f"The content of the table record should be the content of {len(partners)} partners",
            )

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = table_records.updated_by_sync_ids

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be 'done'")
            self.assertEqual(
                sync.sent_content,
                table_records[:1].content,
                "Content should be the same between the synchronization and the table record",
            )
            self.assertEqual(len(sync.error_ids), 0, "No error should have happened during the synchronization")

        # Trigger second step
        self.integration._process_edi_table(self.integration.id)
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].browse(table_records.ids)

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = table_records.processed_by_sync_ids

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be 'done'")
            csv_content = "\r\n".join(["id,name"] + [f"{partner.id},{partner.name}" for partner in partners]) + "\r\n"
            self.assertEqual(
                sync.sent_content, csv_content, "The synchronization content should be the CSV content of the partners"
            )
            self.assertEqual(len(sync.error_ids), 0, "No error should have happened during the synchronization")

        outputs = self._sent_items
        self.assertEqual(len(outputs), 1)

        reader = csv.reader(StringIO(outputs[0][1]), delimiter=",")
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line, strict=True))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data["name"], "EDI TEST %s" % str(i).zfill(3))

    def test_export_partner_one(self):
        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": "EDI TEST %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi_one.process_integration()

        self.assertEqual(self._sent_items, [], "No file should be exported in the first step of the integration")

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(
                len(table_records),
                len(partners),
                f"The integration should create {len(partners)} table records (1 per partner)",
            )

            for table_record in table_records:
                table_record_content = literal_eval(table_record.content)
                partner_id = table_record_content[0]["id"]
                partner = partners.filtered(lambda p, pid=partner_id: p.id == pid)
                self.assertEqual(table_record_content, [{"id": partner.id, "name": partner.name}])

            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            synchronizations = table_records.updated_by_sync_ids

            self.assertEqual(
                len(synchronizations), len(partners), f"The integration should create {len(partners)} synchronization"
            )
            for sync in synchronizations:
                self.assertEqual(sync.state, "done", "The synchronization should be 'done'")
                self.assertEqual(len(sync.error_ids), 0, "No error should have happened during the synchronization")

        # Trigger second step
        self.edi_one._process_edi_table(self.edi_one.id)
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].browse(table_records.ids)

            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            synchronizations = table_records.processed_by_sync_ids

            self.assertEqual(
                len(synchronizations), len(partners), f"The integration should create {len(partners)} synchronization"
            )
            for sync in synchronizations:
                self.assertEqual(sync.state, "done", "The synchronization should be 'done'")
                self.assertEqual(len(sync.error_ids), 0, "No error should have happened during the synchronization")

        outputs = self._sent_items
        self.assertEqual(len(outputs), 20)

        for _, content in outputs:
            reader = csv.reader(StringIO(content), delimiter=",")
            header = reader.__next__()
            for line in reader:
                data = dict(zip(header, line, strict=True))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])

    def test_export_partner_multi(self):
        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": "EDI TEST %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi_multi.process_integration()

        self.assertEqual(self._sent_items, [], "No file should be exported in the first step of the integration")
        nb_records = round(len(partners) / self.edi_multi.synchronization_creation)

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(
                len(table_records),
                nb_records,
                f"The integration should create {nb_records} table records (3 per partner)",
            )

            for table_record in table_records:
                table_record_content = literal_eval(table_record.content)
                partner_ids = [p["id"] for p in table_record_content]
                content_partners = partners.filtered(lambda p, pids=partner_ids: p.id in pids)
                self.assertCountEqual(
                    table_record_content, [{"id": partner.id, "name": partner.name} for partner in content_partners]
                )

            integration = new_env["edi.integration"].browse(self.edi_multi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            synchronizations = table_records.updated_by_sync_ids

            self.assertEqual(
                len(synchronizations), nb_records, f"The integration should create {nb_records} synchronization"
            )
            for sync in synchronizations:
                self.assertEqual(sync.state, "done", "The synchronization should be 'done'")
                self.assertEqual(len(sync.error_ids), 0, "No error should have happened during the synchronization")

        # Trigger second step
        self.edi_multi._process_edi_table(self.edi_multi.id)
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].browse(table_records.ids)

            integration = new_env["edi.integration"].browse(self.edi_multi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            synchronizations = table_records.processed_by_sync_ids

            self.assertEqual(
                len(synchronizations), nb_records, f"The integration should create {nb_records} synchronization"
            )
            for sync in synchronizations:
                self.assertEqual(sync.state, "done", "The synchronization should be 'done'")
                self.assertEqual(len(sync.error_ids), 0, "No error should have happened during the synchronization")

        outputs = self._sent_items
        self.assertEqual(len(outputs), nb_records)

        result = {2: 1, 3: 6}
        for _, content in outputs:
            reader = csv.reader(StringIO(content), delimiter=",")
            header = reader.__next__()
            lines = list(reader)
            remaining_files = result.get(len(lines), 0)
            self.assertGreaterEqual(remaining_files, 1)
            result[len(lines)] -= 1
            for line in lines:
                data = dict(zip(header, line, strict=True))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])
        for value in result.values():
            self.assertEqual(value, 0)

    def test_export_partner_error(self):
        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST error"})
        self.new_env.cr.commit()

        self.integration.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 1, "The integration should create 1 table record")
            self.assertEqual(table_records.content, "[]", "The table content should not be empty")

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "done", "The integration should be 'done'")
            self.assertGreaterEqual(
                integration.last_success_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.integration.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertEqual(sync.sent_content, "[]", "The synchronization's content should be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertEqual(
                sync.error_ids.description,
                "Cannot export the partner",
                "The synchronization's error description differ",
            )

    @mute_logger("odoo.sql_db")
    def test_export_partner_crash(self):
        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST raise"})
        self.new_env.cr.commit()

        self.integration.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 0, "The integration should not have created any table record")

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "fail", "The integration should have failed")
            self.assertGreaterEqual(
                integration.last_failure_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.integration.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
            self.assertFalse(sync.sent_content, "The synchronization's content should not be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertIn(
                """new row for relation "res_partner" violates check constraint "res_partner_check_name"\n""",
                sync.error_ids.description,
                "The synchronization's error description should be set",
            )

    @mute_logger("odoo.sql_db")
    def test_export_partner_crash_raise(self):

        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST raise"})
        self.new_env.cr.commit()

        with self.assertRaises(UserError):
            self.integration.with_context(edi_raise_error=True).process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            table_records = new_env["edi.table.record"].search([("create_date", ">=", now)])
            self.assertEqual(len(table_records), 0, "The integration should not have created any table record")

            integration = new_env["edi.integration"].browse(self.integration.id)
            self.assertEqual(integration.last_state, "fail", "The integration should have failed")
            self.assertGreaterEqual(
                integration.last_failure_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.integration.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
            self.assertFalse(sync.sent_content, "The synchronization's content should not be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertIn(
                """new row for relation "res_partner" violates check constraint "res_partner_check_name"\n""",
                sync.error_ids.description,
                "The synchronization's error description should be set",
            )
