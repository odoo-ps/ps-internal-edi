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

    def test_python_log_correlates_lines_by_id(self):
        backend = self.env["edi.audit.backend.python_log"]
        with self.assertLogs("odoo.addons.edi_audit.audit", level="INFO") as capture:
            cid = backend._audit_start("run-pl", {})
            backend._audit_received(cid, "payload-in")
            backend._audit_finalize(cid, "done")
        self.assertNotEqual(cid, "run-pl")  # cid is a generated id, not the run name
        joined = "\n".join(capture.output)
        self.assertIn("run-pl", joined)  # human name on the start line
        self.assertIn(f"[{cid}]", joined)  # cid tags the lines
        self.assertGreaterEqual(joined.count(f"[{cid}]"), 2)  # not just the start line

    def test_python_log_distinct_runs_get_distinct_ids(self):
        backend = self.env["edi.audit.backend.python_log"]
        cid1 = backend._audit_start("same-run", {})
        cid2 = backend._audit_start("same-run", {})
        self.assertNotEqual(cid1, cid2)

    def test_ir_logging_backend_creates_records(self):
        backend = self.env["edi.audit.backend.ir_logging"]
        self.assertTrue(backend._audit_needs_cursor)
        with RecordCapturer(self.env["ir.logging"], [("name", "=", "edi_audit")]) as capture:
            entry = backend._audit_start("run-il", {})
            backend._audit_error(entry, "Process", message="kaput")
        logs = capture.records
        self.assertTrue(logs)
        self.assertTrue(any("kaput" in log.message for log in logs))

    def test_ir_logging_correlates_rows_by_path(self):
        backend = self.env["edi.audit.backend.ir_logging"]
        with RecordCapturer(self.env["ir.logging"], [("name", "=", "edi_audit")]) as capture:
            cid = backend._audit_start("run-il", {})
            backend._audit_received(cid, "in")
            backend._audit_finalize(cid, "done")
        rows = capture.records
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows.mapped("path")), {cid})  # every row carries the run's id
        start_row = rows.filtered(lambda r: r.func == "start")
        self.assertEqual(start_row.message, "run-il")  # human name preserved on the start row

    def test_ir_logging_distinct_runs_get_distinct_ids(self):
        backend = self.env["edi.audit.backend.ir_logging"]
        cid1 = backend._audit_start("same-run", {})
        cid2 = backend._audit_start("same-run", {})
        self.assertNotEqual(cid1, cid2)
