# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models


class Integration(models.Model):
    _inherit = "edi.integration"

    def _get_in_content(self):
        """
        Override to pass the integration_id into the kwargs
        """
        if self.connection_id.type != "sftp":
            return super()._get_in_content()

        return self.connection_id._fetch_synchronizations(integration_id=self)
