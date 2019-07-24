# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json

from datetime import datetime as dt

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class IntegrationTest(TransactionCase):

    def setUp(self):

        super(IntegrationTest, self).setUp()

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

        now = dt.utcnow()

        # Outgoing stuff
        self.out_integration_1 = self.Integration.create({
            'name': 'Test Incoming Integration 1',
            'integration_type': 'out',
            'connection_id': self.connection.id,
            'res_model_id': self.test_model1_model.id,
            'provider_name': 'Test Partner'
        })
        self.out_integration_2 = self.Integration.create({
            'name': 'Test Incoming Integration 2',
            'integration_type': 'out',
            'connection_id': self.connection.id,
            'res_model_id': self.test_model2_model.id,
            'provider_name': 'Test Partner'
        })
        self.out_synchronization_1 = self.Synchronization.create({
            'name': '%s_%s_%s_%s_integration_%s_synchronization' % (
                self.out_integration_1.res_model_id.model.replace('.', '_'),
                self.out_integration_1.synchronization_content_type,
                self.out_integration_1.integration_type,
                now.strftime('%s'),
                self.out_integration_1.id
            ),
            'integration_id': self.out_integration_1.id,
            'content': json.dumps([])
        })
        self.out_synchronization_2 = self.Synchronization.create({
            'name': '%s_%s_%s_%s_integration_%s_synchronization' % (
                self.out_integration_2.res_model_id.model.replace('.', '_'),
                self.out_integration_2.synchronization_content_type,
                self.out_integration_2.integration_type,
                now.strftime('%s'),
                self.out_integration_2.id
            ),
            'integration_id': self.out_integration_2.id,
            'content': json.dumps([])
        })

        # Incoming stuff
        self.in_synchronization_1 = {
            'filename': 'test_model_1_file1.txt',
            'content_type': 'text',
            'content': "fieldA1_1\tfieldB1_1\tfieldC1_1\tfieldD1_1\nfieldA1_2\tfieldB1_2\tfieldC1_2\tfieldD1_2\n"""
        }
        self.in_synchronization_2 = {
            'filename': 'test_model_1_file2.txt',
            'content_type': 'text',
            'content': "fieldA2_1\tfieldB2_1\tfieldC2_1\tfieldD2_1\nfieldA2_2\tfieldB2_2\tfieldC2_2\tfieldD2_2\n"""
        }
        self.in_model1_synchronizations = [self.in_synchronization_1, self.in_synchronization_2]

        self.in_synchronization_3 = {
            'filename': 'test_model_2_file1.txt',
            'content_type': 'text',
            'content': "fieldA1_1\tfieldB1_1\tfieldC1_1\tfieldD1_1\nfieldA1_2\tfieldB1_2\tfieldC1_2\tfieldD1_2\n"""
        }
        self.in_synchronization_4 = {
            'filename': 'test_model_2_file2.txt',
            'content_type': 'text',
            'content': "fieldA2_1\tfieldB2_1\tfieldC2_1\tfieldD2_1\nfieldA2_2\tfieldB2_2\tfieldC2_2\tfieldD2_2\n"""
        }
        self.in_model2_synchronizations = [self.in_synchronization_3, self.in_synchronization_4]

        self.in_synchronizations = [
            self.in_synchronization_1,
            self.in_synchronization_2,
            self.in_synchronization_3,
            self.in_synchronization_4
        ]

        def do_fetch_synchronizations(self, *args, **kwargs):
            result = filter(lambda s: self._is_valid_filename(s['filename']), [
                {
                    'filename': 'test_model_1_file1.txt',
                    'content_type': 'text',
                    'content': "fieldA1_1\tfieldB1_1\tfieldC1_1\tfieldD1_1\nfieldA1_2\tfieldB1_2\tfieldC1_2\tfieldD1_2\n"""
                }, {
                    'filename': 'test_model_1_file2.txt',
                    'content_type': 'text',
                    'content': "fieldA2_1\tfieldB2_1\tfieldC2_1\tfieldD2_1\nfieldA2_2\tfieldB2_2\tfieldC2_2\tfieldD2_2\n"""
                }, {
                    'filename': 'test_model_2_file1.txt',
                    'content_type': 'text',
                    'content': "fieldA1_1\tfieldB1_1\tfieldC1_1\tfieldD1_1\nfieldA1_2\tfieldB1_2\tfieldC1_2\tfieldD1_2\n"""
                }, {
                    'filename': 'test_model_2_file2.txt',
                    'content_type': 'text',
                    'content': "fieldA2_1\tfieldB2_1\tfieldC2_1\tfieldD2_1\nfieldA2_2\tfieldB2_2\tfieldC2_2\tfieldD2_2\n"""
                }
            ])
            return result

        self.env['edi.connection']._patch_method(
            'fetch_synchronizations',
            do_fetch_synchronizations
        )

        self.model1_connection_in = self.env['edi.connection'].create({
            'name': 'Test In Connection',
            'connection_type': 'model1_test',
            'configuration': '{}'
        })
        self.in_integration_1 = self.Integration.create({
            'name': 'Test Outgoing Integration 1',
            'integration_type': 'in',
            'connection_id': self.model1_connection_in.id,
            'res_model_id': self.test_model1_model.id,
            'provider_name': 'Test Partner'
        })

        self.model2_connection_in = self.env['edi.connection'].create({
            'name': 'Test In Connection',
            'connection_type': 'model2_test',
            'configuration': '{}'
        })
        self.in_integration_2 = self.Integration.create({
            'name': 'Test Outgoing Integration 2',
            'integration_type': 'in',
            'connection_id': self.model2_connection_in.id,
            'res_model_id': self.test_model2_model.id,
            'provider_name': 'Test Partner'
        })

    def test_integration_create_1(self):

        integration = self.Integration.create({
            'name': 'Test Integration 1',
            'integration_type': 'in',
            'connection_id': self.connection.id,
            'connection_configuration': json.dumps({
                'name': 'Test Connection from Configuration',
                'connection_type': 'test',
                'configuration': '{}'
            }),
            'res_model_id': self.test_model1_model.id,
            'post_message_needed': True,
            'provider_name': 'Test Partner'
        })
        self.assertEqual(len(integration.synchronization_stage_ids), 4, '')

        self.assertFalse(
            integration.post_message_available,
            'post_message_available should be False'
        )
        self.assertEqual(
            integration.connection_id.id,
            self.connection.id,
            'Connections should be the same'
        )

    def test_integration_create_2(self):

        integration = self.Integration.create({
            'name': 'Test Integration 2',
            'integration_type': 'in',
            'connection_configuration': json.dumps({
                'name': 'Test Connection from Configuration',
                'connection_type': 'test',
                'configuration': '{}'
            }),
            'res_model_id': self.test_model2_model.id,
            'post_message_needed': True,
            'provider_name': 'Test Partner'
        })

        self.assertTrue(
            integration.post_message_available,
            'post_message_available should be True'
        )
        self.assertNotEqual(
            integration.connection_id.id,
            self.connection.id,
            'connections should be different'
        )

        with self.assertRaises(ValidationError) as cm:
            integration.synchronization_stage_ids = [(2, 1)]

        exc = cm.exception
        self.assertEqual(
            exc.name,
            'Missing required synchronization stages: new'
        )

    def test_integration_out(self):
        self.out_integration_1.process_integration()
        self.assertEqual(
            self.out_synchronization_1.stage_id.state,
            'done',
            ''
        )

        self.out_integration_2.process_integration()
        self.assertEqual(
            self.out_synchronization_2.stage_id.state,
            'done',
            ''
        )

    def test_integration_in_1(self):

        self.in_integration_1.process_integration()

        in_synchronizations = self.Synchronization.search([
            ('synchronization_type', '=', 'in'),
            ('res_model_id', '=', self.test_model1_model.id)
        ])

        self.assertEqual(
            len(self.in_model1_synchronizations),
            len(in_synchronizations),
            'There should be %s in synchronizations in DB, %s found' % (
                len(self.in_model1_synchronizations),
                len(in_synchronizations)
            )
        )

        in_model1_synchronizations_names = [i['filename'] for i in self.in_model1_synchronizations]
        self.assertSetEqual(
            set(in_synchronizations.mapped('filename')),
            set(in_model1_synchronizations_names),
            ''
        )

    def test_integration_in_2(self):

        self.in_integration_2.process_integration()

        in_synchronizations = self.Synchronization.search([
            ('synchronization_type', '=', 'in'),
            ('res_model_id', '=', self.test_model2_model.id,)
        ])
        self.assertEqual(
            len(self.in_model2_synchronizations),
            len(in_synchronizations),
            'There should be %s in synchronizations in DB, %s found' % (
                len(self.in_model2_synchronizations),
                len(in_synchronizations)
            )
        )

        in_model2_synchronizations_names = [i['filename'] for i in self.in_model2_synchronizations]
        self.assertSetEqual(
            set(in_synchronizations.mapped('filename')),
            set(in_model2_synchronizations_names),
            ''
        )
