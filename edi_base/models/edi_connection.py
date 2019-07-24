# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class Connection(models.Model):

    _name = 'edi.connection'
    _description = 'EDI Connection'

    name = fields.Char(required=True)
    type = fields.Selection(selection=[], required=True, string='Type')
    configuration = fields.Text()

    @api.multi
    def test(self):
        """
            Test the connection is successful with the third party component
        """
        raise NotImplementedError("No test method implemented for this type of connection")

    def send_synchronization(self, synchronization, *args, **kwargs):
        """
        """
        raise NotImplementedError("No send_synchronization method implemented for this type of connection")

    def fetch_synchronizations(self, *args, **kwargs):
        """
        """
        raise NotImplementedError("No fetch_synchronizations method implemented for this type of connection")

    def clean_synchronization(self, filename, status, *args, **kwargs):
        """
        """
        raise NotImplementedError("No clean_synchronization method implemented for this type of connection")

    def _get_default_configuration(self):
        """
            Return a dictionnary 
            with the template configuration for this type of connection
        """
        return {}


    ###################################
    #    End of abstract interface    #
    #  don't override these methods   #
    ###################################

    @api.onchange('type')
    def _set_default_configuration(self):
        if not self.configuration or self.configuration == '{}':
            self.configuration = json.dumps(self._get_default_configuration(), indent=4, sort_keys=True)

    def _read_configuration(self):
        self.ensure_one()
        return json.loads(self.configuration)


import os

class ConnectionFolder(models.Model):

    _inherit = 'edi.connection'
    _description = 'EDI Connection'

    type = fields.Selection(selection_add=[('folder', 'Folder')])

    def _get_default_configuration(self):
        if self.type != 'folder':
            return super()._get_default_configuration()

        return {
            'in_folder' : '<PATH HERE>',
            'out_folder' : '<PATH HERE>',
        }


    @api.multi
    def test(self):
        """
        """
        self.ensure_one()
        if not self.type == 'folder':
            return super().test()

        config = self._read_configuration()
        
        for fname in [config['in_folder'], config['out_folder']]:
            path = "%s/test" % config['in_folder']
            with open(path, "w") as in_f:
                in_f.write("Test")
            os.remove(path)
        raise UserError("Connection Successful")

    def _connect(self):
        return True

    def send_synchronization(self, synchronization, *args, **kwargs):
        """
        """

        self.ensure_one()
        getattr(self, '_%s_send_synchronization' % self.connection_type)(synchronization, *args, **kwargs)

    def fetch_synchronizations(self, *args, **kwargs):
        """
        """

        self.ensure_one()
        return getattr(self, '_%s_fetch_synchronizations' % self.connection_type)(*args, **kwargs)

    def clean_synchronization(self, filename, status, *args, **kwargs):
        """
        """

        self.ensure_one()
        getattr(
            self,
            '_%s_%s_clean_synchronization' % (self.connection_type, status),
            getattr(
                self,
                '_%s_clean_synchronization' % self.connection_type,
                lambda *a, **kw: None
            )
        )(filename, *args, **kwargs)
