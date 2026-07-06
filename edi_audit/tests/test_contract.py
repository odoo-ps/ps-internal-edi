from odoo.tests.common import TransactionCase, tagged

from odoo.addons.edi_audit.audit.backend import BACKENDS, AuditBackend, get_backend, register


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestAuditContract(TransactionCase):
    def test_register_and_resolve_backend(self):
        # BACKENDS is process-global; don't leak the test key into other tests.
        self.addCleanup(BACKENDS.pop, "dummy_contract", None)

        @register("dummy_contract")
        class _Dummy(AuditBackend):
            def start(self, env, name, metadata):
                pass

            def received(self, content):
                pass

            def sent(self, content):
                pass

            def error(self, activity, exception=None, message=None):
                pass

            def finalize(self, state):
                pass

        backend = get_backend("dummy_contract")
        self.assertIsInstance(backend, _Dummy)
        self.assertFalse(backend.needs_env)
        self.assertIsNone(backend.record)

    def test_unknown_backend_raises(self):
        with self.assertRaisesRegex(KeyError, "no_such_backend"):
            get_backend("no_such_backend")
