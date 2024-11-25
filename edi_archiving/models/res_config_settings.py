from odoo import _, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    edi_archive_duration = fields.Integer(default=0, config_parameter="edi.archive.duration")
    edi_delete_duration = fields.Integer(default=0, config_parameter="edi.archive.duration.delete")
    edi_archive_state_fail = fields.Boolean(default=False, config_parameter="edi.archive.state.fail")
    edi_archive_state_done = fields.Boolean(default=False, config_parameter="edi.archive.state.done")
    edi_archive_state_new = fields.Boolean(default=False, config_parameter="edi.archive.state.new")
    edi_archive_state_cancel = fields.Boolean(default=False, config_parameter="edi.archive.state.cancel")

    def execute(self):
        if (self.edi_archive_duration or self.edi_delete_duration) and not any(
            self[f] for f in self._fields if f.startswith("edi_archive_state_")
        ):
            raise UserError(
                _("An EDI archive or delete duration should go with actual states to consider for archiving")
            )
        return super(ResConfigSettings, self).execute()
