from odoo import api
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.edi_audit.audit import audit_run
from odoo.addons.edi_base.tests.test_edi_common import TestEDICommon


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiSynchronizationBackendModel(TestEDICommon):
    def _make_integration(self, flow="in"):
        return self.env["edi.integration"].create({
            "name": "Audit Backend Model Test",
            "integration_flow": flow,
            "connection_id": self.env.ref("edi_base.api_connection").id,
            "type": "api",
            "synchronization_content_type": "json",
            "active": False,
        })

    def test_backend_records_sync_lifecycle(self):
        integration = self._make_integration()
        backend = self.env["edi.audit.backend.edi_synchronization"]
        self.assertTrue(backend._audit_needs_cursor)
        entry = backend._audit_start("sync-x", {"name": "sync-x", "integration_id": integration.id})
        backend._audit_input(entry, "hello")
        backend._audit_finalize(entry, "done")
        self.assertEqual(entry.integration_id, integration)
        self.assertEqual(entry.received_content, "hello")
        self.assertEqual(entry.state, "done")

    @mute_logger("odoo.models.unlink")
    def test_backend_reports_error(self):
        integration = self._make_integration()
        backend = self.env["edi.audit.backend.edi_synchronization"]
        entry = backend._audit_start("sync-e", {"name": "sync-e", "integration_id": integration.id})
        backend._audit_error(entry, "Process", message="bad thing")
        backend._audit_finalize(entry, "fail")
        self.assertEqual(entry.state, "fail")
        self.assertTrue(entry.error_ids)
        self.assertIn("bad thing", entry.error_ids.description)

    def test_backend_finalize_cancelled(self):
        integration = self._make_integration()
        backend = self.env["edi.audit.backend.edi_synchronization"]
        entry = backend._audit_start("sync-c", {"name": "sync-c", "integration_id": integration.id})
        backend._audit_finalize(entry, "cancelled")
        self.assertEqual(entry.state, "cancelled")

    def test_backend_flips_content_by_flow(self):
        backend = self.env["edi.audit.backend.edi_synchronization"]

        # inbound: input -> received, output -> sent
        inb = self._make_integration(flow="in")
        e_in = backend._audit_start("sync-in", {"name": "sync-in", "integration_id": inb.id})
        backend._audit_input(e_in, "the-call")
        backend._audit_output(e_in, "the-return")
        self.assertEqual(e_in.received_content, "the-call")
        self.assertEqual(e_in.sent_content, "the-return")

        # outbound: input -> sent, output -> received (flipped)
        outb = self._make_integration(flow="out")
        e_out = backend._audit_start("sync-out", {"name": "sync-out", "integration_id": outb.id})
        backend._audit_input(e_out, "the-call")
        backend._audit_output(e_out, "the-return")
        self.assertEqual(e_out.sent_content, "the-call")
        self.assertEqual(e_out.received_content, "the-return")

        # out_real is also outbound -> same flip as "out"
        outr = self._make_integration(flow="out_real")
        e_outr = backend._audit_start("sync-outr", {"name": "sync-outr", "integration_id": outr.id})
        backend._audit_input(e_outr, "the-call")
        backend._audit_output(e_outr, "the-return")
        self.assertEqual(e_outr.sent_content, "the-call")
        self.assertEqual(e_outr.received_content, "the-return")

    @mute_logger("odoo.models.unlink")
    def test_auditrun_drives_edi_synchronization_end_to_end(self):
        """AuditRun resolves the model backend, opens its own cursor, threads the
        entry (the sync record) through input/output, and commits independently."""
        # The integration must be committed so the isolated audit cursor can
        # FK-reference it.
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, self.env.context)
            integration_id = env["edi.integration"].create({
                "name": "AuditRun E2E Integration",
                "integration_flow": "in",
                "connection_id": env.ref("edi_base.api_connection").id,
                "type": "api",
                "synchronization_content_type": "json",
                "active": False,
            }).id
            cr.commit()

        with audit_run(
            self.env,
            "edi_synchronization",
            name="e2e-sync",
            metadata={"name": "e2e-sync", "integration_id": integration_id},
        ) as run:
            self.assertIsNot(run.env.cr, self.env.cr)  # isolated cursor opened
            run.input("in-data")
            run.output("out-data")  # exercises _audit_output
            self.assertEqual(run.record.integration_id.id, integration_id)  # entry is the sync record

        # committed on the isolated cursor -> visible from a fresh cursor
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, self.env.context)
            sync = env["edi.synchronization"].search([("integration_id", "=", integration_id)])
            self.assertEqual(len(sync), 1)
            self.assertEqual(sync.state, "done")
            self.assertEqual(sync.received_content, "in-data")
            self.assertEqual(sync.sent_content, "out-data")
