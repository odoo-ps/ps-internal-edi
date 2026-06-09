import ftplib
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from freezegun import freeze_time

from odoo import api
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.edi_base.tests.test_edi_common import TestEDICommonBase


class TestFTPBase(TransactionCase):
    """Shared helpers for FTP connection test classes."""

    def _make_mock_ctx(self, in_folder="/in", out_folder="/out", in_folder_done=None, in_folder_error=None):
        """Return (mock_ctx, server) usable as a context manager for connect()."""
        attrs = {"in_folder": in_folder, "out_folder": out_folder}
        if in_folder_done is not None:
            attrs["in_folder_done"] = in_folder_done
        if in_folder_error is not None:
            attrs["in_folder_error"] = in_folder_error
        server = SimpleNamespace(**attrs)
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = server
        return mock_ctx, server


@tagged("post_install", "-at_install")
class TestFTPFilename(TestFTPBase):
    """Unit tests for _ftp_is_valid_filename — no FTP server required."""

    def setUp(self):
        super().setUp()
        self.conn = self.env["edi.connection"]

    def test_dot_entries_are_invalid(self):
        self.assertFalse(self.conn._ftp_is_valid_filename("."))
        self.assertFalse(self.conn._ftp_is_valid_filename(".."))

    def test_file_without_extension_is_invalid(self):
        # rpartition(".") → ("", "", "noext") — fname is empty
        self.assertFalse(self.conn._ftp_is_valid_filename("noextension"))

    def test_hidden_file_is_invalid(self):
        # ".hidden" → rpartition → ("", ".", "hidden") — fname is empty
        self.assertFalse(self.conn._ftp_is_valid_filename(".hidden"))

    def test_backup_extensions_are_invalid(self):
        self.assertFalse(self.conn._ftp_is_valid_filename("file.bak"))
        self.assertFalse(self.conn._ftp_is_valid_filename("file.old"))
        self.assertFalse(self.conn._ftp_is_valid_filename("archive.tar.bak"))
        self.assertFalse(self.conn._ftp_is_valid_filename("archive.tar.old"))

    def test_regular_files_are_valid(self):
        self.assertTrue(self.conn._ftp_is_valid_filename("export.csv"))
        self.assertTrue(self.conn._ftp_is_valid_filename("data.txt"))
        self.assertTrue(self.conn._ftp_is_valid_filename("archive.tar.gz"))


@tagged("post_install", "-at_install")
class TestFTPFilterFiles(TestFTPBase):
    """Unit tests for _filter_files — no FTP server required."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conn = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Test FTP No Limit",
                    "type": "ftp",
                    "ftp_limit_in_files": 0,
                    "ftp_in_done_let": False,
                }
            )
        )
        # Separate connection for dedup tests (ftp_in_done_let=True created before
        # any integration is linked so the constraint passes at connection level)
        cls.conn_let = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Test FTP Let",
                    "type": "ftp",
                    "ftp_limit_in_files": 0,
                    "ftp_in_done_let": True,
                }
            )
        )
        # Integration for dedup tests: synchronization_creation=1 satisfies the
        # ftp_in_done_let constraint on both connection and integration side
        cls.integration = cls.env["edi.integration"].create(
            {
                "name": "FTP Dedup Integration",
                "type": "api",
                "integration_flow": "in",
                "connection_id": cls.conn_let.id,
                "synchronization_content_type": "csv",
                "synchronization_creation": 1,
                "active": False,
            }
        )

    def _mock_integration(self, content_type="csv"):
        m = MagicMock()
        m.synchronization_content_type = content_type
        return m

    # ------------------------------------------------------------------
    # Early exit
    # ------------------------------------------------------------------

    def test_empty_filenames_returns_empty(self):
        self.assertEqual(self.conn._filter_files([], self._mock_integration()), [])

    def test_no_integration_returns_filenames_unchanged(self):
        filenames = ["a.csv", "b.txt"]
        self.assertEqual(self.conn._filter_files(filenames, False), filenames)

    # ------------------------------------------------------------------
    # Extension filter
    # ------------------------------------------------------------------

    def test_extension_filter_keeps_matching_files(self):
        filenames = ["a.csv", "b.txt", "c.csv", "d.xml"]
        self.assertEqual(
            self.conn._filter_files(filenames, self._mock_integration("csv")),
            ["a.csv", "c.csv"],
        )

    def test_extension_filter_is_case_insensitive(self):
        filenames = ["A.CSV", "b.csv", "c.TXT"]
        self.assertEqual(
            self.conn._filter_files(filenames, self._mock_integration("csv")),
            ["A.CSV", "b.csv"],
        )

    # ------------------------------------------------------------------
    # Limit
    # ------------------------------------------------------------------

    def test_limit_truncates_after_extension_filter(self):
        conn = (
            self.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "FTP Limit 2", "type": "ftp", "ftp_limit_in_files": 2})
        )
        filenames = ["a.csv", "b.csv", "c.csv", "d.csv"]
        self.assertEqual(
            conn._filter_files(filenames, self._mock_integration("csv")),
            ["a.csv", "b.csv"],
        )

    def test_limit_zero_means_no_limit(self):
        filenames = ["a.csv", "b.csv", "c.csv"]
        self.assertEqual(
            self.conn._filter_files(filenames, self._mock_integration("csv")),
            filenames,
        )

    # ------------------------------------------------------------------
    # Dedup SQL (ftp_in_done_let=True)
    # ------------------------------------------------------------------

    def test_dedup_excludes_file_with_done_sync(self):
        self.env["edi.synchronization"].create(
            {
                "name": "file_1.csv",
                "integration_id": self.integration.id,
                "state": "done",
                "filename": "file_1.csv",
            }
        )
        result = self.conn_let._filter_files(["file_1.csv", "file_2.csv"], self.integration)
        self.assertEqual(result, ["file_2.csv"])

    def test_dedup_keeps_file_without_any_sync(self):
        result = self.conn_let._filter_files(["new_file.csv"], self.integration)
        self.assertEqual(result, ["new_file.csv"])

    def test_dedup_keeps_file_with_failed_sync(self):
        # A 'fail' sync must not exclude the file — it should be retried
        self.env["edi.synchronization"].create(
            {
                "name": "failed.csv",
                "integration_id": self.integration.id,
                "state": "fail",
                "filename": "failed.csv",
            }
        )
        result = self.conn_let._filter_files(["failed.csv"], self.integration)
        self.assertEqual(result, ["failed.csv"])


@tagged("post_install", "-at_install")
class TestFTPLoadConfig(TestFTPBase):
    """Unit tests for ftp_load_config() — folder setup and validation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conn = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Test FTP LoadConfig", "type": "ftp", "ftp_in_done_let": False})
        )
        cls.conn_let = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Test FTP LoadConfig Let", "type": "ftp", "ftp_in_done_let": True})
        )

    def _load_config(self, conn, config, existing_dirs=()):
        """Run ftp_load_config with pwd="/root/" and dir_exists restricted to existing_dirs."""
        server = SimpleNamespace()
        exists = set(existing_dirs)
        with patch.object(type(conn), "pwd", return_value="/root/"), patch.object(
            type(conn), "dir_exists", side_effect=lambda srv, p: p in exists
        ):
            conn.ftp_load_config(server, config)
        return server

    def test_absolute_paths_set_as_server_attributes(self):
        server = self._load_config(
            self.conn,
            {"in_folder": "/in", "out_folder": "/out", "in_folder_done": "/done"},
            ["/in", "/out", "/done"],
        )
        self.assertEqual(server.in_folder, "/in")
        self.assertEqual(server.out_folder, "/out")
        self.assertEqual(server.in_folder_done, "/done")
        self.assertFalse(hasattr(server, "in_folder_error"))

    def test_relative_path_resolved_against_pwd(self):
        server = self._load_config(
            self.conn,
            {"in_folder": "incoming", "out_folder": "/out"},
            ["/root/incoming", "/out"],
        )
        self.assertEqual(server.in_folder, "/root/incoming")

    def test_encoding_set_on_server(self):
        server = self._load_config(
            self.conn,
            {"in_folder": "/in", "out_folder": "/out", "encoding": "latin-1"},
            ["/in", "/out"],
        )
        self.assertEqual(server.encoding, "latin-1")

    def test_nonexistent_folder_raises(self):
        from odoo.exceptions import UserError

        server = SimpleNamespace()
        with patch.object(type(self.conn), "pwd", return_value="/"), patch.object(
            type(self.conn), "dir_exists", return_value=False
        ), self.assertRaises(UserError):
            self.conn.ftp_load_config(server, {"in_folder": "/missing", "out_folder": "/out"})

    def test_duplicate_path_raises(self):
        from odoo.exceptions import UserError

        server = SimpleNamespace()
        with patch.object(type(self.conn), "pwd", return_value="/"), patch.object(
            type(self.conn), "dir_exists", return_value=True
        ), self.assertRaises(UserError):
            self.conn.ftp_load_config(server, {"in_folder": "/same", "out_folder": "/same"})

    def test_in_done_let_with_in_folder_done_raises(self):
        from odoo.exceptions import UserError

        server = SimpleNamespace()
        with patch.object(type(self.conn_let), "pwd", return_value="/"), patch.object(
            type(self.conn_let), "dir_exists", return_value=True
        ), self.assertRaises(UserError):
            self.conn_let.ftp_load_config(server, {"in_folder": "/in", "out_folder": "/out", "in_folder_done": "/done"})


@tagged("post_install", "-at_install")
class TestFTPFileExists(TestFTPBase):
    """Unit tests for file_exists() — SIZE command with NLST fallback."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conn = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Test FTP FileExists", "type": "ftp"})
        )

    def _server(self, size_effect=None):
        """Return a mock FTP server with server.size() configured."""
        srv = MagicMock()
        if size_effect is not None:
            srv.size.side_effect = size_effect
        return srv

    def test_size_succeeds_returns_true(self):
        self.assertTrue(self.conn.file_exists(self._server(), "/out", "file.csv"))

    def test_size_550_file_absent_returns_false(self):
        """550 = command supported but file not found."""
        srv = self._server(size_effect=ftplib.error_perm("550 Not found"))
        self.assertFalse(self.conn.file_exists(srv, "/out", "file.csv"))

    def test_size_502_falls_back_to_list_files_found(self):
        """502 = SIZE not implemented → NLST fallback, file present."""
        srv = self._server(size_effect=ftplib.error_perm("502 Not implemented"))
        with patch.object(type(self.conn), "list_files", return_value=["file.csv"]):
            self.assertTrue(self.conn.file_exists(srv, "/out", "file.csv"))

    def test_size_500_also_falls_back_to_list_files(self):
        """500 (unrecognized) is treated the same as 502 — both trigger NLST fallback."""
        srv = self._server(size_effect=ftplib.error_perm("500 Unrecognized command"))
        with patch.object(type(self.conn), "list_files", return_value=["file.csv"]):
            self.assertTrue(self.conn.file_exists(srv, "/out", "file.csv"))

    def test_size_502_falls_back_to_list_files_not_found(self):
        srv = self._server(size_effect=ftplib.error_perm("502 Not implemented"))
        with patch.object(type(self.conn), "list_files", return_value=["other.csv"]):
            self.assertFalse(self.conn.file_exists(srv, "/out", "file.csv"))

    def test_size_502_list_files_raises_returns_false(self):
        srv = self._server(size_effect=ftplib.error_perm("502 Not implemented"))
        with patch.object(type(self.conn), "list_files", side_effect=Exception("NLST failed")):
            self.assertFalse(self.conn.file_exists(srv, "/out", "file.csv"))

    def test_unexpected_exception_returns_false(self):
        srv = self._server(size_effect=ConnectionError("timeout"))
        self.assertFalse(self.conn.file_exists(srv, "/out", "file.csv"))


@tagged("post_install", "-at_install")
class TestFTPClean(TestFTPBase):
    """Unit tests for _clean() routing — all branches, no real FTP server."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conn = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Test FTP Clean",
                    "type": "ftp",
                    "ftp_in_done_let": False,
                }
            )
        )
        cls.conn_let = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Test FTP Clean Let",
                    "type": "ftp",
                    "ftp_in_done_let": True,
                }
            )
        )

    # ------------------------------------------------------------------
    # Early returns — connect() must not be called
    # ------------------------------------------------------------------

    def test_in_any_status_let_skips_connect(self):
        """in + ftp_in_done_let=True: file stays on server regardless of status."""
        with patch.object(type(self.conn_let), "connect") as mock_connect:
            self.conn_let._clean("file.csv", "done", "in")
            self.conn_let._clean("file.csv", "fail", "in")
            mock_connect.assert_not_called()

    def test_out_done_skips_connect(self):
        """out/done: upload succeeded, nothing to clean up."""
        with patch.object(type(self.conn), "connect") as mock_connect:
            self.conn._clean("file.csv", "done", "out")
            mock_connect.assert_not_called()

    # ------------------------------------------------------------------
    # out/error
    # ------------------------------------------------------------------

    def test_out_error_deletes_uploaded_file_when_present(self):
        mock_ctx, server = self._make_mock_ctx(out_folder="/out")
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "file_exists", return_value=True
        ), patch.object(type(self.conn), "delete_file") as mock_del:
            self.conn._clean("file.csv", "fail", "out")
            mock_del.assert_called_once_with(server, "/out/file.csv")

    def test_out_error_skips_delete_when_file_not_on_server(self):
        mock_ctx, server = self._make_mock_ctx(out_folder="/out")
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "file_exists", return_value=False
        ), patch.object(type(self.conn), "delete_file") as mock_del:
            self.conn._clean("file.csv", "fail", "out")
            mock_del.assert_not_called()

    # ------------------------------------------------------------------
    # in/done
    # ------------------------------------------------------------------

    def test_in_done_moves_file_to_done_folder(self):
        mock_ctx, server = self._make_mock_ctx(in_folder="/in", in_folder_done="/done")
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "_manage_conflict"
        ) as mock_mc, patch.object(type(self.conn), "rename") as mock_rename:
            self.conn._clean("file.csv", "done", "in")
            mock_mc.assert_called_once_with(server, "/done", "file.csv")
            mock_rename.assert_called_once_with(server, "/in/file.csv", "/done/file.csv")

    def test_in_done_deletes_file_when_no_done_folder(self):
        mock_ctx, server = self._make_mock_ctx(in_folder="/in")  # no in_folder_done
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "delete_file"
        ) as mock_del, patch.object(type(self.conn), "rename") as mock_rename:
            self.conn._clean("file.csv", "done", "in")
            mock_del.assert_called_once_with(server, "/in/file.csv")
            mock_rename.assert_not_called()

    # ------------------------------------------------------------------
    # in/error
    # ------------------------------------------------------------------

    def test_in_error_moves_file_to_error_folder(self):
        mock_ctx, server = self._make_mock_ctx(in_folder="/in", in_folder_error="/error")
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "_manage_conflict"
        ) as mock_mc, patch.object(type(self.conn), "rename") as mock_rename:
            self.conn._clean("file.csv", "fail", "in")
            mock_mc.assert_called_once_with(server, "/error", "file.csv")
            mock_rename.assert_called_once_with(server, "/in/file.csv", "/error/file.csv")

    def test_in_error_leaves_file_when_no_error_folder(self):
        """No error folder configured: leave the file so it can be retried."""
        mock_ctx, server = self._make_mock_ctx(in_folder="/in")  # no in_folder_error
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "rename"
        ) as mock_rename, patch.object(type(self.conn), "delete_file") as mock_del:
            self.conn._clean("file.csv", "fail", "in")
            mock_rename.assert_not_called()
            mock_del.assert_not_called()


@tagged("post_install", "-at_install")
class TestFTPManageConflict(TestFTPBase):
    """Unit tests for _manage_conflict() — 5 cases, no real FTP server."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conn = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create({"name": "Test FTP Conflict", "type": "ftp"})
        )

    def test_no_conflict_returns_early(self):
        """File absent on server: nothing to do regardless of on_conflict setting."""
        server = SimpleNamespace()
        with patch.object(type(self.conn), "file_exists", return_value=False), patch.object(
            type(self.conn), "rename"
        ) as mock_rename, patch.object(type(self.conn), "delete_file") as mock_del:
            self.conn._manage_conflict(server, "/out", "file.csv")
            mock_rename.assert_not_called()
            mock_del.assert_not_called()

    @freeze_time("2024-01-01 12:00:00 UTC")
    def test_conflict_rename_with_custom_extension(self):
        self.conn.configuration = json.dumps({"on_conflict": "rename", "on_conflict_rename_extension": "bak"})
        server = SimpleNamespace()
        with patch.object(type(self.conn), "file_exists", return_value=True), patch.object(
            type(self.conn), "rename"
        ) as mock_rename:
            self.conn._manage_conflict(server, "/out", "file.csv")
            mock_rename.assert_called_once_with(server, "/out/file.csv", "/out/file.csv-20240101-120000-utc.bak")

    @freeze_time("2024-01-01 12:00:00 UTC")
    def test_conflict_rename_defaults_to_old_extension(self):
        # on_conflict_rename_extension absent → defaults to "old"
        self.conn.configuration = json.dumps({"on_conflict": "rename"})
        server = SimpleNamespace()
        with patch.object(type(self.conn), "file_exists", return_value=True), patch.object(
            type(self.conn), "rename"
        ) as mock_rename:
            self.conn._manage_conflict(server, "/out", "file.csv")
            mock_rename.assert_called_once_with(server, "/out/file.csv", "/out/file.csv-20240101-120000-utc.old")

    def test_conflict_replace_deletes_existing_file(self):
        self.conn.configuration = json.dumps({"on_conflict": "replace"})
        server = SimpleNamespace()
        with patch.object(type(self.conn), "file_exists", return_value=True), patch.object(
            type(self.conn), "delete_file"
        ) as mock_del:
            self.conn._manage_conflict(server, "/out", "file.csv")
            mock_del.assert_called_once_with(server, "/out/file.csv")

    def test_conflict_raise_raises_user_error(self):
        from odoo.exceptions import UserError

        self.conn.configuration = json.dumps({"on_conflict": "raise"})
        server = SimpleNamespace()
        with patch.object(type(self.conn), "file_exists", return_value=True):
            with self.assertRaises(UserError):
                self.conn._manage_conflict(server, "/out", "file.csv")


@tagged("post_install", "-at_install")
class TestFTPFlows(TestFTPBase):
    """Flow tests for _ftp_send_file and _ftp_fetch_files — ftplib mocked out."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conn = (
            cls.env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Test FTP Flows",
                    "type": "ftp",
                    "ftp_load_content": True,
                }
            )
        )

    # ------------------------------------------------------------------
    # _ftp_send_file
    # ------------------------------------------------------------------

    def test_send_renames_to_final_filename(self):
        """Upload goes through a .filepart temp name; final rename uses the original filename."""
        mock_ctx, server = self._make_mock_ctx(out_folder="/out")
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "change_dir"
        ), patch.object(type(self.conn), "_manage_conflict"), patch.object(
            type(self.conn), "_upload_file"
        ), patch.object(
            type(self.conn), "pwd", return_value="/out"
        ), patch.object(
            type(self.conn), "rename"
        ) as mock_rename:
            self.conn._ftp_send_file("report.csv", "col1,col2\nval1,val2")
            mock_rename.assert_called_once()
            _, src, dst = mock_rename.call_args[0]
            self.assertIn(".filepart", src)
            self.assertEqual(dst, "/out/report.csv")

    @mute_logger("odoo.addons.edi_ftp_connection.models.ftp_connection")
    def test_send_error_raises_user_error(self):
        from odoo.exceptions import UserError

        mock_ctx, server = self._make_mock_ctx(out_folder="/out")
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "change_dir"
        ), patch.object(type(self.conn), "_manage_conflict"), patch.object(
            type(self.conn), "_upload_file", side_effect=Exception("disk full")
        ):
            with self.assertRaises(UserError):
                self.conn._ftp_send_file("report.csv", "content")

    # ------------------------------------------------------------------
    # _ftp_fetch_files
    # ------------------------------------------------------------------

    def test_fetch_downloads_listed_files(self):
        """list_files result flows through _filter_files (pass-through) into _download_file."""
        mock_ctx, server = self._make_mock_ctx(in_folder="/in")
        downloaded = []

        def fake_download(conn_self, srv, directory, filename):
            path = os.path.join(directory, filename)
            downloaded.append(filename)
            return path

        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "list_files", return_value=["a.csv", "b.csv"]
        ), patch.object(type(self.conn), "_download_file", fake_download):
            self.conn.ftp_load_content = False
            result = self.conn._ftp_fetch_files()

        self.assertEqual([r["filename"] for r in result], ["a.csv", "b.csv"])
        self.assertEqual(downloaded, ["a.csv", "b.csv"])

    def test_fetch_loads_content_when_enabled(self):
        mock_ctx, server = self._make_mock_ctx(in_folder="/in")

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        tmp.write("hello,world\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)

        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "list_files", return_value=["data.csv"]
        ), patch.object(type(self.conn), "_download_file", lambda conn_self, srv, d, f: tmp.name):
            self.conn.ftp_load_content = True
            result = self.conn._ftp_fetch_files()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "hello,world\n")

    def test_fetch_skips_content_when_disabled(self):
        mock_ctx, server = self._make_mock_ctx(in_folder="/in")

        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "list_files", return_value=["data.csv"]
        ), patch.object(type(self.conn), "_download_file", lambda conn_self, srv, d, f: "/fake/data.csv"):
            self.conn.ftp_load_content = False
            result = self.conn._ftp_fetch_files()

        self.assertEqual(len(result), 1)
        self.assertNotIn("content", result[0])

    @mute_logger("odoo.addons.edi_ftp_connection.models.ftp_connection")
    def test_fetch_error_raises_user_error(self):
        from odoo.exceptions import UserError

        mock_ctx, server = self._make_mock_ctx(in_folder="/in")
        with patch.object(type(self.conn), "connect", return_value=mock_ctx), patch.object(
            type(self.conn), "list_files", return_value=["bad.csv"]
        ), patch.object(type(self.conn), "_download_file", side_effect=Exception("FTP timeout")):
            with self.assertRaises(UserError):
                self.conn._ftp_fetch_files()


@tagged("post_install", "-at_install")
class TestFTPIntegration(TestEDICommonBase):
    """End-to-end IN flow via process_integration() — ftplib mocked, real DB writes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ftp_conn = (
            cls.new_env["edi.connection"]
            .with_context(mail_create_nolog=True)
            .create(
                {
                    "name": "Test FTP Integration Conn",
                    "type": "ftp",
                    "ftp_in_done_let": True,  # _clean() returns early, no second connect()
                    "ftp_load_content": True,
                }
            )
        )
        cls.ftp_integration = cls.Integration.create(
            {
                "name": "Import Partner FTP",
                "type": "api",
                "integration_flow": "in",
                "synchronization_content_type": "csv",
                "connection_id": cls.ftp_conn.id,
                "synchronization_creation": 1,
                "store_received_content": True,
                "active": False,
            }
        )
        cls.ftp_out_integration = cls.Integration.create(
            {
                "name": "Export Partner FTP",
                "type": "api",
                "integration_flow": "out",
                "synchronization_content_type": "csv",
                "connection_id": cls.ftp_conn.id,
                "synchronization_creation": 1,
                "store_sent_content": True,
                "parameter": json.dumps({"filter": [["name", "=", "Partner FTP Out Test"]], "fields": ["name"]}),
                "active": False,
            }
        )
        cls.new_cr.commit()

    def setUp(self):
        super().setUp()
        self._csv_content = "name\nPartner FTP Test\n"
        self._downloaded_path = None

        server = SimpleNamespace(in_folder="/in")
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = server

        def fake_download(conn_self, srv, directory, filename):
            path = os.path.join(directory, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._csv_content)
            self._downloaded_path = path
            return path

        for name, kwargs in [
            ("connect", {"return_value": mock_ctx}),
            ("list_files", {"return_value": ["partner.csv"]}),
        ]:
            patcher = patch.object(type(self.ftp_conn), name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        download_patcher = patch.object(type(self.ftp_conn), "_download_file", fake_download)
        download_patcher.start()
        self.addCleanup(download_patcher.stop)
        self.addCleanup(self._cleanup_temp_file)
        self.addCleanup(self._clean_ftp_partners)

    def _cleanup_temp_file(self):
        if self._downloaded_path and os.path.exists(self._downloaded_path):
            os.unlink(self._downloaded_path)

    @mute_logger("odoo.models.unlink")
    def _clean_ftp_partners(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["res.partner"].search([("name", "=", "Partner FTP Test")]).unlink()

    def test_in_flow_creates_sync_and_imports_content(self):
        """process_integration() downloads CSV, creates a done sync, and imports the partner."""
        # autocommit=True forces _process_in_out() to open a real cursor and commit,
        # which is required in test mode (where _should_commit() returns False by default)
        self.ftp_integration.with_context(autocommit=True).process_integration()

        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)

            syncs = env["edi.synchronization"].search(
                [("integration_id", "=", self.ftp_integration.id), ("state", "=", "done")]
            )
            self.assertEqual(len(syncs), 1)
            self.assertEqual(syncs.filename, "partner.csv")
            self.assertEqual(syncs.received_content, self._csv_content)

            partners = env["res.partner"].search([("name", "=", "Partner FTP Test")])
            self.assertEqual(len(partners), 1)

        if self._downloaded_path is not None:
            self.assertFalse(
                os.path.exists(self._downloaded_path),
                "_clean_synchronization_in() should have deleted the local temp file",
            )

    @mute_logger("odoo.models.unlink")
    def _clean_ftp_out_partner(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            env["res.partner"].search([("name", "=", "Partner FTP Out Test")]).unlink()

    def test_out_flow_creates_sync_and_sends_content(self):
        """process_integration() exports partner as CSV and uploads it via FTP."""
        # Partner must be committed so the autocommit cursor in process_integration() can read it
        self.new_env["res.partner"].create({"name": "Partner FTP Out Test"})
        self.new_cr.commit()
        self.addCleanup(self._clean_ftp_out_partner)

        sent_files = []

        def fake_send(conn_self, filename, content, *args, **kwargs):
            sent_files.append((filename, content))

        with patch.object(type(self.ftp_conn), "_ftp_send_file", fake_send):
            self.ftp_out_integration.with_context(autocommit=True).process_integration()

        self.assertEqual(len(sent_files), 1)
        _, sent_content = sent_files[0]
        self.assertIn("Partner FTP Out Test", sent_content)

        with self.registry.cursor() as cr:
            env = api.Environment(cr, self.env.user.id, self.env.context)
            syncs = env["edi.synchronization"].search(
                [("integration_id", "=", self.ftp_out_integration.id), ("state", "=", "done")]
            )
            self.assertEqual(len(syncs), 1)
