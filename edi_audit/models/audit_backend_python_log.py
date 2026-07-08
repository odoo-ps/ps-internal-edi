import logging

from odoo import models


_logger = logging.getLogger("odoo.addons.edi_audit.audit")


class AuditBackendPythonLog(models.AbstractModel):
    _name = "edi.audit.backend.python_log"
    _inherit = "edi.audit.backend"
    _description = "Audit backend: python logging"

    def _audit_start(self, name, metadata):
        _logger.info("Audit run started: %s", name)
        return name

    def _audit_received(self, entry, content):
        _logger.info("Audit [%s] received: %s", entry, content)

    def _audit_sent(self, entry, content):
        _logger.info("Audit [%s] sent: %s", entry, content)

    def _audit_error(self, entry, activity, exception=None, message=None):
        _logger.error("Audit [%s] error during %s: %s", entry, activity, message or exception)

    def _audit_finalize(self, entry, state):
        _logger.info("Audit [%s] finalized: %s", entry, state)
