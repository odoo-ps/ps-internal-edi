# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, SUPERUSER_ID


class Synchronization(models.Model):

    _name = 'edi.synchronization'
    _description = 'Synchronization'

    name = fields.Char(readonly=True, required=True)
    filename = fields.Char(readonly=True)
    state = fields.Selection([
            ('new', 'New'), 
            ('fail', 'Fail'), 
            ('done', 'Done'), 
            ('cancelled', 'Cancelled')
        ], 
        default="new",
        string='Status'
    )
    integration_id = fields.Many2one('edi.integration', required=True, string='Integration')
    synchronization_flow = fields.Selection(
        related='integration_id.integration_flow',
        store=True, readonly=True, string='Type'
    )
    content_type = fields.Selection(
        related='integration_id.synchronization_content_type',
        store=True, readonly=True, string='Content type'
    )
    #res_model_id = fields.Many2one(related='integration_id.res_model_id', store=True, string='Resource model')
    #res_model = fields.Char(related='res_model_id.model', string='Resouce model name')
    res_id = fields.Integer(string='Resource ID')
    synchronization_date = fields.Datetime(readonly=True, string='Synchronized on')
    content = fields.Text(readonly=True)
    error_ids = fields.One2many('edi.synchronization.error', 'synchronization_id', string='synchronization_id')
    errors_count = fields.Integer(_compute='_compute_errors_count', string='# errors')

    def _process_in(self, data):
        """
        """
        raise NotImplementedError("No _process method implemented for this type of connection")

    def _process_out(self, records):
        """
        """
        raise NotImplementedError("No _process method implemented for this type of connection")

    def _get_content(self):
        """ 
            For file sync process return a dict 
            {
                'filename': string
                'filecontent': base64_encoded binary
            }
            for web service process return a dict with all the parameter of the query
        """
        raise NotImplementedError("No _get_content method implemented for this type of connection")

    ###################################
    #    End of abstract interface    #
    #  don't override these methods   #
    ###################################

    _sql_constraints = [
        (
            'name_integration_id_uniq',
            'unique (name, integration_id)',
            'The name must be unique per integration!'
        )
    ]

    @api.depends('error_ids')
    def _compute_errors_count(self):
        for synchronization in self:
            synchronization.errors_count = len(synchronization.error_ids)

    @api.multi
    def open_integration(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Integration',
            'res_model': 'edi.integration',
            'res_id': self.integration_id.id,
            'view_mode': 'form'
        }

    @api.multi
    def open_resource_records(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Open records',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form'
        }

    



class SynchronizationError(models.Model):

    _name = 'edi.synchronization.error'
    _description = 'Synchronization Error'

    synchronization_id = fields.Many2one(
        comodel_name='edi.synchronization',
        on_delete='cascade',
        string='Synchronization'
    )
    activity = fields.Char()
    description = fields.Text()
