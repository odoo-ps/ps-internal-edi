from odoo import models


class AuditBackendIrLogging(models.AbstractModel):
    _name = "edi.audit.backend.ir_logging"
    _inherit = "edi.audit.backend"
    _description = "Audit backend: ir.logging"

    _audit_needs_cursor = True

    def _audit_start(self, name, metadata):
        self._audit_log("start", name)
        return name

    def _audit_received(self, entry, content):
        self._audit_log("received", content)

    def _audit_sent(self, entry, content):
        self._audit_log("sent", content)

    def _audit_error(self, entry, activity, exception=None, message=None):
        self._audit_log(activity or "error", message or exception, level="ERROR")

    def _audit_finalize(self, entry, state):
        self._audit_log("finalize", state)

    def _audit_log(self, func, message, level="INFO"):
        self.env["ir.logging"].create({
            "name": "edi_audit",
            "type": "server",
            "level": level,
            "dbname": self.env.cr.dbname,
            "message": str(message),
            "func": func,
            "path": "edi_audit",
            "line": "0",
        })
