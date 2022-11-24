""" Add tracked fields on integration model + chatter """
import logging

from odoo import fields, models


_logger = logging.getLogger(__name__)


class Integration(models.Model):
    _name = "edi.integration"
    _inherit = ["edi.integration", "mail.thread", "mail.activity.mixin"]

    # cron_id
    active = fields.Boolean(related="cron_id.active", readonly=False, tracking=True)
    interval_number = fields.Integer(related="cron_id.interval_number", readonly=False, tracking=True)
    interval_type = fields.Selection(related="cron_id.interval_type", readonly=False, tracking=True)

    # integration
    parameter = fields.Text(tracking=True)
    integration_flow = fields.Selection(tracking=True)
    type = fields.Selection(tracking=True)
    connection_id = fields.Many2one(tracking=True)
