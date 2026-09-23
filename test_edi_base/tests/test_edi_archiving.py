from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.edi_archiving.hooks import post_init_hook, pre_init_hook


@tagged("post_install", "-at_install", "ps_internal_edi")
class TestEdiArchiving(TransactionCase):
    """Retention behaviour of edi_archiving: which states it acts on, and what it clears."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Synchronization = cls.env["edi.synchronization"]
        cls.config = cls.env["ir.config_parameter"].sudo()
        cls.integration = cls.env["edi.integration"].create(
            {
                "name": "Archiving Test",
                "type": "api",
                "integration_flow": "in",
                "connection_id": cls.env.ref("edi_base.api_connection").id,
                "active": False,
            }
        )

    def _create_synchronization(self, state, days_old, description=None):
        """Create a synchronization that looks `days_old` days old to the vacuum."""
        synchronization = self.Synchronization.create(
            {
                "name": "sync %s" % state,
                "state": state,
                "integration_id": self.integration.id,
                "received_content": "payload",
            }
        )
        if description:
            self.env["edi.synchronization.error"].create(
                {
                    "synchronization_id": synchronization.id,
                    "activity": "Test",
                    "description": description,
                }
            )
        # create_date drives the vacuum and cannot be written through the ORM
        self.env.cr.execute(
            "UPDATE edi_synchronization SET create_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(days=days_old), synchronization.id],
        )
        synchronization.invalidate_recordset(["create_date"])
        return synchronization

    def _edi_parameters(self):
        return {param.key: param.value for param in self.config.search([("key", "=like", "edi.archive.%")])}

    def test_archive_states_exist_on_the_model(self):
        """Every state offered for archiving must be a real state of edi.synchronization.

        _archive_states() used to return "cancel" while the selection value is "cancelled", so
        ticking Cancelled in the settings built a domain that matched nothing and the records were
        never vacuumed -- silently, since an empty result is indistinguishable from "nothing due".
        """
        selection_values = dict(self.Synchronization._fields["state"].selection)
        self.assertEqual(
            set(self.Synchronization._archive_states()) - set(selection_values),
            set(),
            "_archive_states() offers a state that edi.synchronization does not have",
        )

    def test_cancelled_synchronizations_are_archived(self):
        self.config.set_param("edi.archive.duration", "90")
        self.config.set_param("edi.archive.state.cancelled", "True")
        synchronization = self._create_synchronization("cancelled", days_old=200)

        self.Synchronization._archive_outdated_synchronizations()

        self.assertFalse(
            synchronization.active,
            "a cancelled synchronization past the delay must be archived",
        )
        self.assertFalse(
            synchronization.received_content,
            "archiving must release the stored content",
        )

    def test_states_not_configured_are_left_alone(self):
        self.config.set_param("edi.archive.duration", "90")
        self.config.set_param("edi.archive.state.cancelled", "True")
        failed = self._create_synchronization("fail", days_old=200)

        self.Synchronization._archive_outdated_synchronizations()

        self.assertTrue(failed.active, "a state that was not configured must not be archived")
        self.assertEqual(failed.received_content, "payload")

    @mute_logger("odoo.models.unlink")
    def test_delete_uses_its_own_duration(self):
        self.config.set_param("edi.archive.duration.delete", "365")
        self.config.set_param("edi.archive.state.done", "True")
        recent = self._create_synchronization("done", days_old=200)
        old = self._create_synchronization("done", days_old=400)

        self.Synchronization._delete_outdated_synchronizations()

        self.assertTrue(recent.exists(), "within the delete delay, the synchronization must survive")
        self.assertFalse(old.exists(), "past the delete delay, the synchronization must be deleted")

    def test_error_description_cleared_only_when_configured(self):
        self.config.set_param("edi.archive.duration", "90")
        self.config.set_param("edi.archive.state.done", "True")

        self.config.set_param("edi.archive.clear_error_description", "True")
        cleared = self._create_synchronization("done", days_old=200, description="a traceback")
        self.Synchronization._archive_outdated_synchronizations()
        self.assertFalse(
            cleared.error_ids.description,
            "the description must be released when configured",
        )
        self.assertTrue(cleared.error_ids, "the error row itself must be kept")

        # set_param(key, False) removes the parameter, which is how the settings screen stores an
        # unticked box -- storing the string "False" would read back as truthy
        self.config.set_param("edi.archive.clear_error_description", False)
        kept = self._create_synchronization("done", days_old=200, description="a traceback")
        self.Synchronization._archive_outdated_synchronizations()
        self.assertEqual(
            kept.error_ids.description,
            "a traceback",
            "without the setting, nothing is cleared",
        )

    def test_post_init_hook_leaves_a_database_with_history_alone(self):
        """The guard that keeps this shared module from switching retention on at an existing client.

        edi_archiving is auto_install and force-installed by an edi_base migration, so the hook also
        runs on databases that have been logging EDI for years. It must configure nothing there: the
        defaults would enable deletion of an audit trail nobody agreed to give up.
        """
        self._create_synchronization("done", days_old=1)
        # a configuration of the client's own, different from the defaults on every key
        self.config.search([("key", "=like", "edi.archive.%")]).unlink()
        self.config.set_param("edi.archive.duration", "30")
        before = self._edi_parameters()

        post_init_hook(self.env)

        self.assertEqual(
            self._edi_parameters(),
            before,
            "an installation with history keeps its own retention",
        )

    @mute_logger("odoo.models.unlink")
    def test_post_init_hook_configures_a_database_without_history(self):
        """The other half: a database with nothing to lose gets a retention policy out of the box.

        The expected policy is spelled out here rather than compared against NEW_INSTALL_PARAMETERS,
        which would only restate the hook to itself. These five values are the documented contract:
        changing one is a decision, and it should break a test and be re-taken, not slip through.
        """
        self.Synchronization.with_context(active_test=False).search([]).unlink()
        self.config.search([("key", "=like", "edi.archive.%")]).unlink()

        post_init_hook(self.env)

        self.assertEqual(
            self._edi_parameters(),
            {
                "edi.archive.duration": "90",
                "edi.archive.duration.delete": "365",
                "edi.archive.state.done": "True",
                "edi.archive.state.cancelled": "True",
                "edi.archive.clear_error_description": "True",
            },
            "a fresh installation must archive after 90 days and delete after 365, for done and cancelled",
        )
        self.assertNotIn(
            "edi.archive.state.fail",
            self._edi_parameters(),
            "a failure is often still needed weeks later: it must not be enabled by default",
        )
        self.assertNotIn(
            "edi.archive.state.new",
            self._edi_parameters(),
            "a synchronization stuck in new is a symptom, not something to delete automatically",
        )

    def test_pre_init_hook_leaves_an_existing_column_alone(self):
        """Re-running the hook must not touch a table that already has the column.

        The `column_exists` guard is what makes the hook safe to re-enter, and dropping the column
        default is what keeps the installed schema identical to what the ORM would have produced.
        """
        archived = self._create_synchronization("done", days_old=1)
        archived.active = False

        pre_init_hook(self.env)

        archived.invalidate_recordset(["active"])
        self.assertFalse(
            archived.active,
            "the hook must not rewrite a table whose column already exists",
        )
        self.env.cr.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'edi_synchronization' AND column_name = 'active'"
        )
        self.assertIsNone(self.env.cr.fetchone()[0], "the column default must not survive the install")
