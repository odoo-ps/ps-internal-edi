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

    def test(self):
        """
            Test the connection is successful with the third party component
        """
        raise NotImplementedError("No test method implemented for this type of connection")

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """
        """
        raise NotImplementedError("No send_synchronization method implemented for this type of connection")

    def _fetch_synchronizations(self, *args, **kwargs):
        """
            Return list of dict or a dict
            the dict should be {
                'filename': FILENAME (str),
                'content': str or dict: will be handle by in edi.integration._process_data
            }
        """
        raise NotImplementedError("No fetch_synchronizations method implemented for this type of connection")

    def _clean_synchronization(self, filename, status, flow_type, *args, **kwargs):
        """
            Status: done if everything went well
                    error if there is something that went wrong
            Default behavior: Do Nothing
        """
        return

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


class ConnectionApi(models.Model):

    _inherit = 'edi.connection'
    _description = 'EDI Connection'

    type = fields.Selection(selection_add=[('api', 'Rpc Api')])

    def test(self):
        self.ensure_one()
        if not self.type == 'api':
            return super().test()

        raise UserError("Not applicable for this type of connection")