import csv
import os
import unittest

from psycopg2 import IntegrityError
from unittest import mock

from odoo import api, fields, registry
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from odoo.addons.edi_base.models.edi_integration import ProcessIntegrationException


FILE_IN = "/tmp/edi/in/partner.csv"
FOLDER_OUT = "/tmp/edi/out/"


class TestEdiCases(TransactionCase):

    @mute_logger('odoo.models.unlink')
    def tearDown(self):

        with registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env['edi.synchronization'].search([]).unlink()

        super().tearDown()


@unittest.skip('Needs to be fixed, if somebody uses it...')
class TestEdiApiCases(TestEdiCases):

    def test_api_decorator(self):
        """ Test Api decorator """
        now = fields.Datetime.now()
        name = 'Test partner'
        res = self.env['res.partner'].create_partner({'name': name})
        #Check compute field are ok
        partner = self.env['res.partner'].browse(res)
        self.assertEqual(partner.display_name, name)
        #Check integration has been created
        edi = self.env['edi.integration'].with_context(active_test=False).search([('name', '=', 'Create Partner')])
        self.assertEqual(edi.last_sync_status, "Success")
        self.assertGreaterEqual(edi.last_success_date, now)
        #Check synchronization object has been created and is in state done
        sync = self.env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'done')
        self.assertTrue(sync.content)

    def test_api_decorator_error(self):
        """ Test Api decorator with error """
        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()
        with self.assertRaises(IntegrityError):
            self.env['res.partner'].create_partner({'name': False})
        edi = self.env['edi.integration'].with_context(active_test=False).search([('name', '=', 'Create Partner')])
        self.assertTrue(edi)
        self.assertGreaterEqual(edi.last_failure_date, now)
        self.assertEqual(edi.last_sync_status, "Fail")
        #Check synchronization object has been created and is in state fail
        sync = self.env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'fail')
        self.assertTrue(sync.content)
        self.assertEqual(len(sync.error_ids), 1)
        self.env.cr._default_log_exceptions = True


class TestEdiINCases(TestEdiCases):

    def setUp(self):
        super().setUp()
        if not os.path.isdir("/tmp/edi"):
            os.mkdir("/tmp/edi")
        if not os.path.isdir("/tmp/edi/in"):
            os.mkdir("/tmp/edi/in")

    @mute_logger('odoo.models.unlink')
    def tearDown(self):

        if os.path.isfile(FILE_IN):
            os.remove(FILE_IN)

        super().tearDown()

    def test_import_partner(self):
        """ Use an integration that import partner from file """

        now = fields.Datetime.now()

        content = [
            'name,id\n',
            'Partner Test 1,partner_test_1\n',
            'Partner Test 2,partner_test_2\n',
        ]

        with open(FILE_IN, "w") as f:
            f.writelines(content)

        edi = self.env.ref('test_edi_base.import_partner_integration')
        edi.process_integration()

        self.assertEqual(edi.last_sync_status, "Success", "The integration should have succeed")
        self.assertTrue(edi.last_success_date, "The integration should have the last success date set")
        self.assertGreaterEqual(edi.last_success_date, now, "The integration should be updated after the initial date")

        partner = self.env['res.partner'].search([('write_date', '>=', now)])

        self.assertEqual(len(partner), 2, "The integration should create 2 partners")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, 'done', "The synchronization should be in 'done'")
            self.assertTrue(sync.content, "The synchronization's content should be set")
            self.assertEqual(sync.content, ''.join(content), "The content differs")

    def test_import_partner_report_error(self):
        """ Use an integration that import partner from file with wrong record """

        now = fields.Datetime.now()

        content = [
            'name,id\n',
            ',partner_test_1\n',
            'Partner Test 2,partner_test_2\n',
        ]

        with open(FILE_IN, "w") as f:
            f.writelines(content)

        edi = self.env.ref('test_edi_base.import_partner_integration')
        edi.process_integration()

        self.assertEqual(edi.last_sync_status, "Success", "The integration should have succeed")
        self.assertGreaterEqual(edi.last_success_date, now, "The integration should be updated after the initial date")

        partners = self.env['res.partner'].search([('write_date', '>=', now)])

        self.assertEqual(len(partners), 1, "The integration should create 1 partner")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, 'done', "The synchronization should be in 'done'")
            self.assertTrue(sync.content, "The synchronization's content should be set")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "The synchronization's error description should be set")
            self.assertEqual(
                sync.error_ids.description,
                "No value for field name, name is required \n ['', 'partner_test_1']",
                "The synchronization's error description differ"
            )

    def test_import_partner_crash(self):

        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()

        with open(FILE_IN, "w") as f:
            f.write('raise')

        edi = self.env.ref('test_edi_base.import_partner_integration')
        edi.with_context().process_integration()

        self.assertEqual(edi.last_sync_status, "Fail", "The integration should have failed")
        self.assertGreaterEqual(edi.last_failure_date, now, "The integration should be updated after the initial date")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partner = new_env['res.partner'].search([('write_date', '>=', now)])

            self.assertEqual(len(partner), 0, "The integration should create any partners")

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, 'fail', "The synchronization should be in 'fail'")
            self.assertTrue(sync.content, "")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "")

        self.env.cr._default_log_exceptions = True

    def test_import_partner_crash_raise(self):

        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()

        with open(FILE_IN, "w") as f:
            f.write('raise')

        edi = self.env.ref('test_edi_base.import_partner_integration')

        with self.assertRaises(
            ProcessIntegrationException,
            msg="The integration should raise a ProcessIntegrationException"
        ):
            edi.with_context(
                raise_error=True,
                no_exception_log=True
            ).process_integration()

            self.assertEqual(edi.last_sync_status, "Fail", "The integration should have failed")
            self.assertGreaterEqual(edi.last_failure_date, now, "The integration should be updated after the initial date")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            partner = new_env['res.partner'].search([('write_date', '>=', now)])

            self.assertEqual(len(partner), 0, "The integration should create any partners")

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, 'fail', "The synchronization should be in 'fail'")
            self.assertTrue(sync.content, "")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "")

        self.env.cr._default_log_exceptions = True

    def test_import_partner_crash_raise_get_in_content(self):

        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()

        edi = self.env.ref('test_edi_base.import_partner_integration')

        with mock.patch.object(
            type(edi),
            '_get_in_content',
            side_effect=ProcessIntegrationException('Failed fetching content')
        ):

            with self.assertRaises(
                ProcessIntegrationException,
                msg="The integration should raise a ProcessIntegrationException"
            ):
                edi.with_context(
                    raise_error=True,
                    no_exception_log=True
                ).process_integration()

                self.assertEqual(edi.last_sync_status, "Fail", "The integration should have failed")
                self.assertGreaterEqual(edi.last_failure_date, now, "The integration should be updated after the initial date")

            partner = self.env['res.partner'].search([('write_date', '>=', now)])

            self.assertEqual(len(partner), 0, "The integration should create any partners")

            with registry(self.env.cr.dbname).cursor() as new_cr:

                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

                sync = new_env['edi.synchronization'].search([
                    ('integration_id', '=', edi.id),
                    ('synchronization_date', '>=', now)
                ])

                self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
                self.assertEqual(sync.state, 'fail', "The synchronization should be in 'fail'")
                # NOTE: Here the failure is before fetching any content, so it should
                #       be empty
                self.assertFalse(sync.content, "")
                self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
                self.assertTrue(sync.error_ids.description, "")

        self.env.cr._default_log_exceptions = True


class TestEdiOUTCases(TestEdiCases):

    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.Partner = cls.env['res.partner']
        cls.edi = cls.env.ref('test_edi_base.export_partner_filter_integration')
        cls.edi_one = cls.env.ref('test_edi_base.export_partner_filter_integration_one')
        cls.country = cls.env.ref('base.be')

    @mute_logger('odoo.models.unlink')
    def tearDown(self):

        for f in os.listdir(FOLDER_OUT):
            os.remove('%s/%s' % (FOLDER_OUT, f))

        super().tearDown()

    def test_export_partner(self):

        now = fields.Datetime.now()

        for i in range(0, 20):
            self.Partner.create({'name': "EDI TEST %s" % str(i).zfill(3)})

        self.edi.process_integration()

        self.assertEqual(self.edi.last_sync_status, "Success")
        self.assertGreaterEqual(self.edi.last_success_date, now)

        filenames = os.listdir(FOLDER_OUT)
        self.assertEqual(len(filenames), 1)

        reader = csv.reader(open('%s/%s' % (FOLDER_OUT, filenames[0])), delimiter=',')
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data['name'], "EDI TEST %s" % str(i).zfill(3))

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'done')
            self.assertTrue(sync.content)
            self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_one(self):

        now = fields.Datetime.now()

        for i in range(0, 20):
            self.Partner.create({'name': "EDI TEST %s" % str(i).zfill(3)})

        self.edi_one.process_integration()

        self.assertEqual(self.edi_one.last_sync_status, "Success")
        self.assertGreaterEqual(self.edi_one.last_success_date, now)

        filenames = os.listdir(FOLDER_OUT)
        self.assertEqual(len(filenames), 20)

        for fname in filenames:
            reader = csv.reader(open('%s/%s' % (FOLDER_OUT, fname)), delimiter=',')
            header = reader.__next__()
            for i, line in enumerate(reader):
                data = dict(zip(header, line))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data['name'])

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi_one.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 20)
            for s in sync:
                self.assertEqual(s.state, 'done')
                self.assertTrue(s.content)
                self.assertEqual(len(s.error_ids), 0)

    def test_export_partner_error(self):

        now = fields.Datetime.now()

        self.Partner.create({'name': "EDI TEST error"})

        self.edi.process_integration()

        self.assertGreaterEqual(self.edi.last_success_date, now)
        self.assertEqual(self.edi.last_sync_status, "Success")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'done')
            self.assertTrue(sync.content)
            self.assertEqual(len(sync.error_ids), 1)

    def test_export_partner_crash(self):

        self.env.cr._default_log_exceptions = False

        now = fields.Datetime.now()

        self.Partner.create({'name': "EDI TEST raise"})

        self.edi.process_integration()

        self.assertGreaterEqual(self.edi.last_failure_date, now)
        self.assertEqual(self.edi.last_sync_status, "Fail")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'fail')
            self.assertEqual(len(sync.error_ids), 1)
            self.env.cr._default_log_exceptions = True

    def test_export_partner_crash_raise(self):

        self.env.cr._default_log_exceptions = False

        now = fields.Datetime.now()

        self.Partner.create({'name': "EDI TEST raise"})

        with self.assertRaises(IntegrityError):
            self.edi.with_context(raise_error=True, no_exception_log=True).process_integration()

            self.assertGreaterEqual(self.edi.last_failure_date, now)
            self.assertEqual(self.edi.last_sync_status, "Fail")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'fail')
            self.assertEqual(len(sync.error_ids), 1)
            self.env.cr._default_log_exceptions = True

    def test_export_partner_real_time(self):

        now = fields.Datetime.now()

        partners = self.Partner
        for i in range(0, 20):
            partners |= self.Partner.create({'name': "EDI TEST %s" % str(i).zfill(3)})

        partners.sync_real_time()

        #Check value has been properly written by business Code
        self.assertEqual(partners.mapped('country_id').id, self.country.id)

        #Check the synchro went well
        filenames = os.listdir(FOLDER_OUT)
        self.assertEqual(len(filenames), 1)

        reader = csv.reader(open('%s/%s' % (FOLDER_OUT, filenames[0])), delimiter=',')
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data['name'], "EDI TEST %s" % str(i).zfill(3))

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            edi = new_env.ref('test_edi_base.export_partner_filter_integration')

            self.assertGreaterEqual(edi.last_success_date, now)
            self.assertEqual(edi.last_sync_status, "Success")

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'done')
            self.assertTrue(sync.content)
            self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_real_time_error(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({'name': "EDI TEST error"})

        partner.sync_real_time()

        self.assertEqual(partner.mapped('country_id').id, self.country.id)

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            edi = new_env.ref('test_edi_base.export_partner_filter_integration')

            self.assertGreaterEqual(edi.last_success_date, now)
            self.assertEqual(edi.last_sync_status, "Success")

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'done')
            self.assertEqual(len(sync.error_ids), 1)

    def test_export_partner_real_time_crash(self):

        self.env.cr._default_log_exceptions = False

        now = fields.Datetime.now()

        partner = self.Partner.create({'name': "EDI TEST raise"})

        partner.with_context(no_exception_log=True).sync_real_time()

        self.assertEqual(partner.mapped('country_id').id, self.country.id)

        self.assertEqual(self.edi.last_sync_status, "Fail")
        self.assertGreaterEqual(self.edi.last_failure_date, now)

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'fail')
            self.assertEqual(len(sync.error_ids), 1)

        self.env.cr._default_log_exceptions = True

    def test_export_partner_real_time_crash_raise(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({'name': "EDI TEST raise"})

        with self.assertRaises(IntegrityError):
            partner.with_context(no_exception_log=True).sync_real_time(raise_error=True)

            self.assertEqual(partner.mapped('country_id').id, False)

            self.assertEqual(self.edi.last_sync_status, "Fail")
            self.assertGreaterEqual(self.edi.last_failure_date, now)

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', self.edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'fail')
            self.assertEqual(len(sync.error_ids), 1)
