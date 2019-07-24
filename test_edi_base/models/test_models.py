# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class TestModel1(models.Model):

    _name = 'test.model.1'

    field_a = fields.Char('Field A')
    field_b = fields.Char('Field B')


class TestModel2(models.Model):

    _name = 'test.model.2'
    _inherit = 'mail.thread'

    field_a = fields.Char('Field A')
    field_b = fields.Char('Field B')
