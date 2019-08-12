# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import UserError

import os
import os.path
from pathlib import Path

class ConnectionFolder(models.Model):

    _inherit = 'edi.connection'
    _description = 'EDI Connection'

    type = fields.Selection(selection_add=[('folder', 'Folder')])

    def _get_default_configuration(self):
        if self.type != 'folder':
            return super()._get_default_configuration()

        return {
            'in_folder' : '<PATH HERE>',
            'in_folder_done': '<PATH HERE>',
            'in_folder_error': '<PATH HERE>',
            'out_folder' : '<PATH HERE>',
        }


    @api.multi
    def test(self):
        self.ensure_one()
        if not self.type == 'folder':
            return super().test()

        config = self._read_configuration()
        for fname in [config['in_folder'], config['out_folder'], config['in_folder_done'], config['in_folder_error']]:
            path = "%s/test" % config['in_folder']
            with open(path, "w") as in_f:
                in_f.write("Test")
            os.remove(path)
        raise UserError("Connection Successful")

    def _send_synchronization(self, filename, content, *args, **kwargs):
        self.ensure_one()
        if not self.type == 'folder':
            return super()._send_synchronization()

        config = self._read_configuration()
        path = "%s/%s" % (config['out_folder'], filename)
        with open(path, 'w') as out_file:
            out_file.write(content)

    def _fetch_synchronizations(self, *args, **kwargs):
        self.ensure_one()
        if not self.type == 'folder':
            return super()._send_synchronization()

        config = self._read_configuration()
        data = []
        for f in os.listdir(config['in_folder']):
            file_path = "%s/%s" % (config['in_folder'], f)
            if os.path.isfile(file_path):
                with open(file_path, 'r') as fd:
                    data.append({
                        'filename': f,
                        'content': fd.read(),
                    })
        return data

    def _clean_synchronization(self, filename, status, flow_type, *args, **kwargs):
        self.ensure_one()
        if not self.type == 'folder':
            return super()._clean_synchronization()

        config = self._read_configuration()
        if flow_type == 'out':
            path = "%s/%s" % (config['out_folder'], filename)
            if status == 'error':
                if os.path.isfile(path):
                    os.remove(path)

        if flow_type == 'in':
            path = "%s/%s" % (config['in_folder'], filename)
            if status == "done":
                done_path = "%s/%s" % (config['in_folder_done'], filename)
            else:
                done_path = "%s/%s" % (config['in_folder_error'], filename)
            os.rename(path, done_path)


