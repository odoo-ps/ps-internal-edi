from odoo import models, fields


class Integration(models.Model):
    _inherit = "edi.integration"

    edi_table_record_clear_content = fields.Selection(selection_add=
        [
            ("on_archiving", "On Archiving"),
            ("on_success_or_archiving", "On Success or Archiving"),
            ("on_error_or_archiving", "On Error or Archiving"),
        ],
        ondelete={
            "on_archiving": "set default",
            "on_success_or_archiving": "set default",
            "on_error_or_archiving": "set default"
        },
    )
    edi_table_auto_archive = fields.Boolean(
        string="EDI 2-steps Auto Archive",
        default=True,
        help="Should the Archiving CRON archive the EDI 2-steps queue records ?",
    )


