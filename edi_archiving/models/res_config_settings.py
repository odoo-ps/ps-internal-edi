from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    edi_archive_duration = fields.Integer(default=0, config_parameter="edi.archive.duration")
    edi_archive_state_fail = fields.Boolean(default=False, config_parameter="edi.archive.state.fail")
    edi_archive_state_done = fields.Boolean(default=False, config_parameter="edi.archive.state.done")
    edi_archive_state_new = fields.Boolean(default=False, config_parameter="edi.archive.state.new")
    edi_archive_state_cancel = fields.Boolean(default=False, config_parameter="edi.archive.state.cancel")

    def execute(self):
        """Active or archive the cron task depending on the parameters
        Notes :
        - cost is less to change status of cron on every execution than to consider change of values from ir_config
        - introspection with edi_archive_state_ allow other modules to define new state fields,
          and still this code will be able to check them, so no need to add control in other modules
        """
        cron = self.env.ref("edi_archiving.archive_outdated_synchronizations_cron")
        if abs(self.edi_archive_duration) and any(self[f] for f in self._fields if f.startswith("edi_archive_state_")):
            cron.active = True
        else:
            cron.active = False
        return super(ResConfigSettings, self).execute()
