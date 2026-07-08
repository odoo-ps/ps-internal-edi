from odoo.tests.common import RecordCapturer, TransactionCase, tagged


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestAuditBackendModels(TransactionCase):
    def test_backend_resolves_by_model_name(self):
        backend = self.env["edi.audit.backend.python_log"]
        self.assertFalse(backend._audit_needs_cursor)

    def test_unknown_backend_model_raises(self):
        with self.assertRaisesRegex(KeyError, "edi.audit.backend.nope"):
            self.env["edi.audit.backend.nope"]  # noqa: B018

    def test_python_log_backend_logs(self):
        backend = self.env["edi.audit.backend.python_log"]
        with self.assertLogs("odoo.addons.edi_audit.audit", level="INFO") as capture:
            entry = backend._audit_start("run-pl", {})
            backend._audit_received(entry, "payload-in")
            backend._audit_finalize(entry, "done")
        joined = "\n".join(capture.output)
        self.assertIn("run-pl", joined)
        self.assertIn("payload-in", joined)

    def test_ir_logging_backend_creates_records(self):
        backend = self.env["edi.audit.backend.ir_logging"]
        self.assertTrue(backend._audit_needs_cursor)
        with RecordCapturer(self.env["ir.logging"], [("name", "=", "edi_audit")]) as capture:
            entry = backend._audit_start("run-il", {})
            backend._audit_error(entry, "Process", message="kaput")
        logs = capture.records
        self.assertTrue(logs)
        self.assertTrue(any("kaput" in log.message for log in logs))
