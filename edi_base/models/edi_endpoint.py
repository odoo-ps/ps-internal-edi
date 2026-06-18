# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class Endpoint(models.Model):
    _name = "edi.endpoint"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "EDI Endpoint"
    _order = "name"

    name = fields.Char(required=True)
    connection_id = fields.Many2one(
        "edi.connection",
        required=True,
        ondelete="cascade",
    )
    auth_type = fields.Selection(related="connection_id.api_auth_type")

    role = fields.Selection(
        [("resource", "Resource"), ("token", "OAuth2 Token")],
        default="resource",
        required=True,
        tracking=True,
        help="OAuth2 Token:\nCalled once to obtain an access token.\n\n"
        "Resource:\nThe actual API business endpoint called to exchange data.",
    )

    api_key = fields.Char(string="API Key", copy=False)

    grant_type = fields.Selection(
        [
            ("client_credentials", "Client Credentials"),
            ("password", "Password"),
        ],
        string="Grant Type",
        default="client_credentials",
    )

    username = fields.Char()
    password = fields.Char()
    client_id = fields.Char()
    client_secret = fields.Char()
    scope = fields.Char()

    method = fields.Selection(
        [("get", "GET"), ("post", "POST"), ("put", "PUT"), ("patch", "PATCH"), ("delete", "DELETE")],
        default="post",
        required=True,
        string="HTTP Method",
        tracking=True,
    )
    url = fields.Char(
        required=True,
        help="Absolute URL of the endpoint, e.g. https://myprovider.com/endpoints/endpoint1",
        tracking=True,
    )

    integration_ids = fields.One2many(
        "edi.integration", "api_endpoint_id", readonly=True, context={"active_test": False}
    )

    @api.constrains("role", "connection_id")
    def _check_unique_token_endpoint(self):
        for endpoint in self:
            if endpoint.role == "token" and self.search_count(
                [
                    ("connection_id", "=", endpoint.connection_id.id),
                    ("role", "=", "token"),
                    ("id", "!=", endpoint.id),
                ]
            ):
                raise ValidationError(
                    self.env._(
                        "Connection '%s' already has a token endpoint. Only one is allowed per connection.",
                        endpoint.connection_id.name,
                    )
                )
