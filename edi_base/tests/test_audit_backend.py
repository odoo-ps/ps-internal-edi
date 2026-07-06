from odoo.tests.common import TransactionCase, tagged

from odoo.addons.edi_audit.audit.backend import get_backend


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiSynchronizationBackend(TransactionCase):
    def _make_integration(self):
        return self.env["edi.integration"].create(
            {
                "name": "Audit Backend Test",
                "integration_flow": "in",
                "connection_id": self.env.ref("edi_base.api_connection").id,
                "type": "api",
                "synchronization_content_type": "json",
                "active": False,
            }
        )

    def test_backend_records_sync_lifecycle(self):
        integration = self._make_integration()
        backend = get_backend("edi_synchronization")
        self.assertTrue(backend.needs_env)
        backend.start(self.env, "sync-x", {"name": "sync-x", "integration_id": integration.id})
        backend.received("hello")
        backend.finalize("done")

        sync = backend.record
        self.assertEqual(sync.integration_id, integration)
        self.assertEqual(sync.received_content, "hello")
        self.assertEqual(sync.state, "done")

    def test_backend_reports_error(self):
        integration = self._make_integration()
        backend = get_backend("edi_synchronization")
        backend.start(self.env, "sync-e", {"name": "sync-e", "integration_id": integration.id})
        backend.error("Process", message="bad thing")
        backend.finalize("fail")

        sync = backend.record
        self.assertEqual(sync.state, "fail")
        self.assertTrue(sync.error_ids)
        self.assertIn("bad thing", sync.error_ids.description)

    def test_backend_finalize_cancelled(self):
        integration = self._make_integration()
        backend = get_backend("edi_synchronization")
        backend.start(self.env, "sync-c", {"name": "sync-c", "integration_id": integration.id})
        backend.finalize("cancelled")

        self.assertEqual(backend.record.state, "cancelled")
