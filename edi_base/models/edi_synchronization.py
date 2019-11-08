# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import traceback

from odoo import api, fields, models, SUPERUSER_ID

class SynchronizationError(models.Model):

    _name = 'edi.synchronization.error'
    _description = 'Synchronization Error'
    _order = 'create_date desc'

    integration_id = fields.Many2one(related='synchronization_id.integration_id', store=True)
    synchronization_id = fields.Many2one(
        comodel_name='edi.synchronization',
        on_delete='cascade',
        string='Synchronization'
    )
    activity = fields.Char()
    description = fields.Text()
    description_short = fields.Text(compute='_get_short_desc')

    def _get_short_desc(self):
        for rec in self:
            if not rec.description or len(rec.description) < 650:
                rec.description_short = rec.description
            else:
                rec.description_short = "%s\n....\n%s" % (rec.description[:150], rec.description[-500:])

class Synchronization(models.Model):
    """
        Object to store the status of the synchronization
    """

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
    res_id = fields.Integer(string='Resource ID')
    synchronization_date = fields.Datetime(readonly=True, string='Synchronized on')
    content = fields.Text(readonly=True)
    error_ids = fields.One2many('edi.synchronization.error', 'synchronization_id', string='synchronization_id')
    errors_count = fields.Integer(_compute='_compute_errors_count', string='# errors')
    user_id = fields.Many2one('res.users', string='Trigger User', help="User that trigger the synchronization or call the API")

    _sql_constraints = [
        (
            'name_integration_id_uniq',
            'unique (name, integration_id)',
            'The name must be unique per integration!'
        )
    ]

    def open_integration(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Integration',
            'res_model': 'edi.integration',
            'res_id': self.integration_id.id,
            'view_mode': 'form'
        }

    def open_resource_records(self):
        self.ensure_one()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Open records',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form'
        }

    ##################
    #      API       #
    ##################
    def _report_error(self, activity, exception=None, message=None):
        description = "Unkown Error"
        if exception:
            tb = traceback.format_exc()
            description = "%s\n\n%s" % (str(exception), str(tb))
        if message:
            description = message

        self.write({
            'state': 'fail',
            'error_ids' : [(0, 0, {
                'activity': activity,
                'description': description,
            })]
        })
        self.flush(fnames=['state', 'error_ids', 'content_type'], records=self)

    def _write_content(self, content):
        self.write({'content': content})
        self.flush(fnames=['content'], records=self)

    def _done(self):
        self.write({'state': 'done'})
        self.flush(fnames=['state', 'content_type', 'synchronization_flow'], records=self)

