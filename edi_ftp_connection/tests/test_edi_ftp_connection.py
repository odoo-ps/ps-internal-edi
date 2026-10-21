# Part of Odoo. See LICENSE file for full copyright and licensing details.
import ftplib
import posixpath
from datetime import UTC, datetime
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.base.tests.common import BaseCommon
from odoo.addons.edi_base.tools import EdiTransportError


class FakeFTP:
    """In-memory FTP server, enough for the ftplib calls of the transport."""

    files = {}  # path: bytes, shared by all instances (= the server), reset by the tests
    dates = {}  # path: modification time (YYYYMMDDHHMMSS), optional
    folders = set()
    mlsd_supported = True
    instances = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        FakeFTP.instances.append(self)

    def _call(self, *args):
        self.calls.append(args)

    def connect(self, host, port):
        self._call("connect", host, port)

    def login(self, user="", passwd=""):
        self._call("login", user, passwd)
        if passwd == "wrong":
            raise ftplib.error_perm("530 Login incorrect.")

    def prot_p(self):
        self._call("prot_p")

    def set_pasv(self, value):
        self._call("set_pasv", value)

    def voidcmd(self, cmd):
        self._call("voidcmd", cmd)
        if cmd.startswith("MDTM "):
            path = cmd.removeprefix("MDTM ")
            if path not in self.dates:
                raise ftplib.error_perm("550 Could not get file modification time.")
            return f"213 {self.dates[path]}"
        return "200 OK"

    def close(self):
        self._call("close")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _children(self, path):
        return [p for p in self.files if posixpath.dirname(p) == path], [
            f for f in self.folders if posixpath.dirname(f) == path
        ]

    def mlsd(self, path, facts=()):
        if not self.mlsd_supported:
            raise ftplib.error_perm("500 Unknown command.")
        files, folders = self._children(path)
        for p in files:
            facts = {"type": "file", "size": str(len(self.files[p]))}
            if p in self.dates:
                facts["modify"] = self.dates[p]
            yield posixpath.basename(p), facts
        yield from ((posixpath.basename(f), {"type": "dir"}) for f in folders)

    def nlst(self, path):
        files, folders = self._children(path)
        if not files and not folders:
            raise ftplib.error_perm("550 No files found.")
        return files + folders  # full paths, like some servers do

    def size(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 Could not get file size.")
        return len(self.files[path])

    def retrbinary(self, cmd, callback):
        callback(self.files[cmd.removeprefix("RETR ")])

    def storbinary(self, cmd, file):
        path = cmd.removeprefix("STOR ")
        if posixpath.dirname(path) not in self.folders:
            raise ftplib.error_perm("553 Could not create file.")
        self.files[path] = file.read()

    def rename(self, path, new_path):
        self._call("rename", path, new_path)
        self.files[new_path] = self.files.pop(path)

    def delete(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 No such file.")
        self.files.pop(path)


class FakeFTP_TLS(FakeFTP):
    def __init__(self, *args, context=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.context = context


@tagged("post_install", "-at_install")
class TestEdiFtpConnection(BaseCommon):
    _test_user_groups = ("base.group_system",)  # connections are managed by administrators only

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.env["edi.connection"].create(
            {
                "name": "ACME FTP",
                "transport": "ftp",
                "url": "ftp://files.example.com:2121/acme",
                "auth_type": "basic",
                "username": "odoo",
                "password": "secret",
            }
        )
        cls.startClassPatcher(patch.object(ftplib, "FTP", FakeFTP))
        cls.startClassPatcher(patch.object(ftplib, "FTP_TLS", FakeFTP_TLS))

    def setUp(self):
        super().setUp()
        FakeFTP.files = {
            "/acme/in/b_order.csv": b"b",
            "/acme/in/a_order.csv": b"a",
            "/acme/in/c_order.xml": b"c",
            "/acme/in/done/old.csv": b"old",
        }
        FakeFTP.folders = {"/acme", "/acme/in", "/acme/in/done", "/acme/in/error", "/acme/out"}
        FakeFTP.dates = {}
        FakeFTP.mlsd_supported = True
        FakeFTP.instances = []

    def test_connect(self):
        with self.connection._edi_connect() as session:
            self.assertIsInstance(session, FakeFTP)
            self.assertNotIsInstance(session, FakeFTP_TLS)
        self.assertEqual(
            session.calls,
            [
                ("connect", "files.example.com", 2121),
                ("login", "odoo", "secret"),
                ("set_pasv", True),
                ("voidcmd", "TYPE I"),
                ("close",),
            ],
        )

    def test_connect_ftps_anonymous_active(self):
        self.connection.write({"url": "ftps://files.example.com", "auth_type": "none", "ftp_passive": False})
        with self.connection._edi_connect() as session:
            self.assertIsInstance(session, FakeFTP_TLS)
        self.assertEqual(
            session.calls[:4],
            [("connect", "files.example.com", 21), ("login", "", ""), ("prot_p",), ("set_pasv", False)],
        )

    def test_connect_refused(self):
        self.connection.password = "wrong"
        with self.assertRaisesRegex(EdiTransportError, "530"):
            self.connection._edi_connect()
        self.assertEqual(FakeFTP.instances[0].calls[-1], ("close",))
        with self.assertRaises(Exception):  # noqa: B017 the button turns it into a UserError
            self.connection.action_test()

    def test_auth_types(self):
        for auth_type in ("api_key", "oauth2", "ssh_key", "custom"):
            with self.subTest(auth_type=auth_type), self.assertRaises(ValidationError):
                self.connection.auth_type = auth_type
        with self.assertRaises(ValidationError):
            self.connection.url = "sftp://files.example.com"

    def test_client_certificate_needs_tls(self):
        self.assertTrue(self.connection.tls_client_auth_supported)
        with self.assertRaisesRegex(ValidationError, "ftps://"):
            self.connection.tls_client_auth = True

    def test_receive(self):
        self.connection.file_processed_action = "keep"  # oldest first: by name when there is no date
        with self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "in")
            self.assertEqual([m.name for m in messages], ["a_order.csv", "b_order.csv", "c_order.xml"])
            self.assertEqual(messages[0].content, b"a")
            self.assertEqual(messages[0].ref, "/acme/in/a_order.csv")
            self.assertEqual(messages[0].meta["size"], 1)

            messages = self.connection._edi_receive(session, "/in/", pattern="*.csv", limit=1)
            self.assertEqual([m.name for m in messages], ["a_order.csv"])

    def test_receive_most_recent_first(self):
        FakeFTP.dates = {
            "/acme/in/a_order.csv": "20261020120000",
            "/acme/in/b_order.csv": "20261021120000.123",
            "/acme/in/c_order.xml": "20261019120000",
        }
        for mlsd_supported in (True, False):  # dates from MLSD, or from MDTM
            FakeFTP.mlsd_supported = mlsd_supported
            with self.subTest(mlsd=mlsd_supported), self.connection._edi_connect() as session:
                # processed files are moved (default): most recent first
                messages = self.connection._edi_receive(session, "in")
                self.assertEqual([m.name for m in messages], ["b_order.csv", "a_order.csv", "c_order.xml"])
                self.assertEqual(messages[0].meta["modify"], datetime(2026, 10, 21, 12, tzinfo=UTC))
                limited = self.connection._edi_receive(session, "in", pattern="*.csv", limit=1)
                self.assertEqual([m.name for m in limited], ["b_order.csv"])

    def test_receive_oldest_first_when_files_stay(self):
        FakeFTP.dates = {
            "/acme/in/a_order.csv": "20261020120000",
            "/acme/in/b_order.csv": "20261021120000",
            "/acme/in/c_order.xml": "20261019120000",
        }
        self.connection.file_processed_action = "keep"
        with self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "in", limit=2)
            self.assertEqual([m.name for m in messages], ["c_order.xml", "a_order.csv"])
            # next run: after the last processed file, not the same oldest files again
            last_date = messages[-1].meta["modify"]
            messages = self.connection._edi_receive(session, "in", limit=2, modified_after=last_date)
            self.assertEqual([m.name for m in messages], ["b_order.csv"])

    def test_receive_without_mlsd(self):
        FakeFTP.mlsd_supported = False
        with self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "in")
            self.assertEqual([m.name for m in messages], ["c_order.xml", "b_order.csv", "a_order.csv"])
            self.assertEqual(self.connection._edi_receive(session, "in/error"), [])

    def test_send(self):
        with self.connection._edi_connect() as session:
            answer = self.connection._edi_send(session, "out", b"<order/>", name="order.xml")
        self.assertIsNone(answer)
        self.assertEqual(FakeFTP.files["/acme/out/order.xml"], b"<order/>")
        # uploaded under a temporary name, then renamed
        (_rename, uploading_path, path) = next(c for c in session.calls if c[0] == "rename")
        self.assertTrue(uploading_path.startswith("/acme/out/order.xml.") and uploading_path.endswith(".filepart"))
        self.assertEqual(path, "/acme/out/order.xml")

    @mute_logger("odoo.addons.edi_base.models.edi_connection")  # the partial upload cannot be removed
    def test_send_errors(self):
        with self.connection._edi_connect() as session:
            with self.assertRaises(EdiTransportError):  # no name
                self.connection._edi_send(session, "out", b"x")
            with self.assertRaisesRegex(EdiTransportError, "553"):  # no such folder
                self.connection._edi_send(session, "nowhere", b"x", name="x.csv")

    def test_conflicts(self):
        FakeFTP.files["/acme/out/order.csv"] = b"previous"
        with self.connection._edi_connect() as session:
            with self.assertRaisesRegex(EdiTransportError, "already exists"):
                self.connection._edi_send(session, "out", b"new", name="order.csv")
            self.assertEqual(FakeFTP.files["/acme/out/order.csv"], b"previous")

            self.connection.file_on_conflict = "replace"
            self.connection._edi_send(session, "out", b"new", name="order.csv")
            self.assertEqual(FakeFTP.files["/acme/out/order.csv"], b"new")

            self.connection.file_on_conflict = "rename"
            self.connection._edi_send(session, "out", b"newer", name="order.csv")
        self.assertEqual(FakeFTP.files["/acme/out/order.csv"], b"newer")
        (renamed,) = [p for p in FakeFTP.files if p.startswith("/acme/out/order.csv-")]
        self.assertRegex(renamed, r"^/acme/out/order\.csv-\d{8}-\d{6}-utc\.old$")
        self.assertEqual(FakeFTP.files[renamed], b"new")

    def _receive_by_name(self, session):
        return {message.name: message for message in self.connection._edi_receive(session, "in")}

    def _folder(self, folder):
        return {posixpath.basename(p): c for p, c in FakeFTP.files.items() if posixpath.dirname(p) == folder}

    def test_acknowledge_move(self):
        self.connection.file_on_conflict = "rename"
        FakeFTP.files["/acme/in/done/a_order.csv"] = b"processed yesterday"
        with self.connection._edi_connect() as session:
            messages = self._receive_by_name(session)
            self.connection._edi_acknowledge(session, messages["a_order.csv"], success=True)
            self.connection._edi_acknowledge(session, messages["b_order.csv"], success=False)
        self.assertEqual(self._folder("/acme/in"), {"c_order.xml": b"c"})
        self.assertEqual(self._folder("/acme/in/error"), {"b_order.csv": b"b"})
        done = self._folder("/acme/in/done")
        self.assertEqual(done.pop("a_order.csv"), b"a")
        self.assertEqual(sorted(done.values()), [b"old", b"processed yesterday"])  # renamed aside

    def test_acknowledge_move_absolute_folders(self):
        self.connection.write({"file_success_folder": "/archive/ok", "file_failed_folder": "/archive/ko"})
        with self.connection._edi_connect() as session:
            messages = self._receive_by_name(session)
            self.connection._edi_acknowledge(session, messages["a_order.csv"], success=True)
            self.connection._edi_acknowledge(session, messages["b_order.csv"], success=False)
        self.assertEqual(self._folder("/acme/archive/ok"), {"a_order.csv": b"a"})
        self.assertEqual(self._folder("/acme/archive/ko"), {"b_order.csv": b"b"})

    def test_acknowledge_delete(self):
        self.connection.write({"file_processed_action": "delete", "file_failed_folder": False})
        with self.connection._edi_connect() as session:
            messages = self._receive_by_name(session)
            self.connection._edi_acknowledge(session, messages["a_order.csv"], success=True)
            self.connection._edi_acknowledge(session, messages["b_order.csv"], success=False)
        # the failed file is left in place, to be retried
        self.assertEqual(self._folder("/acme/in"), {"b_order.csv": b"b", "c_order.xml": b"c"})

    def test_acknowledge_keep(self):
        self.connection.file_processed_action = "keep"
        with self.connection._edi_connect() as session:
            messages = self._receive_by_name(session)
            self.connection._edi_acknowledge(session, messages["a_order.csv"], success=True)
            self.connection._edi_acknowledge(session, messages["b_order.csv"], success=False)
        self.assertEqual(set(self._folder("/acme/in")), {"a_order.csv", "c_order.xml"})
        self.assertEqual(self._folder("/acme/in/error"), {"b_order.csv": b"b"})

    def test_acknowledge_folder_outside_root(self):
        self.connection.file_success_folder = "../../elsewhere"
        with (
            self.connection._edi_connect() as session,
            self.assertRaisesRegex(EdiTransportError, "outside"),
        ):
            self.connection._edi_acknowledge(session, self._receive_by_name(session)["a_order.csv"], success=True)

    def test_paths_outside_root(self):
        with self.connection._edi_connect() as session:
            for location in ("..", "in/../../etc", "/../etc"):
                with self.subTest(location=location), self.assertRaisesRegex(EdiTransportError, "outside"):
                    self.connection._edi_receive(session, location)
            for name in ("..", "../order.csv", "sub/order.csv", ""):
                with self.subTest(name=name), self.assertRaises(EdiTransportError):
                    self.connection._edi_send(session, "out", b"x", name=name)
            # going up and down again inside the root is fine
            self.assertEqual(len(self.connection._edi_receive(session, "in/done/..")), 3)
        self.assertFalse([p for p in FakeFTP.files if not p.startswith("/acme/in/")])

    def test_send_failure_removes_partial_upload(self):
        with (
            patch.object(FakeFTP, "rename", side_effect=ftplib.error_perm("553 Rename refused.")),
            self.connection._edi_connect() as session,
            self.assertRaisesRegex(EdiTransportError, "553"),
        ):
            self.connection._edi_send(session, "out", b"x", name="order.csv")
        self.assertFalse([p for p in FakeFTP.files if p.startswith("/acme/out/")])

    def test_replace_restores_target_on_failure(self):
        self.connection.file_on_conflict = "replace"
        FakeFTP.files["/acme/out/order.csv"] = b"previous"
        original_rename = FakeFTP.rename

        def rename(ftp, path, new_path):
            if path.endswith(".filepart"):
                raise ftplib.error_perm("553 Rename refused.")
            return original_rename(ftp, path, new_path)

        with (
            patch.object(FakeFTP, "rename", rename),
            self.connection._edi_connect() as session,
            self.assertRaises(EdiTransportError),
        ):
            self.connection._edi_send(session, "out", b"new", name="order.csv")
        self.assertEqual(
            {p: c for p, c in FakeFTP.files.items() if p.startswith("/acme/out/")}, {"/acme/out/order.csv": b"previous"}
        )
