from odoo import fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    edi_table_record_archive_duration = fields.Integer(default=0, config_parameter="edi.table.record.archive.duration")
    edi_table_record_delete_duration = fields.Integer(default=0, config_parameter="edi.table.record.delete.duration")
    edi_table_record_archive_state_fail = fields.Boolean(default=False, config_parameter="edi.table.record.archive.state.fail")
    edi_table_record_archive_state_done = fields.Boolean(default=False, config_parameter="edi.table.record.archive.state.done")
    edi_table_record_archive_state_new = fields.Boolean(default=False, config_parameter="edi.table.record.archive.state.new")
    edi_table_record_archive_state_cancelled = fields.Boolean(default=False, config_parameter="edi.table.record.archive.state.cancelled")

    def execute(self):
        if (self.edi_table_record_archive_duration or self.edi_table_record_delete_duration) and not any(
                self[f] for f in self._fields if f.startswith("edi_table_record_archive_state_")
        ):
            raise UserError(
                self.env._("An EDI record archive or delete duration should go with actual states to consider for archiving")
            )
        return super(ResConfigSettings, self).execute()
