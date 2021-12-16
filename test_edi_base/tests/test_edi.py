import csv
import json

from datetime import timedelta
from pathlib import Path
from psycopg2 import IntegrityError
from unittest import mock

from odoo import api, fields, registry, SUPERUSER_ID
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.edi_base.models.edi_integration import ProcessIntegrationException
from odoo.addons.edi_base.tests.test_edi_common import TestEDICommon, FOLDER_EDI, FOLDER_IN, FOLDER_IN_DONE, FOLDER_IN_ERROR, FOLDER_OUT


FILE_IN = Path(FOLDER_IN,'partner.csv')


@tagged('edi_decorator')
class TestEdiApiCases(TestEDICommon):

    @mute_logger('odoo.addons.edi_base.models.decorator')
    def test_api_decorator(self):
        """ Test Api decorator """

        now = fields.Datetime.now()

        edi = self.env['edi.integration'].with_context(active_test=False).search([
            ('name', '=', 'Create Partner')
        ])
        self.assertFalse(edi)

        name = 'Test partner'

        res_id = self.env['res.partner'].create_partner({'name': name})
        self.assertTrue(res_id)

        partner = self.env['res.partner'].browse(res_id)
        self.assertEqual(partner.display_name, name)

        with registry(self.env.cr.dbname).cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            #Check integration has been created
            edi = new_env['edi.integration'].with_context(active_test=False).search([('name', '=', 'Create Partner')])
            self.assertEqual(edi.last_sync_status, "Success")
            self.assertGreaterEqual(edi.last_success_date, now)
            #Check synchronization object has been created and is in state done
            sync = new_env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, 'done')
            self.assertTrue(sync.content)

    @mute_logger('odoo.addons.edi_base.models.decorator')
    def test_api_decorator_error(self):
        """ Test Api decorator with error """

        self.env.cr._default_log_exceptions = False

        now = fields.Datetime.now()

        with self.assertRaises(IntegrityError):
            self.env['res.partner'].create_partner({'name': False})

            with registry(self.env.cr.dbname).cursor() as new_cr:
                new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
                edi = new_env['edi.integration'].with_context(active_test=False).search([('name', '=', 'Create Partner')])
                self.assertTrue(edi)
                self.assertGreaterEqual(edi.last_failure_date, now)
                self.assertEqual(edi.last_sync_status, "Fail")
                #Check synchronization object has been created and is in state fail
                sync = new_env['edi.synchronization'].search([('integration_id', '=', edi.id), ('synchronization_date', '>=', now)])
                self.assertEqual(len(sync), 1)
                self.assertEqual(sync.state, 'fail')
                self.assertTrue(sync.content)
                self.assertEqual(len(sync.error_ids), 1)

        self.env.cr._default_log_exceptions = True


@tagged('edi_in')
class TestEdiINCases(TestEDICommon):

    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.integration = cls.Integration.create({
            'name': 'Import Partner',
            'type': 'partner_folder_in',
            'integration_flow': 'in',
            'synchronization_content_type': 'csv',
            'connection_id': cls.folder_connection.id,
            'active': False
        })
        cls.new_cr.commit()

    def setUp(self):

        super().setUp()

        FOLDER_IN.mkdir(parents=True, exist_ok=True)

        # NOTE: We clean the filesystem ,reset the integration and remove created
        #       partners between each individual tests
        self.addCleanup(self._clean_fs)
        self.addCleanup(self._clean_partners)

    @mute_logger('odoo.models.unlink')
    def _clean_partners(self):
        self.new_env['res.partner'].search([('name', 'like', 'Partner Test')]).unlink()
        self.new_env.cr.commit()

    def _clean_fs(self):
        FILE_IN.unlink(missing_ok=True)
        Path(FOLDER_IN_DONE, 'partner.csv').unlink(missing_ok=True)
        Path(FOLDER_IN_ERROR, 'partner.csv').unlink(missing_ok=True)
        if FOLDER_IN_DONE.exists():
            FOLDER_IN_DONE.rmdir()
        if FOLDER_IN_ERROR.exists():
            FOLDER_IN_ERROR.rmdir()
        FOLDER_IN.rmdir()
        FOLDER_EDI.rmdir()

    def test_import_partner(self):
        """ Use an integration that import partners from file"""

        now = fields.Datetime.now()

        content = [
            'name,id\n',
            'Partner Test 1,partner_test_1\n',
            'Partner Test 2,partner_test_2\n',
        ]

        with open(FILE_IN, "w") as f:
            f.writelines(content)

        edi = self.integration
        edi.process_integration()

        partners = self.new_env['res.partner'].search([('write_date', '>=', now)])
        self.assertEqual(len(partners), 2, "The integration should create 2 partners")

        self.assertEqual(edi.last_sync_status, "Success", "The integration should have succeed")
        self.assertTrue(edi.last_success_date, "The integration should have the last success date set")
        self.assertGreaterEqual(edi.last_success_date, now, "The integration should be updated after the initial date")

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

        edi = self.integration
        edi.process_integration()

        self.assertEqual(edi.last_sync_status, "Success", "The integration should have succeed")
        self.assertGreaterEqual(edi.last_success_date, now, "The integration should be updated after the initial date")

        partners = self.new_env['res.partner'].search([('write_date', '>=', now)])
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

    @mute_logger('odoo.sql_db')
    def test_import_partner_crash(self):

        now = fields.Datetime.now()

        with open(FILE_IN, "w") as f:
            f.write('raise')

        edi = self.integration
        edi.process_integration()

        self.assertEqual(edi.last_sync_status, "Fail", "The integration should have failed")
        self.assertGreaterEqual(edi.last_failure_date, now, "The integration should be updated after the initial date")

        partners = self.new_env['res.partner'].search([('write_date', '>=', now)])
        self.assertEqual(len(partners), 0, "The integration should create any partners")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            sync = new_env['edi.synchronization'].search([
                ('integration_id', '=', edi.id),
                ('synchronization_date', '>=', now)
            ])

            self.assertEqual(len(sync), 1, "The integration should create 1 synchronization")
            self.assertEqual(sync.state, 'fail', "The synchronization should be in 'fail'")
            self.assertTrue(sync.content, "")
            self.assertEqual(len(sync.error_ids), 1, "The synchronization should have 1 error linked to it")
            self.assertTrue(sync.error_ids.description, "")

    @mute_logger('odoo.sql_db')
    def test_import_partner_crash_raise(self):

        now = fields.Datetime.now()

        with open(FILE_IN, "w") as f:
            f.write('raise')

        edi = self.integration

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

            partners = self.new_env['res.partner'].search([('write_date', '>=', now)])
            self.assertEqual(len(partners), 0, "The integration shouldn't create any partners")

        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

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

        now = fields.Datetime.now()

        edi = self.integration

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

                partners = self.new_env['res.partner'].search([('write_date', '>=', now)])
                self.assertEqual(len(partners), 0, "The integration should create any partners")

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


@tagged('edi_out')
class TestEdiOUTCases(TestEDICommon):

    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.Partner = cls.new_env['res.partner']

        cls.filter = cls.new_env['ir.filters'].create({
            'name': 'Export Partner',
            'model_id': 'res.partner',
            'domain': '[["name", "ilike", "EDI TEST"]]'
        })

        cls.edi = cls.Integration.create({
            'name': 'Export Partner with filter',
            'type': 'partner_folder_out',
            'integration_flow': 'out',
            'synchronization_creation': 'multi',
            'synchronization_content_type': 'csv',
            'connection_id': cls.folder_connection.id,
            'record_filter_id': cls.filter.id,
            'parameter': json.dumps({'fields': ['id', 'name']}),
            'active': False
        })
        cls.edi_one = cls.Integration.create({
            'name': 'Import Partner',
            'type': 'partner_folder_out',
            'integration_flow': 'out',
            'synchronization_creation': 'one',
            'synchronization_content_type': 'csv',
            'connection_id': cls.folder_connection.id,
            'record_filter_id': cls.filter.id,
            'parameter': json.dumps({'fields': ['id', 'name']}),
            'active': False
        })
        # NOTE: The real time tests required to have the external identifier on
        #       the DB.
        cls.new_env['ir.model.data']._update_xmlids([{
            'xml_id': 'test_edi_base.export_partner_filter_integration',
            'record': cls.edi
        }])
        cls.new_env['ir.model.data']._update_xmlids([{
            'xml_id': 'test_edi_base.export_partner_filter_integration_one',
            'record': cls.edi_one
        }])
        cls.new_cr.commit()

        cls.country = cls.env.ref('base.be')

        cls.addClassCleanup(cls._clean_fs, cls)
        cls.addClassCleanup(cls._clean_filters, cls)
        cls.addClassCleanup(cls._clean_xmlids, cls)

    def _clean_xmlids(cls):

        with registry(cls.env.cr.dbname).cursor() as cr:
            api.Environment(
                cr,
                cls.env.user.id,
                cls.env.context
            )['ir.model.data'].search([
                ('model', '=', 'edi.integration'),
                ('module', '=', 'test_edi_base')
            ]).unlink()

    @mute_logger('odoo.models.unlink')
    def _clean_filters(cls):

        with registry(cls.env.cr.dbname).cursor() as cr:
            env = api.Environment(
                cr,
                cls.env.user.id,
                cls.env.context
            )
            # NOTE: We need to unset the filter on the integrations since the `ondelete`
            #       policy is defined as `restrict`, thus raising an error.
            (cls.edi | cls.edi_one).with_env(env).write({'record_filter_id': False})
            env['ir.filters'].with_context(active_test=False).search([
                ('name', '=', 'Export Partner')
            ]).unlink()

    def _clean_fs(cls):
        FOLDER_OUT.rmdir()
        FOLDER_EDI.rmdir()

    def setUp(self):

        super().setUp()

        self.addCleanup(self._clean_files)
        self.addCleanup(self._clean_partners)

    @mute_logger('odoo.models.unlink')
    def _clean_partners(self):
        self.new_env['res.partner'].search([('name', 'like', 'EDI')]).unlink()
        self.new_env.cr.commit()

    def _clean_files(self):
        for f in FOLDER_OUT.iterdir():
            f.unlink(missing_ok=True)

    def test_export_partner(self):

        now = fields.Datetime.now()

        self.Partner.create([{
            'name': f"EDI TEST {str(i).zfill(3)}"
        } for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi.process_integration()

        self.assertEqual(self.edi.last_sync_status, "Success")
        self.assertGreaterEqual(self.edi.last_success_date, now)

        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 1)

        reader = csv.reader(open(filenames[0]), delimiter=',')
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

        self.Partner.create([{
            'name': "EDI TEST %s" % str(i).zfill(3)
        } for i in range(0, 20)])
        self.new_env.cr.commit()

        self.edi_one.process_integration()

        self.assertEqual(self.edi_one.last_sync_status, "Success")
        self.assertGreaterEqual(self.edi_one.last_success_date, now)

        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 20)

        for fname in filenames:
            reader = csv.reader(open(fname), delimiter=',')
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
        self.new_env.cr.commit()

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

    @mute_logger('odoo.sql_db')
    def test_export_partner_crash(self):

        now = fields.Datetime.now()

        self.Partner.create({'name': "EDI TEST raise"})
        self.new_env.cr.commit()

        self.edi.process_integration()

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

    @mute_logger('odoo.sql_db')
    def test_export_partner_crash_raise(self):

        now = fields.Datetime.now()

        self.Partner.create({'name': "EDI TEST raise"})
        self.new_env.cr.commit()

        with self.assertRaises(IntegrityError):
            self.edi.with_context(
                raise_error=True,
                no_exception_log=True
            ).process_integration()

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

    def test_export_partner_real_time(self):

        now = fields.Datetime.now()

        partners = self.Partner.create([{
            'name': "EDI TEST %s" % str(i).zfill(3)
        } for i in range(0, 20)])
        self.new_env.cr.commit()

        partners.sync_real_time()

        #Check value has been properly written by business Code
        self.assertEqual(partners.mapped('country_id').id, self.country.id)

        #Check the synchro went well
        filenames = [fname for fname in FOLDER_OUT.iterdir()]
        self.assertEqual(len(filenames), 1)

        reader = csv.reader(open(filenames[0]), delimiter=',')
        header = reader.__next__()
        for i, line in enumerate(reader):
            data = dict(zip(header, line))
            self.assertEqual(len(data.keys()), 2)
            self.assertEqual(data['name'], "EDI TEST %s" % str(i).zfill(3))

        self.assertEqual(self.edi.last_sync_status, "Success")
        self.assertGreaterEqual(self.edi.last_success_date, now)

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

    def test_export_partner_real_time_error(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({'name': "EDI TEST error"})
        self.new_env.cr.commit()

        partner.sync_real_time()

        self.assertEqual(partner.mapped('country_id').id, self.country.id)

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
            self.assertEqual(len(sync.error_ids), 1)

    @mute_logger('odoo.sql_db')
    def test_export_partner_real_time_crash(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({'name': "EDI TEST raise"})
        self.new_env.cr.commit()

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

    @mute_logger('odoo.sql_db')
    def test_export_partner_real_time_crash_raise(self):

        now = fields.Datetime.now()

        partner = self.Partner.create({'name': "EDI TEST raise"})
        self.new_env.cr.commit()

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


@tagged('edi_base')
class TestEdiBase(TestEDICommon):

    @classmethod
    def setUpClass(cls):

        super().setUpClass()

        cls.integration = cls.Integration.create({
            'name': 'Import Partner',
            'type': 'partner_folder_in',
            'integration_flow': 'in',
            'synchronization_content_type': 'csv',
            'connection_id': cls.folder_connection.id,
            'active': False
        })
        cls.new_env.cr.commit()

    def test_set_status_01(self):
        """
        Test integration's initial status
        """

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, 'No Sync Yet')
        self.assertFalse(self.integration.last_success_date)
        self.assertFalse(self.integration.last_failure_date)

    def test_set_status_02(self):
        """
        Test integration's status after success synchronization
        """

        now = fields.Datetime.now()

        new_cr = registry(self.env.cr.dbname).cursor()
        new_env = api.Environment(
            new_cr,
            self.env.user.id,
            self.env.context
        )

        sync = new_env['edi.synchronization'].create({
            'name': 'Synchronization 1',
            'integration_id': self.integration.id
        })

        sync.write({
            'state': 'done',
            'synchronization_date': now
        })

        sync.flush(fnames=['state', 'synchronization_date'], records=sync)

        new_cr.commit()
        new_cr.close()

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, 'Success')
        self.assertTrue(self.integration.last_success_date)
        self.assertFalse(self.integration.last_failure_date)

    def test_set_status_03(self):
        """
        Test integration's status after fail synchronization
        """

        now = fields.Datetime.now()

        new_cr = registry(self.env.cr.dbname).cursor()
        new_env = api.Environment(
            new_cr,
            self.env.user.id,
            self.env.context
        )

        sync = new_env['edi.synchronization'].create({
            'name': 'Synchronization 1',
            'integration_id': self.integration.id
        })

        sync.write({
            'state': 'fail',
            'synchronization_date': now
        })

        sync.flush(records=sync)

        new_cr.commit()
        new_cr.close()

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, 'Fail')
        self.assertFalse(self.integration.last_success_date)
        self.assertTrue(self.integration.last_failure_date)

    def test_set_status_04(self):
        """
        Test integration's status after new success synchronization
        """

        now = fields.Datetime.now()

        new_cr = registry(self.env.cr.dbname).cursor()
        new_env = api.Environment(
            new_cr,
            self.env.user.id,
            self.env.context
        )

        new_env['edi.synchronization'].create({
            'name': 'Synchronization 1',
            'integration_id': self.integration.id,
            'state': 'fail',
            'synchronization_date': now
        })

        new_cr.commit()

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, 'Fail')
        self.assertFalse(self.integration.last_success_date)
        self.assertTrue(self.integration.last_failure_date)

        sync = new_env['edi.synchronization'].create({
            'name': 'Synchronization 2',
            'integration_id': self.integration.id
        })

        sync.write({
            'state': 'done',
            'synchronization_date': now + timedelta(days=1)
        })

        sync.flush(records=sync)

        new_cr.commit()
        new_cr.close()

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, 'Success')
        self.assertTrue(self.integration.last_success_date)
        self.assertEqual(self.integration.last_failure_date, now)

    def test_set_status_05(self):
        """
        Test integration's status after new fail synchronization
        """

        now = fields.Datetime.now()

        new_cr = registry(self.env.cr.dbname).cursor()
        new_env = api.Environment(new_cr, SUPERUSER_ID, self.env.context)

        new_env['edi.synchronization'].create({
            'name': 'Synchronization 1',
            'integration_id': self.integration.id,
            'state': 'done',
            'synchronization_date': now
        })

        new_cr.commit()

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, 'Success')
        self.assertTrue(self.integration.last_success_date)
        self.assertFalse(self.integration.last_failure_date)

        sync = new_env['edi.synchronization'].create({
            'name': 'Synchronization 2',
            'integration_id': self.integration.id
        })

        sync.write({
            'state': 'fail',
            'synchronization_date': now + timedelta(days=1)
        })

        sync.flush(records=sync)

        new_cr.commit()
        new_cr.close()

        self.integration._set_status()

        self.assertEqual(self.integration.last_sync_status, 'Fail')
        self.assertEqual(self.integration.last_success_date, now)
        self.assertTrue(self.integration.last_failure_date)
