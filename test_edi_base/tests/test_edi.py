import csv
import os

from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests.common import TransactionCase


FILE_IN = "/tmp/edi/in/partner.csv"
FOLDER_OUT = "/tmp/edi/out/"


class TestEdiCases(TransactionCase):
    def tearDown(self):
        super().tearDown()
        self.env['edi.synchronization'].search([]).unlink()
        self.env['edi.integration'].with_context(active_test=False).search([]).set_status()
        self.env.cr.commit()


class TestEdiApiCases(TestEdiCases):

    def tearDown(self):
        self.env['res.partner'].search([('name', 'ilike', 'Test partner')]).unlink()
        super().tearDown()

    def test_api_decorator(self):
        """ Test Api decorator """
        now = fields.Datetime.now()
        name = 'Test partner'
        res = self.env['res.partner'].create_partner({'name': name})
        #Commit to start a new transaction
        #in order to see the sync create in another transaction
        self.env.cr.commit()
        #Check compute field are ok
        partner = self.env['res.partner'].browse(res)
        self.assertEqual(partner.display_name, name)
        #Check integration has been created
        edi = self.env['edi.integration'].with_context(active_test=False).search([('name', '=', 'Create Partner')])
        self.assertTrue(edi)
        self.assertGreaterEqual(edi.last_success_date, now)
        self.assertEqual(edi.last_sync_status, "Success")
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
        #Need to rollback() has then end of transaction would do
        #To have a working cursor again
        self.env.cr.rollback()
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

    def tearDown(self):
        self.env['res.partner'].search([('name', 'ilike', 'Partner Test')]).unlink()
        super().tearDown()
        if os.path.isfile(FILE_IN):
            os.remove(FILE_IN)

    def test_import_partner(self):
        """ Use an integration that import partner from file """

        now = fields.Datetime.now()
        with open(FILE_IN, "w") as f:
            f.writelines(['name,id\n',
                          'Partner Test 1,partner_test_1\n',
                          'Partner Test 2,partner_test_2\n',
            ])
        edi = self.env.ref('test_edi_base.import_partner_integration')
        edi._process(edi.id)
        self.env.cr.commit()
        partner = self.env['res.partner'].search([('write_date', '>=', now)])
        self.assertEqual(len(partner), 2)
        self.assertTrue(partner.mapped('display_name'), partner.mapped('name'))

        self.assertGreaterEqual(edi.last_success_date, now)
        self.assertEqual(edi.last_sync_status, "Success")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'done')
        self.assertTrue(sync.content)

    def test_import_partner_report_error(self):
        """ Use an integration that import partner from file with wrong record """
        now = fields.Datetime.now()
        with open(FILE_IN, "w") as f:
            f.writelines(['name,id\n',
                          ',partner_test_1\n',
                          'Partner Test 2,partner_test_2\n',
            ])
        edi = self.env.ref('test_edi_base.import_partner_integration')
        edi._process(edi.id)
        self.env.cr.commit()
        partner = self.env['res.partner'].search([('write_date', '>=', now)])
        self.assertEqual(len(partner), 1)
        self.assertTrue(partner.mapped('display_name'), partner.mapped('name'))
        self.assertGreaterEqual(edi.last_success_date, now)
        self.assertEqual(edi.last_sync_status, "Success")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'done')
        self.assertTrue(sync.content)
        self.assertEqual(len(sync.error_ids), 1)
        self.assertTrue(sync.error_ids.description)

    def test_import_partner_crash(self):
        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()
        with open(FILE_IN, "w") as f:
            f.write('raise')

        edi = self.env.ref('test_edi_base.import_partner_integration')
        edi._process(edi.id)
        self.env.cr.rollback()
        partner = self.env['res.partner'].search([('write_date', '>=', now)])
        self.assertEqual(len(partner), 0)
        self.assertGreaterEqual(edi.last_failure_date, now)
        self.assertEqual(edi.last_sync_status, "Fail")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'fail')
        self.assertTrue(sync.content)
        self.assertEqual(len(sync.error_ids), 1)
        self.assertTrue(sync.error_ids.description)
        self.env.cr._default_log_exceptions = True

    def test_import_partner_crash_raise(self):
        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()
        with open(FILE_IN, "w") as f:
            f.write('raise')
        edi = self.env.ref('test_edi_base.import_partner_integration')
        with self.assertRaises(IntegrityError):
            edi.with_context(raise_error=True, no_exception_log=True)._process(edi.id)
        self.env.cr.rollback()
        partner = self.env['res.partner'].search([('write_date', '>=', now)])
        self.assertEqual(len(partner), 0)
        self.assertGreaterEqual(edi.last_failure_date, now)
        self.assertEqual(edi.last_sync_status, "Fail")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'fail')
        self.assertTrue(sync.content)
        self.assertEqual(len(sync.error_ids), 1)
        self.assertTrue(sync.error_ids.description)
        self.env.cr._default_log_exceptions = True


class TestEdiOUTCases(TestEdiCases):

    def setUp(self):
        super().setUp()
        self.country = self.env.ref('base.be')
        self.edi = self.env.ref('test_edi_base.export_partner_filter_integration')
        self.edi_one = self.env.ref('test_edi_base.export_partner_filter_integration_one')


    def tearDown(self):
        super().tearDown()
        self.env['res.partner'].search([('name', 'ilike', 'EDI TEST')]).unlink()
        self.env.cr.commit()
        for f in os.listdir(FOLDER_OUT):
            os.remove('%s/%s' % (FOLDER_OUT, f))

    def test_export_partner(self):
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        for i in range(0, 20):
            partner.create({'name': "EDI TEST %s" % str(i).zfill(3)})
        self.edi._process(self.edi.id)
        self.env.cr.commit()
        file_name = os.listdir(FOLDER_OUT)
        self.assertEqual(len(file_name), 1)

        reader = csv.reader(open('%s/%s' % (FOLDER_OUT, file_name[0])), delimiter=',')
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data['name'], "EDI TEST %s" % str(i).zfill(3))

        self.assertGreaterEqual(self.edi.last_success_date, now)
        self.assertEqual(self.edi.last_sync_status, "Success")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'done')
        self.assertTrue(sync.content)
        self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_one(self):
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        for i in range(0, 20):
            partner.create({'name': "EDI TEST %s" % str(i).zfill(3)})
        self.edi_one._process(self.edi_one.id)
        self.env.cr.commit()
        file_name = os.listdir(FOLDER_OUT)
        self.assertEqual(len(file_name), 20)

        for fname in file_name:
            reader = csv.reader(open('%s/%s' % (FOLDER_OUT, fname)), delimiter=',')
            header = reader.__next__()
            for i, line in enumerate(reader):
                data = dict(zip(header, line))
                self.assertEqual(len(data.keys()), 2)
                self.assertTrue("EDI TEST" in data['name'])

        self.assertGreaterEqual(self.edi_one.last_success_date, now)
        self.assertEqual(self.edi_one.last_sync_status, "Success")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi_one.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 20)
        for s in sync:
            self.assertEqual(s.state, 'done')
            self.assertTrue(s.content)
            self.assertEqual(len(s.error_ids), 0)

    def test_export_partner_error(self):
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        partner.create({'name': "EDI TEST error"})
        self.edi._process(self.edi.id)
        self.env.cr.commit()

        self.assertGreaterEqual(self.edi.last_success_date, now)
        self.assertEqual(self.edi.last_sync_status, "Success")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'done')
        self.assertTrue(sync.content)
        self.assertEqual(len(sync.error_ids), 1)

    def test_export_partner_crash(self):
        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        partner.create({'name': "EDI TEST raise"})
        self.edi._process(self.edi.id)
        self.env.cr.rollback()

        self.assertGreaterEqual(self.edi.last_failure_date, now)
        self.assertEqual(self.edi.last_sync_status, "Fail")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'fail')
        self.assertEqual(len(sync.error_ids), 1)
        self.env.cr._default_log_exceptions = True

    def test_export_partner_crash_raise(self):
        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        partner.create({'name': "EDI TEST raise"})
        with self.assertRaises(IntegrityError):
            self.edi.with_context(raise_error=True, no_exception_log=True)._process(self.edi.id)
        self.env.cr.rollback()

        self.assertGreaterEqual(self.edi.last_failure_date, now)
        self.assertEqual(self.edi.last_sync_status, "Fail")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id), ('synchronization_date', '>=', now)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'fail')
        self.assertEqual(len(sync.error_ids), 1)
        self.env.cr._default_log_exceptions = True

    def test_export_partner_real_time(self):
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        for i in range(0, 20):
            partner |= partner.create({'name': "EDI TEST %s" % str(i).zfill(3)})
        partner.sync_real_time()
        self.env.cr.commit()
        #Check value has been properly written by business Code
        self.assertEqual(partner.mapped('country_id').id, self.country.id)
        #Check the synchro went well
        file_name = os.listdir(FOLDER_OUT)
        self.assertEqual(len(file_name), 1)

        reader = csv.reader(open('%s/%s' % (FOLDER_OUT, file_name[0])), delimiter=',')
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data['name'], "EDI TEST %s" % str(i).zfill(3))

        self.assertGreaterEqual(self.edi.last_success_date, now)
        self.assertEqual(self.edi.last_sync_status, "Success")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'done')
        self.assertTrue(sync.content)
        self.assertEqual(len(sync.error_ids), 0)

    def test_export_partner_real_time_error(self):
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        partner = partner.create({'name': "EDI TEST error"})
        partner.sync_real_time()
        self.env.cr.commit()

        self.assertEqual(partner.mapped('country_id').id, self.country.id)

        self.assertGreaterEqual(self.edi.last_success_date, now)
        self.assertEqual(self.edi.last_sync_status, "Success")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'done')
        self.assertEqual(len(sync.error_ids), 1)

    def test_export_partner_real_time_crash(self):
        self.env.cr._default_log_exceptions = False
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        partner = partner.create({'name': "EDI TEST raise"})
        partner.with_context(no_exception_log=True).sync_real_time()
        self.env.cr.commit()
        self.assertEqual(partner.mapped('country_id').id, self.country.id)


        self.assertGreaterEqual(self.edi.last_failure_date, now)
        self.assertEqual(self.edi.last_sync_status, "Fail")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'fail')
        self.assertEqual(len(sync.error_ids), 1)
        self.env.cr._default_log_exceptions = True

    def test_export_partner_real_time_crash_raise(self):
        now = fields.Datetime.now()
        partner = self.env['res.partner']
        partner = partner.create({'name': "EDI TEST raise"})
        self.env.cr.commit()
        with self.assertRaises(IntegrityError):
            partner.with_context(no_exception_log=True).sync_real_time(raise_error=True)
        self.env.cr.rollback()
        partner.invalidate_cache()
        self.assertEqual(partner.mapped('country_id').id, False)


        self.assertGreaterEqual(self.edi.last_failure_date, now)
        self.assertEqual(self.edi.last_sync_status, "Fail")

        sync = self.env['edi.synchronization'].search([('integration_id', '=', self.edi.id)])
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync.state, 'fail')
        self.assertEqual(len(sync.error_ids), 1)
