from odoo.tests.common import tagged

from odoo.addons.edi_base.tests.test_edi_common import TestEDICommon


@tagged("post_install", "-at_install")
class TestFtpFilterFiles(TestEDICommon):
    """Tests for FTPConnection._filter_files()."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ftp_connection = (
            cls.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create([{"name": "Mock FTP connection", "type": "ftp", "ftp_in_done_let": True}])
        )
        cls.integration = cls.Integration.create(
            {
                "name": "Test FTP in integration",
                "type": "api",
                "integration_flow": "in",
                "connection_id": cls.ftp_connection.id,
                "synchronization_content_type": "csv",
                "synchronization_creation": 1,
                "active": False,
            }
        )
        cls.new_cr.commit()

    def test_filter_files_skips_done_files(self):
        """Files already processed with success are filtered out, the others keep the server order."""
        self.new_env["edi.synchronization"].create(
            {
                "name": "done sync",
                "filename": "done.csv",
                "integration_id": self.integration.id,
                "state": "done",
            }
        )
        self.new_cr.commit()

        result = self.ftp_connection._filter_files(
            ["new_b.csv", "done.csv", "new_a.csv", "other.xml"], self.integration
        )

        self.assertEqual(result, ["new_b.csv", "new_a.csv"])

    def test_filter_files_special_characters(self):
        """Filenames with a quote or a percent sign are matched and returned unchanged."""
        # leave new_cr usable for the cleanups if the query fails
        self.addCleanup(self.new_cr.rollback)
        self.new_env["edi.synchronization"].create(
            {
                "name": "done sync",
                "filename": "rapport d'avril.csv",
                "integration_id": self.integration.id,
                "state": "done",
            }
        )
        self.new_cr.commit()

        result = self.ftp_connection._filter_files(
            ["rapport d'avril.csv", "rapport d'été.csv", "remise 50%.csv"], self.integration
        )

        self.assertEqual(result, ["rapport d'été.csv", "remise 50%.csv"])
