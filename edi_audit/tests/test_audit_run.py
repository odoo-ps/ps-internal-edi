from odoo.tests.common import TransactionCase, tagged

from odoo.addons.edi_audit.audit import AuditRun, audit_run
from odoo.addons.edi_audit.audit.backend import AuditBackend


class _RecordingBackend(AuditBackend):
    needs_env = False

    def __init__(self):
        self.calls = []

    def start(self, env, name, metadata):
        self.calls.append(("start", name))

    def received(self, content):
        self.calls.append(("received", content))

    def sent(self, content):
        self.calls.append(("sent", content))

    def error(self, activity, exception=None, message=None):
        self.calls.append(("error", activity, str(exception) if exception else message))

    def finalize(self, state):
        self.calls.append(("finalize", state))


class _EnvBackend(_RecordingBackend):
    needs_env = True

    def start(self, env, name, metadata):
        super().start(env, name, metadata)
        self._seen_env = env


class _FailingStartBackend(_RecordingBackend):
    needs_env = True

    def start(self, env, name, metadata):
        raise ValueError("start kaboom")


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestAuditRun(TransactionCase):
    def test_span_success_lifecycle(self):
        backend = _RecordingBackend()
        with audit_run(self.env, backend, name="run1") as run:
            run.received("in")
            run.sent("out")
        self.assertEqual(
            backend.calls,
            [("start", "run1"), ("received", "in"), ("sent", "out"), ("finalize", "done")],
        )

    def test_span_exception_finalizes_fail(self):
        backend = _RecordingBackend()
        with self.assertRaisesRegex(ValueError, "boom"):
            with audit_run(self.env, backend, name="run2"):
                raise ValueError("boom")
        self.assertEqual(backend.calls[0], ("start", "run2"))
        self.assertIn(("finalize", "fail"), backend.calls)
        self.assertTrue(any(call[0] == "error" for call in backend.calls))

    def test_no_cursor_when_backend_needs_no_env(self):
        run = AuditRun(self.env, _RecordingBackend(), name="run3")
        run.start()
        self.addCleanup(run.close)
        self.assertIsNone(run.env)

    def test_isolated_cursor_when_backend_needs_env(self):
        run = AuditRun(self.env, _EnvBackend(), name="run4")
        run.start()
        self.addCleanup(run.close)
        self.assertIsNotNone(run.env)
        self.assertIsNot(run.env.cr, self.env.cr)

    def test_start_failure_closes_cursor(self):
        run = AuditRun(self.env, _FailingStartBackend(), name="run6")
        with self.assertRaisesRegex(ValueError, "start kaboom"):
            run.start()
        # cursor was opened (needs_env) but must have been closed on failure
        self.assertIsNone(run.env)

    def test_string_backend_key_resolves(self):
        # a backend passed by key is resolved via the registry
        with self.assertRaisesRegex(KeyError, "nope_key"):
            AuditRun(self.env, "nope_key", name="run5")

    def test_cancel_finalizes_cancelled(self):
        backend = _RecordingBackend()
        run = AuditRun(self.env, backend, name="run7")
        run.start()
        run.cancel()
        self.assertIn(("finalize", "cancelled"), backend.calls)
        self.assertTrue(run.finalized)
