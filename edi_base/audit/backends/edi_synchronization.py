from odoo.addons.edi_audit.audit.backend import AuditBackend, register


@register("edi_synchronization")
class EdiSynchronizationBackend(AuditBackend):
    """Audit backend persisting to ``edi.synchronization``.

    ``metadata`` is passed straight to ``create`` (integration_id, filename,
    res_id, user_id, synchronization_date, ...). Content serialization is
    handled by the model's ``_write_received``/``_write_sent``.
    """

    needs_env = True

    def start(self, env, name, metadata):
        vals = dict(metadata or {})
        vals.setdefault("name", name)
        self.record = env["edi.synchronization"].create(vals)

    def received(self, content):
        self.record._write_received(content)

    def sent(self, content):
        self.record._write_sent(content)

    def error(self, activity, exception=None, message=None):
        self.record._report_error(activity, exception=exception, message=message)

    def finalize(self, state):
        if state == "done":
            self.record._done()
        elif state in ("fail", "cancelled"):
            self.record.write({"state": state})
            self.record.flush_recordset(fnames=["state"])
