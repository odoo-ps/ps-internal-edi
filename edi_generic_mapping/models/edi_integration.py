# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, models


class Integration(models.Model):
    _inherit = "edi.integration"

    type = fields.Selection(selection_add=[("generic", "Generic")], ondelete={"generic": "cascade"}, default="generic")

    # Generic Configuration
    binding_model_id = fields.Many2one("ir.model", string="Binding Model", ondelete="cascade")
    binding_model_name = fields.Char(related="binding_model_id.model", string="Binding Model Name", store=True)

    # Generic Import Configuration
    mapping_ids = fields.One2many("edi.import.mapping", "integration_id", string="Import Mapping")
    record_path = fields.Char(
        string="Record Path",
        help="Path to the list of records in the file.\n"
        "JSON: Dot notation (e.g. 'response.orders')\n"
        "XML: XPath (e.g. '//orders/order')",
    )
    csv_delimiter = fields.Char(string="CSV Delimiter", default=",")
    csv_quotechar = fields.Char(string="CSV Quote Character", size=1, default='"')

    # Generic Export Configuration
    renderer_type = fields.Selection(
        [
            ("tabular", "Tabular (CSV/JSON via Export Profile)"),
            ("qweb", "QWeb Template (XML/JSON Complex)"),
        ],
        default="tabular",
        string="Generation Method",
    )
    export_id = fields.Many2one(
        "ir.exports",
        string="Export Profile",
        domain="[('resource', '=', binding_model_name)]",
        help="Select an existing export favorite to define fields to send.",
    )
    template_id = fields.Many2one(
        "ir.ui.view",
        string="QWeb Template",
        domain=[("type", "=", "qweb")],
        help="QWeb template to render the content.",
    )
