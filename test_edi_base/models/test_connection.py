# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class TestConnection(models.Model):

    _inherit = 'edi.connection'

    connection_type = fields.Selection(selection_add=[('test', 'Test connection')])

    def _test_test(self):
        return True

    def _test_send_synchronization(self, synchronization, *args, **kwargs):
        pass

    def _test_fetch_synchronizations(self, *args, **kwargs):
        return []

    def _test_clean_synchronization(self, synchronization, *args, **kwargs):
        pass

    def _is_valid_filename(self, filename):
        self.ensure_one()
        return False


class Model1TestConnection(models.Model):

    _inherit = 'edi.connection'

    connection_type = fields.Selection(selection_add=[('model1_test', 'Model 1 Test connection')])

    def _model1_test_fetch_synchronizations(self, *args, **kwargs):
        return []

    def _is_valid_filename(self, filename):
        self.ensure_one()

        result = super(Model1TestConnection, self)._is_valid_filename(filename)
        if self.connection_type == 'model1_test' and filename.startswith('test_model_1_'):
            return True

        return result


class Model2TestConnection(models.Model):

    _inherit = 'edi.connection'

    connection_type = fields.Selection(selection_add=[('model2_test', 'Model 2 Test connection')])

    def _model2_test_fetch_synchronizations(self, *args, **kwargs):
        return []

    def _is_valid_filename(self, filename):
        self.ensure_one()

        result = super(Model2TestConnection, self)._is_valid_filename(filename)
        if self.connection_type == 'model2_test' and filename.startswith('test_model_2_'):
            return True

        return result
