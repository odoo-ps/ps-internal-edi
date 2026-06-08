import base64
import csv
import json
from datetime import datetime, timedelta
from io import StringIO
from unittest import mock
from unittest.mock import MagicMock, patch

from odoo import api, fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.edi_base.models.edi_integration import ProcessIntegrationException
from odoo.addons.edi_base.tests.test_edi_common import TestEDICommonBase
from odoo.addons.test_http.tests.test_common import TestHttpBase


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiApiCases(TestEDICommonBase):
    def setUp(self):
        super().setUp()

        self.addCleanup(self._clean_edi)
        self.addCleanup(self._clean_partners)

    @mute_logger("odoo.models.unlink")
    def _clean_edi(self):
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = (
                new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
            )
            new_env["edi.synchronization"].search([("integration_id", "in", integration.ids)]).unlink()
            integration.unlink()

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env["res.partner"].search([("name", "like", "Test partner")]).unlink()

    @mute_logger("odoo.sql_db")
    def test_api_decorator(self):
        """Test Api decorator"""

        now = fields.Datetime.now()

        edi = self.new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
        self.assertFalse(edi)

        name = "Test partner"

        with mute_logger("odoo.addons.edi_base.models.decorator"):
            res_id = self.new_env["res.partner"].with_context(autocommit=True).create_partner({"name": name})
        self.assertTrue(res_id)

        partner = self.new_env["res.partner"].browse(res_id)
        self.assertEqual(partner.display_name, name)

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            # Check integration has been created
            edi = new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
            self.assertEqual(edi.last_state, "done")
            self.assertGreaterEqual(edi.last_success_date, now)

            # Check synchronization object has been created and is in state done
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.received_content)

    @mute_logger("odoo.sql_db")
    def test_api_decorator_error(self):
        """Test Api decorator with error"""

        now = fields.Datetime.now()

        from psycopg2 import IntegrityError

        with self.assertRaises(IntegrityError), mute_logger("odoo.addons.edi_base.models.decorator"):
            self.new_env["res.partner"].with_context(autocommit=True).create_partner({"name": False})

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            # Check integration has been created
            edi = new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
            self.assertTrue(edi)
            self.assertGreaterEqual(edi.last_failure_date, now)
            self.assertEqual(edi.last_state, "fail")

            # Check synchronization object has been created and is in state fail
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "fail")
            self.assertTrue(sync.received_content)
            self.assertEqual(len(sync.error_ids), 1)


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiApiCasesXMLRPC(TestHttpBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user = cls.env.ref("base.user_admin")
        user = user.with_user(user)
        key = (
            user.env["res.users.apikeys"]
            .sudo()
            ._generate(scope="rpc", name="test", expiration_date=datetime.now() + timedelta(days=0.5))
        )
        cls.bearer_header = {"Authorization": f"Bearer {key}"}

    def test_api_decorator_json(self):
        """Test Api decorator"""
        with mute_logger("odoo.addons.edi_base.models.decorator"):
            res_id = self.url_open(
                "/json/2/res.partner/create_partner",
                headers=self.bearer_header,
                json={"data": {"name": "Test partner"}},
            ).json()
        self.assertTrue(res_id)

        partner = self.env["res.partner"].browse(res_id)
        self.assertEqual(partner.display_name, "Test partner")

    @mute_logger("odoo.sql_db", "odoo.http")
    def test_api_decorator_error_json(self):
        """Test Api decorator with error"""
        with mute_logger("odoo.addons.edi_base.models.decorator"):
            res = self.url_open(
                "/json/2/res.partner/create_partner", headers=self.bearer_header, json={"data": {"name": False}}
            )
            self.assertEqual(res.status_code, 422)
            body = res.json()
            self.assertIn("Contacts require a name", body["message"])


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiINCases(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.integration = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Partner",
                "type": "api",
                "integration_flow": "in",
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "active": False,
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

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env["res.partner"].search([("name", "like", "Partner Test")]).unlink()

    def _set_in_data(self, content, filename="partner.csv"):
        self._in_data = [{"filename": filename, "content": content}]

    def test_import_partner(self):
        """Use an integration that imports partners from in-memory data"""

        now = fields.Datetime.now()

        content = "name,id\nPartner Test 1,partner_test_1\nPartner Test 2,partner_test_2\n"
        self._set_in_data(content)

        edi = self.integration
        edi.process_integration()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 2, "The integration should create 2 partners")

            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "done", "The integration should have succeeded")
            self.assertTrue(integration.last_success_date, "The integration should have the last success date set")
            self.assertGreaterEqual(
                integration.last_success_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertTrue(sync.received_content, "The synchronization's content should be set")
            self.assertEqual(sync.received_content, content, "The content differs")

    def test_import_partner_report_error(self):
        """Use an integration that imports partners with one invalid record"""

        now = fields.Datetime.now()

        content = "name,id\n,partner_test_1\nPartner Test 2,partner_test_2\n"
        self._set_in_data(content)

        edi = self.integration
        edi.process_integration()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 1, "The integration should create 1 partner")

            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "done", "The integration should have succeeded")
            self.assertGreaterEqual(
                integration.last_success_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertTrue(sync.received_content, "The synchronization's content should be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "The synchronization's error description should be set")
            self.assertEqual(
                sync.error_ids.description,
                "No value for field name, name is required \n ['', 'partner_test_1']",
                "The synchronization's error description differs",
            )

    @mute_logger("odoo.sql_db")
    def test_import_partner_crash(self):

        now = fields.Datetime.now()

        self._set_in_data("raise")

        edi = self.integration
        edi.process_integration()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 0, "The integration should not create any partners")

            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "fail", "The integration should have failed")
            self.assertGreaterEqual(
                integration.last_failure_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
            self.assertTrue(sync.received_content, "")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "")

    def test_import_partner_crash_get_in_content(self):

        now = fields.Datetime.now()

        edi = self.integration

        with mock.patch.object(
            type(edi), "_get_in_content", side_effect=ProcessIntegrationException("Failed fetching content")
        ):

            edi.process_integration()

            with self.registry.cursor() as new_cr:

                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

                partners = new_env["res.partner"].search([("write_date", ">=", now)])
                self.assertEqual(len(partners), 0, "The integration should not create any partners")

                integration = new_env["edi.integration"].browse(edi.id)
                self.assertEqual(integration.last_state, "fail", "The integration should have failed")
                self.assertGreaterEqual(
                    integration.last_failure_date, now, "The integration should be updated after the initial date"
                )

                sync = new_env["edi.synchronization"].search(
                    [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
                )

                self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
                self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
                # Failure is before fetching any content, so received_content should be empty
                self.assertFalse(sync.received_content, "")
                self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
                self.assertTrue(sync.error_ids.description, "")


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiOUTCases(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.Partner = cls.new_env["res.partner"]

        cls.filter = cls.new_env["ir.filters"].create(
            {"name": "Export Partner", "model_id": "res.partner", "domain": '[["name", "ilike", "EDI TEST"]]'}
        )

        cls.edi = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner all",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 0,  # all
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "record_filter_id": cls.filter.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
            }
        )
        cls.edi_one = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner one",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 1,  # one
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "record_filter_id": cls.filter.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
            }
        )
        cls.edi_multi = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner multi",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 3,  # multi
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "record_filter_id": cls.filter.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
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
            integrations = self.edi | self.edi_one | self.edi_multi
            filters = integrations.record_filter_id
            integrations.with_env(env).write({"record_filter_id": False})
            filters.with_env(env).unlink()

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

    def _get_out_items(self):
        """Return list of (filename, content) tuples written by the integration."""
        return self._sent_items

    def test_export_partner(self):

        now = fields.Datetime.now()

        self.Partner.create([{"name": f"EDI TEST {str(i).zfill(3)}"} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi.process_integration()

        out_items = self._get_out_items()
        self.assertEqual(len(out_items), 1)

        _, content = out_items[0]
        reader = csv.reader(StringIO(content), delimiter=",")
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line, strict=True))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data["name"], "EDI TEST %s" % str(i).zfill(3))

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.sent_content)
            self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_one(self):

        now = fields.Datetime.now()

        self.Partner.create([{"name": "EDI TEST %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi_one.process_integration()

        out_items = self._get_out_items()
        self.assertEqual(len(out_items), 20)

        for _, content in out_items:
            reader = csv.reader(StringIO(content), delimiter=",")
            header = reader.__next__()
            for line in reader:
                data = dict(zip(header, line, strict=True))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_one.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 20)
            for s in sync:
                self.assertEqual(s.state, "done")
                self.assertTrue(s.sent_content)
                self.assertEqual(len(s.error_ids), 0)

    def test_export_partner_multi(self):

        now = fields.Datetime.now()

        self.Partner.create([{"name": "EDI TEST %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi_multi.process_integration()

        out_items = self._get_out_items()
        self.assertEqual(len(out_items), 7)

        result = {2: 1, 3: 6}
        for _, content in out_items:
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

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi_multi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_multi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 7)
            for s in sync:
                self.assertEqual(s.state, "done")
                self.assertTrue(s.sent_content)
                self.assertEqual(len(s.error_ids), 0)

    def test_export_partner_error(self):

        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST error"})
        self.new_env.cr.commit()

        self.edi.process_integration()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertGreaterEqual(integration.last_success_date, now)
            self.assertEqual(integration.last_state, "done")

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.sent_content)
            self.assertEqual(len(sync.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_export_partner_crash(self):

        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST raise"})
        self.new_env.cr.commit()

        self.edi.process_integration()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_state, "fail")
            self.assertGreaterEqual(integration.last_failure_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "fail")
            self.assertEqual(len(sync.error_ids), 1)

    def test_export_partner_real_time(self):

        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": "EDI TEST %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        partners.write({"country_id": self.env.ref("base.be").id})
        self.edi.with_context(autocommit=True)._process_realtime(data=partners)

        # Check value has been properly written by business code
        self.assertEqual(partners.mapped("country_id").id, self.country.id)

        out_items = self._get_out_items()
        self.assertEqual(len(out_items), 1)

        _, content = out_items[0]
        reader = csv.reader(StringIO(content), delimiter=",")
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line, strict=True))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data["name"], "EDI TEST %s" % str(i).zfill(3))

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.sent_content)
            self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_real_time_error(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({"name": "EDI TEST error"})
        self.new_env.cr.commit()

        partner.write({"country_id": self.env.ref("base.be").id})
        self.edi.with_context(autocommit=True)._process_realtime(data=partner)

        self.assertEqual(partner.mapped("country_id").id, self.country.id)

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertGreaterEqual(integration.last_success_date, now)
            self.assertEqual(integration.last_state, "done")

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertEqual(len(sync.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_export_partner_real_time_crash(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({"name": "EDI TEST raise"})
        self.new_env.cr.commit()

        partner.write({"country_id": self.env.ref("base.be").id})
        self.edi.with_context(autocommit=True)._process_realtime(data=partner)

        self.assertEqual(partner.mapped("country_id").id, self.country.id)

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_state, "fail")
            self.assertGreaterEqual(integration.last_failure_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "fail")
            self.assertEqual(len(sync.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_export_partner_real_time_crash_raise(self):
        now = fields.Datetime.now()
        partner = self.Partner.create({"name": "EDI TEST raise"})
        self.new_env.cr.commit()

        partner.write({"country_id": self.env.ref("base.be").id})
        with self.assertRaises(UserError):
            self.edi.with_context(autocommit=True)._process_realtime(data=partner, raise_error=True)

        # Check value has been properly written by business code (the current cursor is not rolled back)
        self.assertEqual(partner.mapped("country_id").id, self.country.id)

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_state, "fail")
            self.assertGreaterEqual(integration.last_failure_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "fail")
            self.assertEqual(len(sync.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_error_after_realtime(self):
        """Test realtime integration
        Test a particular case when an error occurs after the realtime call.
        Then a potential data inconsistency may appear since the synchronizations
        have been set as success but the sent data are rolled back.
        The synchronizations should then be in error to alert of a potential inconsistency.
        """

        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": "EDI TEST ERROR %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        with self.assertRaises(ValueError):
            with self.registry.cursor() as new_cr:

                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

                partners = new_env["res.partner"].browse(partners.ids)
                for partner in partners:
                    partner.name = partner.name + " UPDATED"

                integration = new_env["edi.integration"].browse(self.edi_one.id)
                integration.with_context(autocommit=True)._process_realtime(data=partners)

                # Simulate an error after the realtime
                raise ValueError("Error after the real time integration")

        out_items = self._get_out_items()
        self.assertEqual(len(out_items), 20)

        for _, content in out_items:
            reader = csv.reader(StringIO(content), delimiter=",")
            header = reader.__next__()
            for line in reader:
                data = dict(zip(header, line, strict=True))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])
                self.assertTrue("UPDATED" in data["name"])  # updated data have been synchronized

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].browse(partners.ids)
            for partner in partners:
                self.assertTrue("EDI TEST" in partner.name)
                self.assertTrue("UPDATED" not in partner.name)  # data have been rolled back

            # Since there is an inconsistency, integration and synchronizations should be in error
            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_state, "fail")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_one.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 20)
            for s in sync:
                self.assertEqual(s.state, "fail")
                self.assertTrue(s.sent_content)
                self.assertEqual(len(s.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_error_after_commit_realtime(self):
        """Test realtime integration
        Test a particular case when an error occurs after the realtime call and commit on the data cursor.
        Since the data cursor has been committed before the error occurs, no inconsistency should be
        detected since the synchronized data are the one stored in the Odoo database.
        The synchronizations should then be in success.
        """

        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": "EDI TEST SUCCESS %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        with self.assertRaises(ValueError):
            with self.registry.cursor() as new_cr:

                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

                partners = new_env["res.partner"].browse(partners.ids)
                for partner in partners:
                    partner.name = partner.name + " UPDATED"

                integration = new_env["edi.integration"].browse(self.edi_one.id)
                integration.with_context(autocommit=True)._process_realtime(data=partners)

                new_env.cr.commit()  # data are committed

                # Simulate an error after the realtime and commit
                raise ValueError("Error after the real time integration")

        out_items = self._get_out_items()
        self.assertEqual(len(out_items), 20)

        for _, content in out_items:
            reader = csv.reader(StringIO(content), delimiter=",")
            header = reader.__next__()
            for line in reader:
                data = dict(zip(header, line, strict=True))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])
                self.assertTrue("UPDATED" in data["name"])  # updated data have been synchronized

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].browse(partners.ids)
            for partner in partners:
                self.assertTrue("EDI TEST" in partner.name)
                self.assertTrue("UPDATED" in partner.name)  # data have not been rolled back

            # Since there is no inconsistency, integration and synchronizations should be in success
            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_state, "done")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_one.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 20)
            for s in sync:
                self.assertEqual(s.state, "done")
                self.assertTrue(s.sent_content)
                self.assertEqual(len(s.error_ids), 0)


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiBase(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.integration = cls.Integration.create(
            {
                "name": "Import Partner",
                "type": "api",
                "integration_flow": "in",
                "synchronization_content_type": "csv",
                "connection_id": cls.mock_connection.id,
                "active": False,
            }
        )
        cls.new_env.cr.commit()

    @mute_logger("odoo.addons.edi_base.models.edi_integration")
    def test_set_status_01(self):
        """
        Test integration's initial status
        """
        self.integration._set_status()
        self.assertEqual(self.integration.last_state, "no_sync")
        self.assertFalse(self.integration.last_success_date)
        self.assertFalse(self.integration.last_failure_date)

    def test_set_status_02(self):
        """
        Test integration's status after success synchronization
        """

        now = fields.Datetime.now()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 1", "integration_id": self.integration.id}
            )

            sync.write({"state": "done", "synchronization_date": now})

            sync.flush_recordset(fnames=["state", "synchronization_date"])

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status(sync)

            self.assertEqual(integration.last_state, "done")
            self.assertTrue(integration.last_success_date)
            self.assertFalse(integration.last_failure_date)

    def test_set_status_03(self):
        """
        Test integration's status after fail synchronization
        """

        now = fields.Datetime.now()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 1", "integration_id": self.integration.id}
            )

            sync.write({"state": "fail", "synchronization_date": now})

            sync.flush_recordset()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status(sync)

            self.assertEqual(integration.last_state, "fail")
            self.assertFalse(integration.last_success_date)
            self.assertTrue(integration.last_failure_date)

    def test_set_status_04(self):
        """
        Test integration's status after new success synchronization
        """

        now = fields.Datetime.now()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            sync = new_env["edi.synchronization"].create(
                {
                    "name": "Synchronization 1",
                    "integration_id": self.integration.id,
                    "state": "fail",
                    "synchronization_date": now,
                }
            )
            new_cr.commit()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status(sync)

            self.assertEqual(integration.last_state, "fail")
            self.assertFalse(integration.last_success_date)
            self.assertTrue(integration.last_failure_date)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 2", "integration_id": self.integration.id}
            )
            sync.write({"state": "done", "synchronization_date": now + timedelta(days=1)})
            sync.flush_recordset()
            integration._set_status(sync)

            self.assertEqual(integration.last_state, "done")
            self.assertTrue(integration.last_success_date)
            self.assertEqual(integration.last_failure_date, now)

    def test_set_status_05(self):
        """
        Test integration's status after new fail synchronization
        """

        now = fields.Datetime.now()

        with self.registry.cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env["edi.synchronization"].create(
                {
                    "name": "Synchronization 1",
                    "integration_id": self.integration.id,
                    "state": "done",
                    "synchronization_date": now,
                }
            )

            new_cr.commit()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status(sync)

            self.assertEqual(integration.last_state, "done")
            self.assertTrue(integration.last_success_date)
            self.assertFalse(integration.last_failure_date)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 2", "integration_id": self.integration.id}
            )

            sync.write({"state": "fail", "synchronization_date": now + timedelta(days=1)})

            sync.flush_recordset()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status(sync)

            self.assertEqual(integration.last_state, "fail")
            self.assertEqual(integration.last_success_date, now)
            self.assertTrue(integration.last_failure_date)


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiEndpointCases(TestEDICommonBase):
    """Test edi.endpoint model and its integration with edi.integration"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.endpoint = cls.new_env["edi.endpoint"].create(
            {
                "name": "Test API Endpoint",
                "connection_id": cls.mock_connection.id,
                "method": "POST",
                "url": "https://fake.url/endpoints/endpoint",
            }
        )
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

    def test_endpoint_pipeline_out(self):
        """OUT integration with endpoint uses _api_call and logs received_content on the sync"""
        filter_ = self.new_env["ir.filters"].create(
            {
                "name": "EDI Endpoint Test Filter",
                "model_id": "res.partner",
                "domain": '[["name","ilike","EDI EP TEST"]]',
            }
        )

        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Export Partner Endpoint",
                "type": "api",
                "integration_flow": "out",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "connection_id": self.mock_connection.id,
                "api_endpoint_id": self.endpoint.id,
                "record_filter_id": filter_.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
            }
        )
        self.new_cr.commit()

        self.addCleanup(self._cleanup_endpoint_test, edi, filter_)

        now = fields.Datetime.now()
        self.new_env["res.partner"].create([{"name": f"EDI EP TEST {i}"} for i in range(3)])
        self.new_env.cr.commit()

        mock_resp = self._mock_http_response(json_data={"status": "ok"})
        with patch("requests.Session.request", return_value=mock_resp):
            edi.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertEqual(integration.api_endpoint_id.id, self.endpoint.id)
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.sent_content, "sent_content (CSV payload) should be logged")
            self.assertTrue(sync.received_content, "received_content (API response) should be logged")

    def test_endpoint_pipeline_in(self):
        """IN integration with GET endpoint uses _api_call and logs received_content on the sync"""
        get_endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Test GET Endpoint",
                "connection_id": self.mock_connection.id,
                "method": "GET",
                "url": "https://fake.url/api/v1/data",
            }
        )
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Data Endpoint",
                "type": "api",
                "integration_flow": "in",
                "synchronization_creation": 0,
                "synchronization_content_type": "csv",
                "store_received_content": True,
                "store_sent_content": True,
                "connection_id": self.mock_connection.id,
                "api_endpoint_id": get_endpoint.id,
                "active": False,
            }
        )
        self.new_cr.commit()

        self.addCleanup(self._cleanup_endpoint_in_test, edi, get_endpoint)

        now = fields.Datetime.now()

        # The API returns plain CSV. _api_wrap_response wraps it in a single item,
        # and _process_content parses the CSV (header only, no rows → no partners created).
        mock_resp = self._mock_http_response(text="name,id\n")
        with patch("requests.Session.request", return_value=mock_resp):
            edi.process_integration()

        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_state, "done")
            self.assertEqual(integration.api_endpoint_id.id, get_endpoint.id)
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.received_content, "received_content (API response) should be logged")

    @mute_logger("odoo.models.unlink")
    def _cleanup_endpoint_test(self, edi, filter_):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.synchronization"].search([("integration_id", "=", edi.id)]).unlink()
            env["res.partner"].search([("name", "ilike", "EDI EP TEST")]).unlink()
            edi.with_env(env).write({"record_filter_id": False})
            edi.with_env(env).unlink()
            filter_.with_env(env).unlink()

    @mute_logger("odoo.models.unlink")
    def _cleanup_endpoint_in_test(self, edi, endpoint):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.synchronization"].search([("integration_id", "=", edi.id)]).unlink()
            edi.with_env(env).unlink()
            endpoint.with_env(env).unlink()

    @mute_logger("odoo.models.unlink")
    def _cleanup_auth_test(self, edi, filter_, partner_pattern):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["edi.synchronization"].search([("integration_id", "=", edi.id)]).unlink()
            env["res.partner"].search([("name", "ilike", partner_pattern)]).unlink()
            edi.with_env(env).write({"record_filter_id": False})
            edi.with_env(env).unlink()
            filter_.with_env(env).unlink()

    def test_endpoint_auth_api_key(self):
        """api_key auth sends Authorization: Bearer <key> header on every request"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "API Key Conn",
                    "type": "api",
                    "api_auth_type": "api_key",
                }
            )
        )
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "ApiKey Endpoint",
                "connection_id": conn.id,
                "method": "POST",
                "url": "https://fake.url/apikey",
                "api_key": "secret123",
            }
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
                "api_endpoint_id": endpoint.id,
                "record_filter_id": filter_.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
            }
        )
        self.new_env["res.partner"].create([{"name": "EDI APIKEY TEST 1"}])
        self.new_cr.commit()
        self.addCleanup(self._cleanup_auth_test, edi, filter_, "EDI APIKEY TEST")

        mock_send = MagicMock(return_value=self._mock_http_response(text='{"ok": true}'))
        with patch("requests.Session.send", mock_send):
            edi.process_integration()

        prep = mock_send.call_args[0][0]
        self.assertEqual(prep.headers.get("Authorization"), "Bearer secret123")

    def test_endpoint_auth_basic(self):
        """basic auth sends Authorization: Basic <base64(user:pass)> header on every request"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Basic Auth Conn",
                    "type": "api",
                    "api_auth_type": "basic",
                }
            )
        )
        endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Basic Endpoint",
                "connection_id": conn.id,
                "method": "POST",
                "url": "https://fake.url/basic",
                "username": "testuser",
                "password": "testpass",
            }
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
                "api_endpoint_id": endpoint.id,
                "record_filter_id": filter_.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
            }
        )
        self.new_env["res.partner"].create([{"name": "EDI BASIC TEST 1"}])
        self.new_cr.commit()
        self.addCleanup(self._cleanup_auth_test, edi, filter_, "EDI BASIC TEST")

        mock_send = MagicMock(return_value=self._mock_http_response(text='{"ok": true}'))
        with patch("requests.Session.send", mock_send):
            edi.process_integration()

        prep = mock_send.call_args[0][0]
        expected = "Basic " + base64.b64encode(b"testuser:testpass").decode()
        self.assertEqual(prep.headers.get("Authorization"), expected)

    def test_endpoint_auth_oauth2_pipeline(self):
        """oauth2 auth fetches a token then uses it as Bearer in the resource call"""
        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "OAuth2 Conn",
                    "type": "api",
                    "api_auth_type": "oauth2",
                }
            )
        )
        self.new_env["edi.endpoint"].create(
            {
                "name": "Token Endpoint",
                "connection_id": conn.id,
                "role": "token",
                "url": "https://fake.url/oauth/token",
                "grant_type": "client_credentials",
                "client_id": "my_client",
                "client_secret": "my_secret",
            }
        )
        resource_endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "OAuth2 Resource Endpoint",
                "connection_id": conn.id,
                "method": "POST",
                "url": "https://fake.url/resource",
            }
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
                "api_endpoint_id": resource_endpoint.id,
                "record_filter_id": filter_.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
            }
        )
        self.new_env["res.partner"].create([{"name": "EDI OAUTH2 TEST 1"}])
        self.new_cr.commit()
        self.addCleanup(self._cleanup_auth_test, edi, filter_, "EDI OAUTH2 TEST")

        token_resp = MagicMock()
        token_resp.raise_for_status.return_value = None
        token_resp.json.return_value = {"access_token": "MY_OAUTH2_TOKEN", "expires_in": 3600}

        mock_send = MagicMock(return_value=self._mock_http_response(text='{"ok": true}'))
        with patch("odoo.addons.edi_base.models.edi_connection.requests.post", return_value=token_resp), patch(
            "requests.Session.send", mock_send
        ):
            edi.process_integration()

        prep = mock_send.call_args[0][0]
        self.assertEqual(prep.headers.get("Authorization"), "Bearer MY_OAUTH2_TOKEN")

    def test_endpoint_in_query_payload(self):
        """_build_in_payload is sent as POST body and logged as sent_content on the sync"""
        post_endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Query Payload Endpoint",
                "connection_id": self.mock_connection.id,
                "method": "POST",
                "url": "https://fake.url/api/query",
            }
        )
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
                "api_endpoint_id": post_endpoint.id,
                "active": False,
            }
        )
        self.new_cr.commit()
        self.addCleanup(self._cleanup_endpoint_in_test, edi, post_endpoint)

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

    def test_endpoint_in_json_split_multi_items(self):
        """_api_wrap_response splitting a response into N items creates N synchronizations"""
        get_endpoint = self.new_env["edi.endpoint"].create(
            {
                "name": "Split Endpoint",
                "connection_id": self.mock_connection.id,
                "method": "GET",
                "url": "https://fake.url/api/items",
            }
        )
        edi = self.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Split Items",
                "type": "api",
                "integration_flow": "in",
                "synchronization_creation": 1,
                "synchronization_content_type": "csv",
                "store_received_content": True,
                "connection_id": self.mock_connection.id,
                "api_endpoint_id": get_endpoint.id,
                "active": False,
            }
        )
        self.new_cr.commit()
        self.addCleanup(self._cleanup_endpoint_in_test, edi, get_endpoint)

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

    @patch("odoo.addons.edi_base.models.edi_connection.requests.post")
    def test_get_token_full_flow(self, mock_post):
        """_api_get_token fetches and caches a token; test() reports authentication success"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "FAKE_TOKEN_123", "expires_in": 3600}
        mock_post.return_value = mock_response

        conn = (
            self.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "OAuth2 Full Flow Conn", "type": "api", "api_auth_type": "oauth2"})
        )
        self.new_env["edi.endpoint"].create(
            {
                "name": "Token Endpoint",
                "connection_id": conn.id,
                "role": "token",
                "url": "https://fake.url/token",
                "grant_type": "password",
                "client_id": "test_client",
                "client_secret": "test_secret",
                "username": "test_user",
                "password": "test_password",
            }
        )
        self.new_cr.commit()

        conn.api_reset_token()
        token = conn._api_get_token()
        self.assertEqual(token, "FAKE_TOKEN_123")
        self.assertEqual(conn.api_token, "FAKE_TOKEN_123")

        with self.assertRaises(UserError) as cm:
            conn.test()
        self.assertIn("Authentication succeeded", str(cm.exception))
