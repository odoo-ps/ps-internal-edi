# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.tests import TransactionCase


class ConnectionTest(TransactionCase):

    def setUp(self):

        super(ConnectionTest, self).setUp()

        self.connection = self.env['edi.connection'].create({
            'name': 'Test Connection',
            'connection_type': 'test',
            'configuration': '{}'
        })

    def test_connection_test(self):
        self.connection.test()

    def test_send_synchronization(self):
        self.connection.send_synchronization(False)

    def test_fetch_synchronizations(self):
        result = self.connection.fetch_synchronizations()
        self.assertTrue(isinstance(result, list))

    def test_clean_synchronization(self):
        self.connection.clean_synchronization(False, '')
