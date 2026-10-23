from datetime import timedelta

from odoo import fields
from odoo.modules.migration import load_script
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import file_path, mute_logger


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
            {"name": "sync %s" % state, "state": state, "integration_id": self.integration.id, "content": "payload"}
        )
        if description:
            self.env["edi.synchronization.error"].create(
                {"synchronization_id": synchronization.id, "activity": "Test", "description": description}
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

    def _run_cancel_migration(self):
        """Run edi_archiving's 17.0.2.0.0 pre-migration on the test cursor, as an upgrade would."""
        script = load_script(
            file_path("edi_archiving/migrations/17.0.2.0.0/pre-migration.py"),
            "edi_archiving_pre_17_0_2_0_0",
        )
        self.env.flush_all()
        script.migrate(self.env.cr, "17.0.1.0.0")
        self.env.invalidate_all()

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

        self.assertFalse(synchronization.active, "a cancelled synchronization past the delay must be archived")
        self.assertFalse(synchronization.content, "archiving must release the stored content")

    def test_states_not_configured_are_left_alone(self):
        self.config.set_param("edi.archive.duration", "90")
        self.config.set_param("edi.archive.state.cancelled", "True")
        failed = self._create_synchronization("fail", days_old=200)

        self.Synchronization._archive_outdated_synchronizations()

        self.assertTrue(failed.active, "a state that was not configured must not be archived")
        self.assertEqual(failed.content, "payload")

    @mute_logger("odoo.models.unlink")
    def test_delete_uses_its_own_duration(self):
        self.config.set_param("edi.archive.duration.delete", "365")
        self.config.set_param("edi.archive.state.done", "True")
        recent = self._create_synchronization("done", days_old=200)
        old = self._create_synchronization("done", days_old=400)

        self.Synchronization._delete_outdated_synchronizations()

        self.assertTrue(recent.exists(), "within the delete delay, the synchronization must survive")
        self.assertFalse(old.exists(), "past the delete delay, the synchronization must be deleted")

    @mute_logger("odoo.models.unlink")
    def test_errors_deleted_only_when_configured(self):
        self.config.set_param("edi.archive.duration", "90")
        self.config.set_param("edi.archive.state.done", "True")

        self.config.set_param("edi.archive.delete_errors", "True")
        archived = self._create_synchronization("done", days_old=200, description="a traceback")
        error = archived.error_ids
        self.Synchronization._archive_outdated_synchronizations()
        self.assertFalse(archived.active, "the synchronization itself must be archived")
        self.assertFalse(error.exists(), "its errors must be deleted when configured")

        # set_param(key, False) removes the parameter, which is how the settings screen stores an
        # unticked box -- storing the string "False" would read back as truthy
        self.config.set_param("edi.archive.delete_errors", False)
        kept = self._create_synchronization("done", days_old=200, description="a traceback")
        self.Synchronization._archive_outdated_synchronizations()
        self.assertEqual(
            kept.error_ids.description, "a traceback", "without the setting, the errors are kept as they are"
        )

    def test_cancel_box_survives_the_upgrade(self):
        """The Cancelled box keeps its value across the rename of its parameter.

        It used to be stored as edi.archive.state.cancel. Without the pre-migration, an installation
        that had ticked it found it unticked after the upgrade -- and, with a duration and no other
        state ticked, could not save any settings screen anymore.
        """
        self.config.search([("key", "=like", "edi.archive.%")]).unlink()
        self.config.set_param("edi.archive.state.cancel", "True")

        self._run_cancel_migration()

        self.assertEqual(
            self._edi_parameters(),
            {"edi.archive.state.cancelled": "True"},
            "a ticked Cancel box must still be ticked after the upgrade",
        )

    def test_cancel_migration_keeps_an_existing_cancelled_key(self):
        """Both keys present: the old one goes, the new one stays, and the unique key holds."""
        self.config.search([("key", "=like", "edi.archive.%")]).unlink()
        self.config.set_param("edi.archive.state.cancel", "True")
        self.config.set_param("edi.archive.state.cancelled", "True")

        self._run_cancel_migration()

        self.assertEqual(self._edi_parameters(), {"edi.archive.state.cancelled": "True"})
