from odoo import api
from odoo.tests.common import RecordCapturer, tagged
from odoo.tools import mute_logger

from odoo.addons.edi_base.tests.test_edi_common import TestEDICommon


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestAuditDecorator(TestEDICommon):
    def test_audit_light_path_creates_no_integration_no_sync(self):
        Integration = self.env["edi.integration"].with_context(active_test=False)
        Sync = self.env["edi.synchronization"]
        with RecordCapturer(Integration, []) as integ_cap, RecordCapturer(Sync, []) as sync_cap, self.assertLogs(
            "odoo.addons.edi_audit.audit", level="INFO"
        ) as log_cap:
            result = self.env["res.partner"].audit_probe_ok("hi")
        self.assertEqual(result, "hi")
        self.assertFalse(integ_cap.records, "no edi.integration should be created")
        self.assertFalse(sync_cap.records, "no edi.synchronization should be created")
        self.assertIn("Audit Probe", "\n".join(log_cap.output))  # explicit name in the run label
        self.assertIn("audit_probe_ok", "\n".join(log_cap.output))  # method shows in the call description

    def test_stacked_integration_and_audit_fans_out(self):
        """Stacking @integration + @audit records the same call to BOTH the sync and the log.

        The dynamically-created "Stacked Probe" integration and its sync are cleaned up by
        TestEDICommon's _clean_integrations / _clean_synchronizations.
        """
        with self.assertLogs("odoo.addons.edi_audit.audit", level="INFO") as log_cap, mute_logger(
            "odoo.addons.edi_base.models.decorator"
        ):
            result = self.env["res.partner"].stacked_probe("hi")
        self.assertEqual(result, "hi")
        # @audit('python_log') emitted a breadcrumb (name defaulted to the method name)
        self.assertIn("stacked_probe", "\n".join(log_cap.output))
        # @integration committed a sync on its own cursor -> read it on a fresh cursor
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, self.env.context)
            integration = env["edi.integration"].with_context(active_test=False).search([("name", "=", "Stacked Probe")])
            self.assertEqual(len(integration), 1)
            syncs = env["edi.synchronization"].search([("integration_id", "=", integration.id)])
            self.assertEqual(len(syncs), 1)
            self.assertEqual(syncs.state, "done")
            self.assertTrue(syncs.received_content)

    @mute_logger("odoo.addons.edi_audit.audit")
    def test_audit_error_reraises_and_creates_nothing(self):
        Integration = self.env["edi.integration"].with_context(active_test=False)
        Sync = self.env["edi.synchronization"]
        with RecordCapturer(Integration, []) as integ_cap, RecordCapturer(Sync, []) as sync_cap:
            with self.assertRaisesRegex(ValueError, "probe boom"):
                self.env["res.partner"].audit_probe_fail()
        self.assertFalse(integ_cap.records)
        self.assertFalse(sync_cap.records)

    def test_audit_captures_return_value(self):
        """@audit records the return value as the output half of the run."""
        with self.assertLogs("odoo.addons.edi_audit.audit", level="INFO") as log_cap:
            result = self.env["res.partner"].audit_probe_ok("hi")
        self.assertEqual(result, "hi")
        joined = "\n".join(log_cap.output)
        self.assertIn("output:", joined)  # the output half was emitted
        self.assertIn("Result", joined)   # _describe_result label
        self.assertIn("'hi'", joined)      # the actual returned value

    def test_integration_records_return_as_sent_content(self):
        """Inbound @integration now fills sent_content with the return (was empty)."""
        with self.assertLogs("odoo.addons.edi_audit.audit", level="INFO"), mute_logger(
            "odoo.addons.edi_base.models.decorator"
        ):
            self.env["res.partner"].stacked_probe("hi")
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, self.env.context)
            integration = env["edi.integration"].with_context(active_test=False).search([("name", "=", "Stacked Probe")])
            sync = env["edi.synchronization"].search([("integration_id", "=", integration.id)])
            self.assertTrue(sync.received_content)         # call description (unchanged)
            self.assertIn("Result", sync.sent_content)     # return description (new)
            self.assertIn("'hi'", sync.sent_content)

    def test_integration_flow_out_creates_out_sync_and_flips(self):
        # outbound_probe is a bare @integration (no @audit stacked), so unlike
        # stacked_probe it never logs to "odoo.addons.edi_audit.audit" (that's
        # python_log's logger); only the decorator's own creation-log needs muting.
        with mute_logger("odoo.addons.edi_base.models.decorator"):
            result = self.env["res.partner"].outbound_probe("hi")
        self.assertEqual(result, "hi")
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, self.env.context)
            integration = env["edi.integration"].with_context(active_test=False).search([("name", "=", "Outbound Probe")])
            self.assertEqual(integration.integration_flow, "out")
            sync = env["edi.synchronization"].search([("integration_id", "=", integration.id)])
            self.assertEqual(len(sync), 1)
            # flipped: the call went out (sent), the return came back (received)
            self.assertIn("outbound_probe", sync.sent_content)
            self.assertIn("Result", sync.received_content)
