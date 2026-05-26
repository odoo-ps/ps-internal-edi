# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class Endpoint(models.Model):
    _name = "edi.endpoint"
    _description = "EDI Endpoint"
    _order = "name"

    name = fields.Char(required=True)
    connection_id = fields.Many2one(
        "edi.connection",
        required=True,
        ondelete="cascade",
        string="Connection",
    )
    method = fields.Selection(
        [("GET", "GET"), ("POST", "POST"), ("PUT", "PUT"), ("PATCH", "PATCH"), ("DELETE", "DELETE")],
        default="POST",
        string="HTTP Method",
    )
    path = fields.Char(
        string="Path",
        help="Relative URL path, e.g. /api/v1/orders",
    )
    integration_ids = fields.One2many("edi.integration", "endpoint_id", readonly=True, string="Integrations")
