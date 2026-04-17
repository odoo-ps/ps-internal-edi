import logging
from datetime import timedelta

from odoo import models, fields, api
from odoo.orm.domains import Domain

_logger = logging.getLogger(__name__)


class EdiTableRecord(models.Model):
    _inherit = "edi.table.record"

    edi_table_auto_archive = fields.Boolean(related="integration_id.edi_table_auto_archive", store=True)
    active = fields.Boolean(default=True)

    def _clear_from_action_filter(self, clear_rule, action):
        if not self.can_be_processed:
            if action == "archive" and clear_rule in ["on_archiving", "on_success_or_archiving", "on_error_or_archiving"]:
                return True
            elif action == "success" and clear_rule == "on_success_or_archiving":
                return True
            elif action == "error" and clear_rule == "on_error_or_archiving":
                return True
        return super()._clear_from_action_filter(clear_rule, action)

    def _reset(self, vals=None):
        super()._reset(vals)
        self.filtered(lambda r: not r.active).action_unarchive()

    @api.model
    def _archive_states(self):
        return ["new", "fail", "done", "cancel", "warning"]

    @api.model
    def _archive_states_domain(self, states):
        return [("state", "in", states)]

    @api.model
    def _archive_outdated_table_records(self):
        """Archive synchronizations that reached the duration and delete the content
        And empty the content sent or received at the same time
        :return: True if all matching records are archived, False if more to handle
        """
        limit = 10000
        records = self._get_table_records_to_vacuum(limit, "edi.table.record.archive.duration")
        if not records:
            return True
        records[:limit].with_context(prefetch_fields=False).action_archive()  # slow without prefetch with few hundreds
        _logger.info(f"GC {len(records[:limit])} table records archived")
        return len(records) <= limit

    @api.model
    def _delete_outdated_table_records(self):
        """Delete synchronizations that reached the duration
        :return: True if all matching records are deleted, False if more to handle
        """
        limit = 10000
        records = self.with_context(active_test=False)._get_table_records_to_vacuum(
            limit, "edi.table.record.delete.duration"
        )
        if not records:
            return True
        records[:limit].unlink()
        _logger.info(f"GC {len(records[:limit])} table records deleted")
        return len(records) <= limit

    @api.model
    def _get_table_records_to_vacuum(self, limit, duration_param):
        """Consider duration and configured states to get table records to be vacuumed
        :param limit:
        :param duration_param:
        :return: records to process
        """
        # consider the configured duration
        config = self.env["ir.config_parameter"].sudo()
        duration = int(config.get_param(duration_param, 0))
        if not duration:
            return self
        domain = Domain([
            ("integration_id.edi_table_auto_archive", "=", True),
            ("can_be_processed", "=", False),
            "|",
            ("last_process_date", "=", False),
            ("last_process_date", "<=", fields.Datetime.now() - timedelta(days=duration)),
        ])

        # only consider the configured states
        states = [
            state for state in self._archive_states()
            if config.get_param(f"edi.table.record.archive.state.{state}", False)
        ]
        if not states:  # just a security, but should never happen thanks to check on settings side
            return self
        domain &= self._archive_states_domain(states)
        return self.search(domain, limit=limit + 1)

    @api.autovacuum
    def _process_outdated_table_records(self):
        delete_complete = self._delete_outdated_table_records()
        archive_complete = self._archive_outdated_table_records()
        if not delete_complete or not archive_complete:
            self.env.ref("base.autovacuum_job")._trigger(at=fields.Datetime.now() + timedelta(minutes=1))
