from odoo import models, fields


class IrCron(models.Model):
    _inherit = "ir.cron"

    integration_ids = fields.One2many("edi.integration", "edi_table_cron_id")
