# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging
import csv
from io import StringIO

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, safe_eval
from odoo.addons.edi_base.models.decorator import integration

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

    type = fields.Selection(selection_add=[('partner_folder_out', 'Export Partner in Folder')])


    def _get_record_to_send(self):
        if self.type != 'partner_folder_out':
            return super()._get_record_to_send()

        conf = self._read_parameter()
        domain = conf['filter']
        return self.env['res.partner'].search(domain)

    def _get_content(self, records):
        if self.type != 'partner_folder_out':
            return super()._get_record_to_send()

        content = StringIO()
        writer = csv.writer(content)

        conf = self._read_parameter()
        fields = conf['fields']
        writer.writerow(fields)
        rows = records.export_data(fields, raw_data=True)['datas']
        writer.writerows(rows)
        return content.getvalue()

class Integration(models.Model):

    _inherit = 'edi.integration'

    type = fields.Selection(selection_add=[('partner_folder_in', 'Import Partner in Folder')])

    def _process_content(self, filename, content):
        if self.type != 'partner_folder_in':
            return super()._get_record_to_send()

        csv_file = StringIO(content)
        reader = csv.reader(csv_file, delimiter=',')
        header = reader.__next__()
        for line in reader:
            if not line[0]:
                self._report_error("Import Partner", message="No value for field name, name is required \n %s" % line)
                continue
            self.env['res.partner'].load(header, [line])

        return "done"