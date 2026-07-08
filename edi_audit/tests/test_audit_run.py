from odoo.tests.common import TransactionCase, tagged

from odoo.addons.edi_audit.audit import AuditRun, audit_run


class _RecordingBackend:
    _audit_needs_cursor = False

    def __init__(self):
        self.calls = []

    def _audit_start(self, name, metadata):
        self.calls.append(("start", name))
        return name

    def _audit_received(self, entry, content):
        self.calls.append(("received", entry, content))

    def _audit_sent(self, entry, content):
        self.calls.append(("sent", entry, content))

    def _audit_error(self, entry, activity, exception=None, message=None):
        self.calls.append(("error", entry, activity, str(exception) if exception else message))

    def _audit_finalize(self, entry, state):
        self.calls.append(("finalize", entry, state))


class _CursorBackend(_RecordingBackend):
    _audit_needs_cursor = True


class _FailingStartBackend(_RecordingBackend):
    _audit_needs_cursor = True

    def _audit_start(self, name, metadata):
        raise ValueError("start kaboom")  # noqa: EM101


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestAuditRun(TransactionCase):
    def test_span_success_lifecycle(self):
        backend = _RecordingBackend()
        with audit_run(self.env, backend, name="run1") as run:
            run.received("in")
            run.sent("out")
        self.assertEqual(
            backend.calls,
            [
                ("start", "run1"),
                ("received", "run1", "in"),
                ("sent", "run1", "out"),
                ("finalize", "run1", "done"),
            ],
        )
        self.assertEqual(run.entry, "run1")

    def test_span_exception_finalizes_fail(self):
        backend = _RecordingBackend()
        with self.assertRaisesRegex(ValueError, "boom"):
            with audit_run(self.env, backend, name="run2"):
                raise ValueError("boom")
        self.assertEqual(backend.calls[0], ("start", "run2"))
        self.assertIn(("finalize", "run2", "fail"), backend.calls)
        self.assertTrue(any(call[0] == "error" for call in backend.calls))

    def test_no_cursor_when_backend_needs_no_cursor(self):
        run = AuditRun(self.env, _RecordingBackend(), name="run3")
        run.start()
        self.addCleanup(run.close)
        self.assertIsNone(run.env)

    def test_isolated_cursor_when_backend_needs_cursor(self):
        run = AuditRun(self.env, _CursorBackend(), name="run4")
        run.start()
        self.addCleanup(run.close)
        self.assertIsNotNone(run.env)
        self.assertIsNot(run.env.cr, self.env.cr)

    def test_start_failure_closes_cursor(self):
        run = AuditRun(self.env, _FailingStartBackend(), name="run6")
        with self.assertRaisesRegex(ValueError, "start kaboom"):
            run.start()
        self.assertIsNone(run.env)

    def test_unknown_backend_key_raises_on_start(self):
        run = AuditRun(self.env, "nope_key", name="run5")
        with self.assertRaisesRegex(KeyError, "edi.audit.backend.nope_key"):
            run.start()

    def test_cancel_finalizes_cancelled(self):
        backend = _RecordingBackend()
        run = AuditRun(self.env, backend, name="run7")
        run.start()
        run.cancel()
        self.assertIn(("finalize", "run7", "cancelled"), backend.calls)
        self.assertTrue(run.finalized)
