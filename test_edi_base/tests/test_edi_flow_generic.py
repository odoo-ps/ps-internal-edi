# Part of Odoo. See LICENSE file for full copyright and licensing details.
from pathlib import Path

from odoo import api
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from odoo.addons.edi_base.tests.test_edi_common import (  # FOLDER_OUT,
    FOLDER_EDI,
    FOLDER_IN,
    FOLDER_IN_DONE,
    FOLDER_IN_ERROR,
    TestEDICommonBase,
)


FILE_IN = Path(FOLDER_IN, "test_partner.csv")


@tagged("edi_flow_generic")
class TestEdiFlowGeneric(TestEDICommonBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.Integration = cls.new_env["edi.integration"]
        cls.Partner = cls.new_env["res.partner"]
        cls.Country = cls.new_env["res.country"]

        # Setup the integration
        cls.integration = cls.Integration.with_context(autocommit=True).create(
            {
                "name": "Generic Import Partner",
                "type": "generic",
                "integration_flow": "in",
                "synchronization_content_type": "csv",
                "connection_id": cls.folder_connection.id,
                "binding_model_id": cls.env.ref("base.model_res_partner").id,
                "csv_delimiter": ",",
                "csv_quotechar": '"',
                "active": False,
            }
        )

        # Setup Mapping
        cls.new_env["edi.import.mapping"].create(
            [
                {
                    "integration_id": cls.integration.id,
                    "sequence": 1,
                    "column_name": "unique",
                    "field_id": cls.env.ref("base.field_res_partner__ref").id,
                    "is_identifier": True,
                },
                {
                    "integration_id": cls.integration.id,
                    "sequence": 2,
                    "column_name": "nom",
                    "field_id": cls.env.ref("base.field_res_partner__name").id,
                },
                {
                    "integration_id": cls.integration.id,
                    "sequence": 3,
                    "column_name": "mail",
                    "field_id": cls.env.ref("base.field_res_partner__email").id,
                },
                {
                    "integration_id": cls.integration.id,
                    "sequence": 4,
                    "column_name": "country",
                    "field_id": cls.env.ref("base.field_res_partner__country_id").id,
                    "lookup_method": "field",
                    "lookup_field_id": cls.env.ref("base.field_res_country__code").id,
                    "on_lookup_failure": "warning",
                },
            ]
        )
        cls.new_cr.commit()

    def setUp(self):
        super().setUp()
        FOLDER_IN.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._clean_fs)
        self.addCleanup(self._clean_partners)

    @mute_logger("odoo.models.unlink")
    def _clean_partners(self):
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            new_env["res.partner"].search([("ref", "in", ["test1", "test2", "test3"])]).unlink()

    def _clean_fs(self):
        FILE_IN.unlink(missing_ok=True)
        Path(FOLDER_IN_DONE, "test_partner.csv").unlink(missing_ok=True)
        Path(FOLDER_IN_ERROR, "test_partner.csv").unlink(missing_ok=True)
        if FOLDER_IN_DONE.exists():
            FOLDER_IN_DONE.rmdir()
        if FOLDER_IN_ERROR.exists():
            FOLDER_IN_ERROR.rmdir()
        if FOLDER_IN.exists():
            FOLDER_IN.rmdir()
        if FOLDER_EDI.exists():
            FOLDER_EDI.rmdir()

    def test_import_generic_csv_with_lookup(self):
        """Test a generic CSV import with identifier and advanced lookup"""

        csv_content = (
            "unique,nom,mail,country\n"
            "test1,partner1,partner1@odoo.com,BE\n"
            "test2,partner2,partner2@odoo.com,YY\n"
            "test3,partner3,partner3@odoo.com,XX\n"
        )

        with open(FILE_IN, "w") as f:
            f.write(csv_content)

        self.integration.process_integration()

        # New cursor to check results committed by the integration
        with self.registry.cursor() as new_cr:
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)

            # Check partners creation
            p1 = new_env["res.partner"].search([("ref", "=", "test1")])
            p2 = new_env["res.partner"].search([("ref", "=", "test2")])
            p3 = new_env["res.partner"].search([("ref", "=", "test3")])

            country_be = new_env.ref("base.be")

            self.assertEqual(len(p1), 1)
            self.assertEqual(p1.name, "partner1")
            self.assertEqual(p1.email, "partner1@odoo.com")
            self.assertEqual(p1.country_id.id, country_be.id, "Country BE should be found")

            self.assertEqual(len(p2), 1)
            self.assertEqual(p2.name, "partner2")
            self.assertFalse(p2.country_id, "Country YY does not exist, should be empty")

            self.assertEqual(len(p3), 1)
            self.assertEqual(p3.name, "partner3")
            self.assertFalse(p3.country_id, "Country XX does not exist, should be empty")

            # Check Warning in Chatter
            integration = new_env["edi.integration"].browse(self.integration.id)
            sync = integration.synchronization_ids[0]
            self.assertEqual(sync.state, "done")

            messages = sync.message_ids
            # We expect a note about warnings
            warning_message = messages.filtered(lambda m: "Import Warnings" in m.body)
            self.assertTrue(warning_message, "A warning message should be posted in chatter")
            self.assertIn("XX", warning_message.body)
            self.assertIn("YY", warning_message.body)
