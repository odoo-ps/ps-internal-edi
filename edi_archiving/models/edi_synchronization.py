import logging
from datetime import timedelta

from odoo import api, fields, models


_logger = logging.getLogger(__name__)


class Synchronization(models.Model):
    _inherit = "edi.synchronization"

    active = fields.Boolean(default=True)
    error_ids = fields.One2many(context={"active_test": False})  # consider archived errors too

    @api.model
    def _archive_states(self):
        return ["new", "fail", "done", "cancelled"]

    @api.model
    def _archive_states_domain(self, states):
        return [("state", "in", states)]

    @api.model
    def _archive_outdated_synchronizations(self):
        """Archive synchronizations that reached the duration and delete the content
        And empty the content sent or received at the same time
        :return: True if all matching records are archived, False if more to handle
        """
        limit = 10000
        records = self._get_records_to_vacuum(limit, "edi.archive.duration")
        if not records:
            return True
        records[:limit].with_context(prefetch_fields=False).action_archive()  # slow without prefetch with few hundreds
        _logger.info("GC %d synchronizations archived", len(records[:limit]))
        return len(records) <= limit

    @api.model
    def _delete_outdated_synchronizations(self):
        """Delete synchronizations that reached the duration
        :return: True if all matching records are deleted, False if more to handle
        """
        limit = 10000
        records = self.with_context(active_test=False)._get_records_to_vacuum(limit, "edi.archive.duration.delete")
        if not records:
            return True
        records[:limit].unlink()
        _logger.info("GC %d synchronizations deleted", len(records[:limit]))
        return len(records) <= limit

    @api.model
    def _get_records_to_vacuum(self, limit, duration_param):
        """Consider duration and configured states to get records to be vacuumed
        :param limit:
        :param duration_param:
        :return: records to process
        """
        # consider the configured duration
        config = self.env["ir.config_parameter"].sudo()
        duration = int(config.get_param(duration_param, 0))
        if not duration:
            return self
        domain = [("create_date", "<=", fields.Datetime.now() - timedelta(days=duration))]

        # only consider the configured states
        states = [x for x in self._archive_states() if config.get_param("edi.archive.state.%s" % x, False)]
        if not states:  # just a security, but should never happen thanks to check on settings side
            return self
        domain += self._archive_states_domain(states)
        return self.search(domain, limit=limit + 1)

    @api.autovacuum
    def _process_outdated_synchronizations(self):
        delete_complete = self._delete_outdated_synchronizations()
        archive_complete = self._archive_outdated_synchronizations()
        if not delete_complete or not archive_complete:
            self.env.ref("base.autovacuum_job")._trigger(at=fields.Datetime.now() + timedelta(minutes=1))

    def action_archive(self):
        """Free some space by clearing stored content when archiving"""
        if not self:
            return
        self.content = False
        # The error descriptions, not the synchronization contents, are what actually grows: they
        # are written per failure and hold a full traceback each. Clearing them is the same trade
        # already made above -- the row, its activity and its date stay, only the text goes -- but
        # it is opt-in, so that an installation which already configured archiving keeps its
        # behaviour until it decides otherwise.
        if self.env["ir.config_parameter"].sudo().get_param("edi.archive.clear_error_description"):
            self.error_ids.write({"description": False})
        return super().action_archive()


class SynchronizationError(models.Model):
    _inherit = "edi.synchronization.error"

    active = fields.Boolean(related="synchronization_id.active", store=True)
