# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models


class TestModel1IncomingSynchronization(models.Model):

    _inherit = 'edi.synchronization'

    def _test_model_1_text_in_synchronization_process(self, item):

        filename, extension = item['filename'].split('.')

        if filename.startswith('test_model_1_') and extension == 'txt':
            return

        raise


class TestModel2IncomingSynchronization(models.Model):

    _inherit = 'edi.synchronization'

    def _test_model_2_text_in_synchronization_process(self, item):

        filename, extension = item['filename'].split('.')

        if filename.startswith('test_model_2_') and extension == 'txt':
            return

        raise
