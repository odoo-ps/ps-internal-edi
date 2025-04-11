import csv
import json
import xmlrpc
from datetime import timedelta
from pathlib import Path
from unittest import mock

from psycopg2 import IntegrityError

from odoo import api, fields
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tests.common import get_db_name, tagged, HttpCase
from odoo.tools import mute_logger

from odoo.addons.edi_base.models.edi_integration import ProcessIntegrationException
from odoo.addons.edi_base.tests.test_edi_common import (
    FOLDER_EDI,
    FOLDER_IN,
    FOLDER_IN_DONE,
    FOLDER_IN_ERROR,
    FOLDER_OUT,
    TestEDICommon,
)


FILE_IN = Path(FOLDER_IN, "partner.csv")


@tagged("edi_decorator")
class TestEdiApiCases(TestEDICommon):
    def setUp(self):
        super().setUp()

        self.addCleanup(self._clean_edi)
        self.addCleanup(self._clean_partners)

    @mute_logger("odoo.models.unlink")
    def _clean_edi(self):
        with Registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = (
                new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
            )
            new_env["edi.synchronization"].search([("integration_id", "in", integration.ids)]).unlink()
            integration.unlink()

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        with Registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env["res.partner"].search([("name", "like", "Test partner")]).unlink()

    @mute_logger("odoo.sql_db")
    def test_api_decorator(self):
        """Test Api decorator"""

        now = fields.Datetime.now()

        edi = self.new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
        self.assertFalse(edi)

        name = "Test partner"

        with mute_logger('odoo.addons.edi_base.models.decorator'):
            res_id = self.new_env["res.partner"].with_context(autocommit=True).create_partner({"name": name})
        self.assertTrue(res_id)

        partner = self.new_env["res.partner"].browse(res_id)
        self.assertEqual(partner.display_name, name)

        with Registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            # Check integration has been created
            edi = new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
            self.assertEqual(edi.last_sync_status, "Success")
            self.assertGreaterEqual(edi.last_success_date, now)

            # Check synchronization object has been created and is in state done
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.content)

    @mute_logger("odoo.sql_db")
    def test_api_decorator_error(self):
        """Test Api decorator with error"""

        now = fields.Datetime.now()

        with self.assertRaises(IntegrityError), mute_logger('odoo.addons.edi_base.models.decorator'):
            self.new_env["res.partner"].with_context(autocommit=True).create_partner({"name": False})

        with Registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            # Check integration has been created
            edi = new_env["edi.integration"].with_context(active_test=False).search([("name", "=", "Create Partner")])
            self.assertTrue(edi)
            self.assertGreaterEqual(edi.last_failure_date, now)
            self.assertEqual(edi.last_sync_status, "Fail")

            # Check synchronization object has been created and is in state fail
            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "fail")
            self.assertTrue(sync.content)
            self.assertEqual(len(sync.error_ids), 1)


@tagged("post_install", "-at_install", "edi_decorator")
class TestEdiApiCasesXMLRPC(HttpCase):

    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.base_args = [get_db_name(), cls.env.ref('base.user_admin').id, 'admin']

    def test_api_decorator_xmlrpc(self):
        """Test Api decorator"""

        args = self.base_args + ['res.partner', 'create_partner']

        with mute_logger('odoo.addons.edi_base.models.decorator'):
            res_id = self.xmlrpc_object.execute(*args, {'name': 'Test partner'})

        self.assertTrue(res_id)

        partner = self.env["res.partner"].browse(res_id)
        self.assertEqual(partner.display_name, 'Test partner')

    @mute_logger("odoo.sql_db", "odoo.http")
    def test_api_decorator_error_xmlrpc(self):
        """Test Api decorator with error"""

        args = self.base_args + ['res.partner', 'create_partner']

        with (
            mute_logger('odoo.addons.edi_base.models.decorator'),
            self.assertRaises(xmlrpc.client.Fault) as cm
        ):
            self.xmlrpc_object.execute(*args, {'name': False})

        self.assertIn('Contacts require a name', cm.exception.faultString)


@tagged("edi_in")
class TestEdiINCases(TestEDICommon):
    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.integration = cls.Integration.with_context(autocommit=True, no_exception_log=True).create(
            {
                "name": "Import Partner",
                "type": "api",
                "integration_flow": "in",
                "synchronization_content_type": "csv",
                "connection_id": cls.folder_connection.id,
                "active": False,
            }
        )
        cls.new_cr.commit()

    def setUp(self):

        super().setUp()

        FOLDER_IN.mkdir(parents=True, exist_ok=True)

        # NOTE: We clean the filesystem ,reset the integration and remove created
        #       partners between each individual tests
        self.addCleanup(self._clean_fs)
        self.addCleanup(self._clean_partners)

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        with Registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env["res.partner"].search([("name", "like", "Partner Test")]).unlink()

    def _clean_fs(self):
        FILE_IN.unlink(missing_ok=True)
        Path(FOLDER_IN_DONE, "partner.csv").unlink(missing_ok=True)
        Path(FOLDER_IN_ERROR, "partner.csv").unlink(missing_ok=True)
        if FOLDER_IN_DONE.exists():
            FOLDER_IN_DONE.rmdir()
        if FOLDER_IN_ERROR.exists():
            FOLDER_IN_ERROR.rmdir()
        FOLDER_IN.rmdir()
        FOLDER_EDI.rmdir()

    def test_import_partner(self):
        """Use an integration that import partners from file"""

        now = fields.Datetime.now()

        content = ["name,id\n", "Partner Test 1,partner_test_1\n", "Partner Test 2,partner_test_2\n"]

        with open(FILE_IN, "w") as f:
            f.writelines(content)

        edi = self.integration
        edi.process_integration()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 2, "The integration should create 2 partners")

            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_sync_status, "Success", "The integration should have succeed")
            self.assertTrue(integration.last_success_date, "The integration should have the last success date set")
            self.assertGreaterEqual(
                integration.last_success_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertTrue(sync.content, "The synchronization's content should be set")
            self.assertEqual(sync.content, "".join(content), "The content differs")

    def test_import_partner_report_error(self):
        """Use an integration that import partner from file with wrong record"""

        now = fields.Datetime.now()

        content = ["name,id\n", ",partner_test_1\n", "Partner Test 2,partner_test_2\n"]

        with open(FILE_IN, "w") as f:
            f.writelines(content)

        edi = self.integration
        edi.process_integration()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 1, "The integration should create 1 partner")

            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_sync_status, "Success", "The integration should have succeed")
            self.assertGreaterEqual(
                integration.last_success_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "done", "The synchronization should be in 'done'")
            self.assertTrue(sync.content, "The synchronization's content should be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "The synchronization's error description should be set")
            self.assertEqual(
                sync.error_ids.description,
                "No value for field name, name is required \n ['', 'partner_test_1']",
                "The synchronization's error description differ",
            )

    @mute_logger("odoo.sql_db")
    def test_import_partner_crash(self):

        now = fields.Datetime.now()

        with open(FILE_IN, "w") as f:
            f.write("raise")

        edi = self.integration
        edi.process_integration()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 0, "The integration should create any partners")

            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_sync_status, "Fail", "The integration should have failed")
            self.assertGreaterEqual(
                integration.last_failure_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
            self.assertTrue(sync.content, "")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "")

    @mute_logger("odoo.sql_db")
    def test_import_partner_crash_raise(self):

        now = fields.Datetime.now()

        with open(FILE_IN, "w") as f:
            f.write("raise")

        edi = self.integration

        with self.assertRaises(Exception, msg="The integration should raise an Exception"):
            edi.with_context(edi_raise_error=True).process_integration()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].search([("write_date", ">=", now)])
            self.assertEqual(len(partners), 0, "The integration shouldn't create any partners")

            integration = new_env["edi.integration"].browse(edi.id)
            self.assertEqual(integration.last_sync_status, "Fail", "The integration should have failed")
            self.assertGreaterEqual(
                integration.last_failure_date, now, "The integration should be updated after the initial date"
            )

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
            self.assertTrue(sync.content, "")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "")

        self.env.cr._default_log_exceptions = True

    def test_import_partner_crash_raise_get_in_content(self):

        now = fields.Datetime.now()

        edi = self.integration

        with mock.patch.object(
            type(edi), "_get_in_content", side_effect=ProcessIntegrationException("Failed fetching content")
        ):

            with self.assertRaises(UserError, msg="The integration should raise a UserError"):
                edi.with_context(edi_raise_error=True).process_integration()

            with Registry(self.env.cr.dbname).cursor() as new_cr:

                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

                partners = new_env["res.partner"].search([("write_date", ">=", now)])
                self.assertEqual(len(partners), 0, "The integration should create any partners")

                integration = new_env["edi.integration"].browse(edi.id)
                self.assertEqual(integration.last_sync_status, "Fail", "The integration should have failed")
                self.assertGreaterEqual(
                    integration.last_failure_date, now, "The integration should be updated after the initial date"
                )

                sync = new_env["edi.synchronization"].search(
                    [("integration_id", "=", edi.id), ("synchronization_date", ">=", now)]
                )

                self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
                self.assertEqual(sync.state, "fail", "The synchronization should be in 'fail'")
                # NOTE: Here the failure is before fetching any content, so it should
                #       be empty
                self.assertFalse(sync.content, "")
                self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
                self.assertTrue(sync.error_ids.description, "")


@tagged("edi_out")
class TestEdiOUTCases(TestEDICommon):
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
                "connection_id": cls.folder_connection.id,
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
                "connection_id": cls.folder_connection.id,
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
                "connection_id": cls.folder_connection.id,
                "record_filter_id": cls.filter.id,
                "parameter": json.dumps({"fields": ["id", "name"]}),
                "active": False,
            }
        )
        cls.new_cr.commit()

        cls.country = cls.env.ref("base.be")

        cls.addClassCleanup(cls._clean_fs, cls)
        cls.addClassCleanup(cls._clean_filters, cls)

    @mute_logger("odoo.models.unlink")
    def _clean_filters(self):

        with Registry(self.env.cr.dbname).cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            # NOTE: We need to unset the filter on the integrations since the `ondelete`
            #       policy is defined as `restrict`, thus raising an error.
            integrations = self.edi | self.edi_one | self.edi_multi
            filters = integrations.record_filter_id
            integrations.with_env(env).write({"record_filter_id": False})
            filters.with_env(env).unlink()

    def _clean_fs(self):
        FOLDER_OUT.rmdir()
        FOLDER_EDI.rmdir()

    def setUp(self):

        super().setUp()

        self.addCleanup(self._clean_files)
        self.addCleanup(self._clean_partners)

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        self.new_env["res.partner"].search([("name", "like", "EDI TEST")]).unlink()
        self.new_env.cr.commit()

    def _clean_files(self):
        for f in FOLDER_OUT.iterdir():
            f.unlink(missing_ok=True)

    def test_export_partner(self):

        now = fields.Datetime.now()

        self.Partner.create([{"name": f"EDI TEST {str(i).zfill(3)}"} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi.process_integration()

        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 1)

        reader = csv.reader(open(filenames[0]), delimiter=",")
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data["name"], "EDI TEST %s" % str(i).zfill(3))

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_sync_status, "Success")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.content)
            self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_one(self):

        now = fields.Datetime.now()

        self.Partner.create([{"name": "EDI TEST %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi_one.process_integration()

        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 20)

        for fname in filenames:
            reader = csv.reader(open(fname), delimiter=",")
            header = reader.__next__()
            for line in reader:
                data = dict(zip(header, line))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_sync_status, "Success")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_one.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 20)
            for s in sync:
                self.assertEqual(s.state, "done")
                self.assertTrue(s.content)
                self.assertEqual(len(s.error_ids), 0)

    def test_export_partner_multi(self):

        now = fields.Datetime.now()

        self.Partner.create([{"name": "EDI TEST %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi_multi.process_integration()

        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 7)

        result = {2: 1, 3: 6}
        for fname in filenames:
            reader = csv.reader(open(fname), delimiter=",")
            header = reader.__next__()
            lines = list(reader)
            remaining_files = result.get(len(lines), 0)
            self.assertGreaterEqual(remaining_files, 1)
            result[len(lines)] -= 1
            for line in lines:
                data = dict(zip(header, line))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])
        for value in result.values():
            self.assertEqual(value, 0)

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi_multi.id)
            self.assertEqual(integration.last_sync_status, "Success")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_multi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 7)
            for s in sync:
                self.assertEqual(s.state, "done")
                self.assertTrue(s.content)
                self.assertEqual(len(s.error_ids), 0)

    def test_export_partner_error(self):

        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST error"})
        self.new_env.cr.commit()

        self.edi.process_integration()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertGreaterEqual(integration.last_success_date, now)
            self.assertEqual(integration.last_sync_status, "Success")

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.content)
            self.assertEqual(len(sync.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_export_partner_crash(self):

        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST raise"})
        self.new_env.cr.commit()

        self.edi.process_integration()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_sync_status, "Fail")
            self.assertGreaterEqual(integration.last_failure_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "fail")
            self.assertEqual(len(sync.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_export_partner_crash_raise(self):

        now = fields.Datetime.now()

        self.Partner.create({"name": "EDI TEST raise"})
        self.new_env.cr.commit()

        with self.assertRaises(UserError):
            self.edi.with_context(edi_raise_error=True).process_integration()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_sync_status, "Fail")
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

        # Check value has been properly written by business Code
        self.assertEqual(partners.mapped("country_id").id, self.country.id)

        # Check the synchro went well
        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 1)

        reader = csv.reader(open(filenames[0]), delimiter=",")
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data["name"], "EDI TEST %s" % str(i).zfill(3))

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_sync_status, "Success")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertTrue(sync.content)
            self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_real_time_error(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({"name": "EDI TEST error"})
        self.new_env.cr.commit()

        partner.write({"country_id": self.env.ref("base.be").id})
        self.edi.with_context(autocommit=True)._process_realtime(data=partner)

        self.assertEqual(partner.mapped("country_id").id, self.country.id)

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertGreaterEqual(integration.last_success_date, now)
            self.assertEqual(integration.last_sync_status, "Success")

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

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_sync_status, "Fail")
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

        with self.assertRaises(UserError):
            partner.write({"country_id": self.env.ref("base.be").id})
            self.edi.with_context(autocommit=True)._process_realtime(data=partner, raise_error=True)

        # Check value has been properly written by business Code (the current cursor is not rolledback)
        self.assertEqual(partner.mapped("country_id").id, self.country.id)

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            integration = new_env["edi.integration"].browse(self.edi.id)
            self.assertEqual(integration.last_sync_status, "Fail")
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
        Test a particular case when an error occurs after the realtime call
        Then a potential data inconsistency may appear since the synchronizations
        have been set as success but the sent data are rolledback
        The synchronizations should then been in error to alert of a potential inconsitency.
        """

        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": "EDI TEST ERROR %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        with self.assertRaises(ValueError):
            with Registry(self.env.cr.dbname).cursor() as new_cr:

                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

                partners = new_env["res.partner"].browse(partners.ids)
                for partner in partners:
                    partner.name = partner.name + " UPDATED"

                integration = new_env["edi.integration"].browse(self.edi_one.id)
                integration.with_context(autocommit=True)._process_realtime(data=partners)

                # simulate an error after the realtime
                raise ValueError("Error after the real time integration")

        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 20)

        for fname in filenames:
            reader = csv.reader(open(fname), delimiter=",")
            header = reader.__next__()
            for line in reader:
                data = dict(zip(header, line))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])
                self.assertTrue("UPDATED" in data["name"])  # updated data have been synchronized

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].browse(partners.ids)
            for partner in partners:
                self.assertTrue("EDI TEST" in partner.name)
                self.assertTrue("UPDATED" not in partner.name)  # data have been rolledback

            # since their is an inconsitency, integration and synchronizations should be in error
            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_sync_status, "Fail")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_one.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 20)
            for s in sync:
                self.assertEqual(s.state, "fail")
                self.assertTrue(s.content)
                self.assertEqual(len(s.error_ids), 1)

    @mute_logger("odoo.sql_db")
    def test_error_after_commit_realtime(self):
        """Test realtime integration
        Test a particular case when an error occurs after the realtime call and commit on the data cursor
        Since the data cursor has been committed before the error occurs, no inconsistency should be
        detected since the synchronized data are the one stored in the Odoo database.
        The synchronizations should then been in success.
        """

        now = fields.Datetime.now()

        partners = self.Partner.create([{"name": "EDI TEST SUCCESS %s" % str(i).zfill(3)} for i in range(0, 20)])
        self.new_env.cr.commit()

        with self.assertRaises(ValueError):
            with Registry(self.env.cr.dbname).cursor() as new_cr:

                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

                partners = new_env["res.partner"].browse(partners.ids)
                for partner in partners:
                    partner.name = partner.name + " UPDATED"

                integration = new_env["edi.integration"].browse(self.edi_one.id)
                integration.with_context(autocommit=True)._process_realtime(data=partners)

                new_env.cr.commit()  # data are committed

                # simulate an error after the realtime and commit
                raise ValueError("Error after the real time integration")

        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 20)

        for fname in filenames:
            reader = csv.reader(open(fname), delimiter=",")
            header = reader.__next__()
            for line in reader:
                data = dict(zip(header, line))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data["name"])
                self.assertTrue("UPDATED" in data["name"])  # updated data have been synchronized

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partners = new_env["res.partner"].browse(partners.ids)
            for partner in partners:
                self.assertTrue("EDI TEST" in partner.name)
                self.assertTrue("UPDATED" in partner.name)  # data have not been rolledback

            # since their is no inconsitency, integration and synchronizations should be in success
            integration = new_env["edi.integration"].browse(self.edi_one.id)
            self.assertEqual(integration.last_sync_status, "Success")
            self.assertGreaterEqual(integration.last_success_date, now)

            sync = new_env["edi.synchronization"].search(
                [("integration_id", "=", self.edi_one.id), ("synchronization_date", ">=", now)]
            )

            self.assertEqual(len(sync), 20)
            for s in sync:
                self.assertEqual(s.state, "done")
                self.assertTrue(s.content)
                self.assertEqual(len(s.error_ids), 0)


@tagged("edi_base")
class TestEdiBase(TestEDICommon):
    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.integration = cls.Integration.create(
            {
                "name": "Import Partner",
                "type": "api",
                "integration_flow": "in",
                "synchronization_content_type": "csv",
                "connection_id": cls.folder_connection.id,
                "active": False,
            }
        )
        cls.new_env.cr.commit()

    def test_set_status_01(self):
        """
        Test integration's initial status
        """

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, "No Sync Yet")
        self.assertFalse(self.integration.last_success_date)
        self.assertFalse(self.integration.last_failure_date)

    def test_set_status_02(self):
        """
        Test integration's status after success synchronization
        """

        now = fields.Datetime.now()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 1", "integration_id": self.integration.id}
            )

            sync.write({"state": "done", "synchronization_date": now})

            sync.flush_recordset(fnames=["state", "synchronization_date"])

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status()

            self.assertEqual(integration.last_sync_status, "Success")
            self.assertTrue(integration.last_success_date)
            self.assertFalse(integration.last_failure_date)

    def test_set_status_03(self):
        """
        Test integration's status after fail synchronization
        """

        now = fields.Datetime.now()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 1", "integration_id": self.integration.id}
            )

            sync.write({"state": "fail", "synchronization_date": now})

            sync.flush_recordset()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status()

            self.assertEqual(integration.last_sync_status, "Fail")
            self.assertFalse(integration.last_success_date)
            self.assertTrue(integration.last_failure_date)

    def test_set_status_04(self):
        """
        Test integration's status after new success synchronization
        """

        now = fields.Datetime.now()

        with Registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env["edi.synchronization"].create(
                {
                    "name": "Synchronization 1",
                    "integration_id": self.integration.id,
                    "state": "fail",
                    "synchronization_date": now,
                }
            )
            new_cr.commit()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status()

            self.assertEqual(integration.last_sync_status, "Fail")
            self.assertFalse(integration.last_success_date)
            self.assertTrue(integration.last_failure_date)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 2", "integration_id": self.integration.id}
            )
            sync.write({"state": "done", "synchronization_date": now + timedelta(days=1)})
            sync.flush_recordset()
            integration._set_status()

            self.assertEqual(integration.last_sync_status, "Success")
            self.assertTrue(integration.last_success_date)
            self.assertEqual(integration.last_failure_date, now)

    def test_set_status_05(self):
        """
        Test integration's status after new fail synchronization
        """

        now = fields.Datetime.now()

        with Registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            new_env["edi.synchronization"].create(
                {
                    "name": "Synchronization 1",
                    "integration_id": self.integration.id,
                    "state": "done",
                    "synchronization_date": now,
                }
            )

            new_cr.commit()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status()

            self.assertEqual(integration.last_sync_status, "Success")
            self.assertTrue(integration.last_success_date)
            self.assertFalse(integration.last_failure_date)

            sync = new_env["edi.synchronization"].create(
                {"name": "Synchronization 2", "integration_id": self.integration.id}
            )

            sync.write({"state": "fail", "synchronization_date": now + timedelta(days=1)})

            sync.flush_recordset()

            integration = new_env["edi.integration"].browse(self.integration.id)
            integration._set_status()

            self.assertEqual(integration.last_sync_status, "Fail")
            self.assertEqual(integration.last_success_date, now)
            self.assertTrue(integration.last_failure_date)
