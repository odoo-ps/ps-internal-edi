# Part of Odoo. See LICENSE file for full copyright and licensing details.
import io
import os
import shutil
import tempfile

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.base.tests.common import BaseCommon
from odoo.addons.edi_base.tools import EdiTransportError

from .sftp_server import SFTPTestServer


def _host_key_line(key):
    return f"{key.get_name()} {key.get_base64()}"


@tagged("post_install", "-at_install")
class TestEdiSftpConnection(BaseCommon):
    _test_user_groups = ("base.group_system",)  # connections are managed by administrators only

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the host key is saved on a separate cursor: make it share the test transaction
        cls.enterClassContext(cls.registry_test_mode())
        # the server side logs an error when the client hangs up during the handshake
        cls.enterClassContext(mute_logger("paramiko.transport"))
        cls.host_key = paramiko.ECDSAKey.generate()
        client_key = ed25519.Ed25519PrivateKey.generate()
        cls.client_key_pem = client_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
        ).decode()
        cls.client_key = paramiko.Ed25519Key.from_private_key(io.StringIO(cls.client_key_pem))

        cls.root = tempfile.mkdtemp(prefix="edi_sftp_test_")
        cls.addClassCleanup(shutil.rmtree, cls.root)
        cls.server = SFTPTestServer(cls.root, cls.host_key, authorized_key=cls.client_key).start()
        cls.addClassCleanup(cls.server.stop)

        cls.connection = cls.env["edi.connection"].create(
            {
                "name": "ACME SFTP",
                "transport": "sftp",
                "url": f"sftp://127.0.0.1:{cls.server.port}/acme",
                "auth_type": "basic",
                "username": "odoo",
                "password": "secret",
                "timeout": 10,
            }
        )

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.root)
        for folder in ("acme/in/done", "acme/in/error", "acme/out"):
            os.makedirs(os.path.join(self.root, folder))
        # most recent first: a.csv, b.csv, c.xml
        for name, content, mtime in [("b.csv", b"b;2\n", 2000), ("a.csv", b"a;1\n", 3000), ("c.xml", b"<c/>", 1000)]:
            path = os.path.join(self.root, "acme/in", name)
            with open(path, "wb") as file:
                file.write(content)
            os.utime(path, (1_700_000_000 + mtime, 1_700_000_000 + mtime))

    def _remote(self, path):
        return os.path.join(self.root, path.lstrip("/"))

    # ------------------------------------------------------------
    # Host key
    # ------------------------------------------------------------

    def test_host_key_saved_on_first_connection(self):
        self.assertFalse(self.connection.sftp_host_key)
        with self.connection._edi_connect():
            pass
        self.connection.invalidate_recordset()  # saved on another cursor
        self.assertEqual(self.connection.sftp_host_key, _host_key_line(self.host_key))
        self.assertTrue(
            any(self.host_key.fingerprint in message.body for message in self.connection.message_ids),
            "the fingerprint is posted in the chatter",
        )

        # next connections check it
        with self.connection._edi_connect():
            pass

    def test_host_key_saved_even_if_login_fails(self):
        self.connection.password = "wrong"
        # not assertRaises: its savepoint would also roll back the test cursor sharing this transaction
        try:
            self.connection._edi_connect()
        except EdiTransportError:
            pass
        else:
            self.fail("The login should have failed")
        self.connection.invalidate_recordset()
        self.assertEqual(self.connection.sftp_host_key, _host_key_line(self.host_key))

    def test_host_key_mismatch(self):
        self.connection.sftp_host_key = _host_key_line(paramiko.ECDSAKey.generate())
        with self.assertRaisesRegex(EdiTransportError, "host key of 127.0.0.1 is not the saved one"):
            self.connection._edi_connect()
        # cleared on purpose: the new key is accepted and saved
        self.connection.sftp_host_key = False
        with self.connection._edi_connect():
            pass
        self.connection.invalidate_recordset()
        self.assertEqual(self.connection.sftp_host_key, _host_key_line(self.host_key))

    def test_host_key_type_of_saved_key(self):
        """A server with several host keys presents the type of the saved key (e.g. RSA migrated from 19.0)."""
        rsa_key = paramiko.RSAKey.generate(2048)
        server = SFTPTestServer(self.root, [paramiko.ECDSAKey.generate(), rsa_key]).start()
        self.addCleanup(server.stop)
        self.connection.write(
            {
                "url": f"sftp://127.0.0.1:{server.port}/acme",
                "sftp_host_key": _host_key_line(rsa_key),
            }
        )
        with self.connection._edi_connect() as session:
            self.assertEqual(session.client.get_transport().get_remote_server_key(), rsa_key)

    def test_host_key_invalid(self):
        self.connection.sftp_host_key = "ssh-ed25519 not-a-key"
        with self.assertRaisesRegex(UserError, "Host Key of connection ACME SFTP is not valid"):
            self.connection.action_test()

    def test_host_key_with_comment(self):
        self.connection.sftp_host_key = f"{_host_key_line(self.host_key)} files.example.com"
        with self.connection._edi_connect():
            pass

    # ------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------

    def test_auth_types(self):
        for auth_type in ("none", "api_key", "oauth2", "custom"):
            with self.subTest(auth_type=auth_type), self.assertRaises(ValidationError):
                self.connection.auth_type = auth_type
        self.assertFalse(self.connection.tls_client_auth_supported)

    def test_ssh_key(self):
        self.connection.write({"auth_type": "ssh_key", "ssh_private_key": self.client_key_pem})
        with self.connection._edi_connect() as session:
            self.assertEqual(self.connection._edi_receive(session, "in", pattern="a.*")[0].content, b"a;1\n")

        # not the authorized key
        other_key = ed25519.Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
        )
        self.connection.ssh_private_key = other_key.decode()
        with self.assertRaisesRegex(EdiTransportError, "Authentication failed"):
            self.connection._edi_connect()

    def test_ssh_key_encrypted_rsa(self):
        rsa_pem = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.BestAvailableEncryption(b"passphrase"),
            )
            .decode()
        )
        # not checked on save: the Test Connection button tells what is wrong
        self.connection.write({"auth_type": "ssh_key", "ssh_private_key": rsa_pem})
        with self.assertRaisesRegex(UserError, "passphrase"):
            self.connection.action_test()
        self.connection.ssh_passphrase = "passphrase"
        self.assertIsInstance(self.connection._sftp_load_private_key(), paramiko.RSAKey)
        self.connection.ssh_private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\ngarbage\n"
        with self.assertRaisesRegex(UserError, "Invalid private key"):
            self.connection.action_test()

    def test_action_test(self):
        self.assertEqual(self.connection.action_test()["params"]["type"], "success")

    # ------------------------------------------------------------
    # Files
    # ------------------------------------------------------------

    @mute_logger("odoo.addons.edi_base.models.edi_connection")  # the partial upload cannot be removed
    def test_files(self):
        self.connection.file_on_conflict = "rename"
        with open(self._remote("acme/in/done/a.csv"), "wb") as file:
            file.write(b"processed yesterday")
        with self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "in")
            self.assertEqual(
                [(m.name, m.content) for m in messages],
                [
                    ("a.csv", b"a;1\n"),
                    ("b.csv", b"b;2\n"),
                    ("c.xml", b"<c/>"),
                ],
            )
            self.assertEqual(messages[0].ref, "/acme/in/a.csv")
            self.assertEqual(messages[0].meta["size"], 4)
            self.assertEqual(
                [m.name for m in self.connection._edi_receive(session, "in", pattern="*.csv", limit=1)], ["a.csv"]
            )

            self.connection._edi_acknowledge(session, messages[0], success=True)  # moved to in/done
            self.connection._edi_acknowledge(session, messages[1], success=False)  # moved to in/error

            self.connection._edi_send(session, "out", b"x;1\n", name="order.csv")
            self.connection._edi_send(session, "out", b"x;2\n", name="order.csv")

            with self.assertRaises(EdiTransportError):  # no such folder
                self.connection._edi_send(session, "nowhere", b"x", name="x.csv")
            with self.assertRaises(EdiTransportError):  # no such file
                self.connection._file_read(session, "/acme/in/missing.csv")

        self.assertEqual(sorted(os.listdir(self._remote("acme/in"))), ["c.xml", "done", "error"])
        self.assertEqual(os.listdir(self._remote("acme/in/error")), ["b.csv"])
        done = sorted(os.listdir(self._remote("acme/in/done")))
        self.assertEqual(done[0], "a.csv")
        self.assertRegex(done[1], r"^a\.csv-\d{8}-\d{6}-utc\.old$")
        out = sorted(os.listdir(self._remote("acme/out")))
        self.assertEqual(out[0], "order.csv")
        self.assertRegex(out[1], r"^order\.csv-\d{8}-\d{6}-utc\.old$")
        with open(self._remote("acme/out/order.csv"), "rb") as file:
            self.assertEqual(file.read(), b"x;2\n")

    def test_url_without_path(self):
        """Without path in the URL, the folders are relative to the login directory (here the server root)."""
        self.connection.url = f"sftp://127.0.0.1:{self.server.port}"
        with self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "acme/in", pattern="a.*")
            self.assertEqual(messages[0].ref, "acme/in/a.csv")
            self.connection._edi_acknowledge(session, messages[0], success=True)
            self.connection._edi_send(session, "acme/out", b"x", name="order.csv")
            with self.assertRaisesRegex(EdiTransportError, "outside"):
                self.connection._edi_receive(session, "..")
        self.assertEqual(os.listdir(self._remote("acme/in/done")), ["a.csv"])
        self.assertEqual(os.listdir(self._remote("acme/out")), ["order.csv"])

    def test_receive_most_recent_first(self):
        os.utime(self._remote("acme/in/c.xml"), (1_800_000_000, 1_800_000_000))
        with self.connection._edi_connect() as session:
            messages = self.connection._edi_receive(session, "in")
            self.assertEqual([m.name for m in messages], ["c.xml", "a.csv", "b.csv"])
            self.assertEqual(messages[0].meta["modify"].timestamp(), 1_800_000_000)
            self.assertEqual([m.name for m in self.connection._edi_receive(session, "in", limit=2)], ["c.xml", "a.csv"])

    def test_replace(self):
        self.connection.file_on_conflict = "replace"
        for posix_rename in (True, False):  # atomic, or in two steps when the server does not support it
            with self.subTest(posix_rename=posix_rename), self.connection._edi_connect() as session:
                self.server.posix_rename = posix_rename
                self.connection._edi_send(session, "out", b"first", name="order.csv")
                self.connection._edi_send(session, "out", b"second", name="order.csv")
                self.assertEqual(os.listdir(self._remote("acme/out")), ["order.csv"])
                with open(self._remote("acme/out/order.csv"), "rb") as file:
                    self.assertEqual(file.read(), b"second")
                os.remove(self._remote("acme/out/order.csv"))
        self.server.posix_rename = True
