# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, models
from odoo.exceptions import ValidationError


class Integration(models.Model):
    _inherit = "edi.integration"

    @api.constrains("integration_flow_type", "synchronization_creation", "connection_id")
    def _check_let_in_folder_and_synchronization_creation(self):
        for rec in self:
            if (
                rec.connection_id.ftp_in_done_let
                and rec.integration_flow_type == "in"
                and rec.synchronization_creation != 1
            ):
                raise ValidationError(_('Let in "in_folder" only works with Synchronization Creation = 1'))

    def _get_in_content(self):
        """
        Override to pass the integration_id into the kwargs
        """
        if self.connection_id.type != "ftp":
            return super()._get_in_content()

        return self.connection_id._fetch_synchronizations(integration_id=self)
