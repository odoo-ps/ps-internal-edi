# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, safe_eval
from .edi_integration import integration

_logger = logging.getLogger(__name__)

class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model
    @integration("Create Partner")
    def create_partner(self, data):
        """
            import odoolib
            odoolib.get_connection(database='edi_test', login='admin', password='admin', hostname="localhost").get_model("res.partner").create_partner({'name': 'Hello'})
        """
        return self.create([data]).id

class Integration(models.Model):

    _inherit = 'edi.integration'

    type = field.Selection(selection_add=[('partner_folder_out', 'Export Partner in Folder')])