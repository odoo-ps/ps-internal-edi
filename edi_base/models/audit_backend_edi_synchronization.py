from odoo import models


class AuditBackendEdiSynchronization(models.AbstractModel):
    _name = "edi.audit.backend.edi_synchronization"
    _inherit = "edi.audit.backend"
    _description = "Audit backend: edi.synchronization"

    _audit_needs_cursor = True

    def _audit_start(self, name, metadata):
        vals = dict(metadata or {})
        vals.setdefault("name", name)
        return self.env["edi.synchronization"].create(vals)

    def _audit_input(self, entry, content):
        if self._is_outbound(entry):
            entry._write_sent(content)
        else:
            entry._write_received(content)

    def _audit_output(self, entry, content):
        if self._is_outbound(entry):
            entry._write_received(content)
        else:
            entry._write_sent(content)

    def _is_outbound(self, entry):
        # out and out_real are both outbound (see edi.integration._get_out_flow_type);
        # use the canonical classifier rather than a literal so out_real binds correctly.
        return entry.integration_id.integration_flow_type == "out"

    def _audit_error(self, entry, activity, exception=None, message=None):
        entry._report_error(activity, exception=exception, message=message)

    def _audit_finalize(self, entry, state):
        if state == "done":
            entry._done()
        elif state in ("fail", "cancelled"):
            entry.write({"state": state})
            entry.flush_recordset(fnames=["state"])
