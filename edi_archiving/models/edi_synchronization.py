import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class Synchronization(models.Model):
    _inherit = "edi.synchronization"

    active = fields.Boolean(default=True)

    @api.model
    def archive_outdated_synchronizations(self):
        """Call by cron task to archive synchronizations having reach the duration"""
        config = self.env["ir.config_parameter"].sudo()

        # consider the configured duration
        duration = int(config.get_param("edi.archive.duration", 0))
        if not duration:
            _logger.warning("Cron task archive_outdated_synchronizations_cron should be archived : duration=0")
            return True
        domain = [("create_date", "<=", fields.Datetime.now() - timedelta(days=duration))]

        # only consider the configured states
        states = [x for x in self._archive_states() if config.get_param("edi.archive.state.%s" % x, False)]
        if not states:
            _logger.warning("Cron task archive_outdated_synchronizations_cron should be archived : no state configured")
            return True
        domain += self._archive_states_domain(states)

        self.search(domain).active = False
        return True

    @api.model
    def _archive_states(self):
        return ["new", "fail", "done", "cancel"]

    @api.model
    def _archive_states_domain(self, states):
        return [("state", "in", states)]


class SynchronizationError(models.Model):
    _inherit = "edi.synchronization.error"

    active = fields.Boolean(related="synchronization_id.active", store=True)
