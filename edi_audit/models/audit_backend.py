import uuid

from odoo import models


class AuditBackend(models.AbstractModel):
    _name = "edi.audit.backend"
    _description = "Audit backend (contract)"

    _audit_needs_cursor = False  # True -> AuditRun opens an isolated cursor

    def _audit_run_id(self):
        """A per-run correlation id, used by log backends to group a run's emissions."""
        return uuid.uuid4().hex

    def _audit_start(self, name, metadata):
        raise NotImplementedError

    def _audit_input(self, entry, content):
        raise NotImplementedError

    def _audit_output(self, entry, content):
        raise NotImplementedError

    def _audit_error(self, entry, activity, exception=None, message=None):
        raise NotImplementedError

    def _audit_finalize(self, entry, state):
        raise NotImplementedError
