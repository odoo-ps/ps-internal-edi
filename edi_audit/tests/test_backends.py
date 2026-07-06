from odoo.tests.common import RecordCapturer, TransactionCase, tagged

from odoo.addons.edi_audit.audit.backend import get_backend


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestAuditBackends(TransactionCase):
    def test_python_log_backend_logs(self):
        backend = get_backend("python_log")
        self.assertFalse(backend.needs_env)
        with self.assertLogs("odoo.addons.edi_audit.audit", level="INFO") as capture:
            backend.start(None, "run-pl", {})
            backend.received("payload-in")
            backend.finalize("done")
        joined = "\n".join(capture.output)
        self.assertIn("run-pl", joined)
        self.assertIn("payload-in", joined)

    def test_ir_logging_backend_creates_records(self):
        backend = get_backend("ir_logging")
        self.assertTrue(backend.needs_env)
        # Called with the test env (rolled back) — no separate cursor needed here.
        with RecordCapturer(self.env["ir.logging"], [("name", "=", "edi_audit")]) as capture:
            backend.start(self.env, "run-il", {})
            backend.error("Process", message="kaput")
        logs = capture.records
        self.assertTrue(logs)
        self.assertTrue(any("kaput" in log.message for log in logs))
