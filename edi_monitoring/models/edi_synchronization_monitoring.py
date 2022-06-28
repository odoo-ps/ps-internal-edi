from odoo import api, fields, models


class Synchronization(models.Model):
    _inherit = 'edi.synchronization'

    monitoring_report_id = fields.Many2one('edi.monitoring.report')
    last_issue = fields.Text(compute='_compute_last_issue')

    @api.depends('error_ids.description')
    def _compute_last_issue(self):
        for rec in self:
            rec.last_issue = rec.error_ids[0].description_short if rec.error_ids else False
