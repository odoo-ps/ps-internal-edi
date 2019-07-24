# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from psycopg2 import IntegrityError

from odoo.tests import TransactionCase
from odoo.tools import mute_logger


class SynchronizationTest(TransactionCase):

    def setUp(self):

        super(SynchronizationTest, self).setUp()

        self.Integration = self.env['edi.integration']
        self.Synchronization = self.env['edi.synchronization']

        self.test_model1_model = self.env['ir.model'].search([
            ('model', '=', 'test.model.1')
        ])
        self.test_model2_model = self.env['ir.model'].search([
            ('model', '=', 'test.model.2')
        ])

        self.connection = self.env['edi.connection'].create({
            'name': 'Test Connection',
            'connection_type': 'test',
            'configuration': '{}'
        })

        self.integration_1 = self.Integration.create({
            'name': 'Test Integration 1',
            'integration_type': 'in',
            'connection_id': self.connection.id,
            'res_model_id': self.test_model1_model.id,
            'provider_name': 'Test Partner'
        })
        self.integration_2 = self.Integration.create({
            'name': 'Test Integration 2',
            'integration_type': 'in',
            'connection_id': self.connection.id,
            'res_model_id': self.test_model2_model.id,
            'provider_name': 'Test Partner'
        })

    @mute_logger('odoo.sql_db')
    def test_create(self):
        """Test synchronizations creation flow"""

        # Check basic creation
        self.Synchronization.with_context(default_integration_id=self.integration_1.id).create({
            'name': 'Test Synchronization 1',
            'integration_id': self.integration_1.id,
        })

        self.Synchronization.with_context(default_integration_id=self.integration_1.id).create({
            'name': 'Test Synchronization 2',
            'integration_id': self.integration_1.id,
        })

        self.Synchronization.with_context(default_integration_id=self.integration_2.id).create({
            'name': 'Test Synchronization 1',
            'integration_id': self.integration_2.id,
        })

        # Check default value for stage_id
        syncs = self.Synchronization.search([])
        self.assertTrue(syncs.mapped('stage_id.state'), ['new'])

        sync = self.Synchronization.create({
            'name': 'Test Synchronization 3',
            'integration_id': self.integration_1.id,
        })
        self.assertFalse(sync.stage_id)

        # Check 'name_integration_id_uniq' constraint
        with self.assertRaises(Exception) as cm:

            self.Synchronization.with_context(default_integration_id=self.integration_1.id).create({
                'name': 'Test Synchronization 1',
                'integration_id': self.integration_1.id,
            })

        exc = cm.exception
        self.assertTrue(isinstance(exc, IntegrityError))
