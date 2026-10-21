# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import contextlib
import io
import logging
import stat
from datetime import UTC, datetime
from urllib.parse import urlsplit

import paramiko

from odoo import api, fields, models

from odoo.addons.edi_base.tools import EdiTransportError


_logger = logging.getLogger(__name__)

SFTP_DEFAULT_PORT = 22


@contextlib.contextmanager
def _convert_sftp_errors():
    """Turn the SSH/SFTP errors (protocol, authentication, network, remote file) into ``EdiTransportError``."""
    try:
        yield
    except EdiTransportError:
        raise
    except (paramiko.SSHException, OSError, EOFError) as e:
        raise EdiTransportError(str(e) or type(e).__name__) from e


class SftpSession:
    """Session of the ``sftp`` transport: the SSH connection and its SFTP channel, closed together."""

    def __init__(self, client, sftp):
        self.client = client
        self.sftp = sftp

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        try:
            self.sftp.close()
        finally:
            self.client.close()


class EdiConnection(models.Model):
    _inherit = "edi.connection"

    transport = fields.Selection(selection_add=[("sftp", "SFTP")], ondelete={"sftp": "cascade"})
    sftp_host_key = fields.Char(
        string="Host Key",
        copy=False,
        tracking=True,
        help="Public key of the server, as in a known_hosts file: <type> <base64 key>. "
        "Saved on the first connection if empty, the server must then always present this key. "
        "Clear it if the server key changed on purpose.",
    )

    @api.model
    def _get_transports(self):
        return {
            **super()._get_transports(),
            "sftp": {
                "schemes": ("sftp",),
                "auth_types": ("basic", "ssh_key"),
                "file_based": True,
            },
        }

    def _edi_connect(self):
        self.ensure_one()
        if self.transport != "sftp":
            return super()._edi_connect()
        connection = self.sudo()  # credentials are restricted to administrators
        parts = urlsplit(connection.url)
        port = parts.port or SFTP_DEFAULT_PORT
        # name of the server in the host keys, as in a known_hosts file
        host_alias = parts.hostname if port == SFTP_DEFAULT_PORT else f"[{parts.hostname}]:{port}"
        saved_key = connection._sftp_get_saved_host_key()

        # Only the host key and the credentials of the connection are used: the host keys of the
        # system (known_hosts files) are not loaded (no load_system_host_keys()), and the other
        # login methods of SSHClient are disabled (SSH agent, keys of ~/.ssh; GSSAPI is off by default).
        client = paramiko.SSHClient()
        if saved_key:
            # checked by paramiko before sending any credential, also asking the server for this key type
            client.get_host_keys().add(host_alias, saved_key.get_name(), saved_key)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            # first connection: the key presented by the server is trusted, and saved below
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        credentials = (
            {"pkey": connection._sftp_load_private_key()}
            if connection.auth_type == "ssh_key"
            else {"password": connection.password or ""}
        )
        with _convert_sftp_errors():
            try:
                client.connect(
                    parts.hostname,
                    port=port,
                    username=connection.username,
                    timeout=connection.timeout,
                    banner_timeout=connection.timeout,
                    auth_timeout=connection.timeout,
                    channel_timeout=connection.timeout,
                    allow_agent=False,
                    look_for_keys=False,
                    **credentials,
                )
                sftp = client.open_sftp()
                sftp.get_channel().settimeout(connection.timeout)
            except paramiko.BadHostKeyException as e:
                client.close()
                raise EdiTransportError(
                    self.env._(
                        "The host key of %(host)s is not the saved one: it presented %(type)s %(fingerprint)s. "
                        "This may be an attack. If the server key changed on purpose, clear the Host Key "
                        "of the connection and connect again.",
                        host=parts.hostname,
                        type=e.key.get_name(),
                        fingerprint=e.key.fingerprint,
                    )
                ) from e
            except BaseException:
                client.close()
                raise
            finally:
                # saved even if the login failed: the key was accepted on this first attempt
                if not saved_key and (presented := (client.get_host_keys().lookup(host_alias) or {})):
                    connection._sftp_save_host_key(next(iter(presented.values())))
        return SftpSession(client, sftp)

    def _file_list(self, session, path):
        if self.transport != "sftp":
            return super()._file_list(session, path)
        with _convert_sftp_errors():
            return [
                (
                    attributes.filename,
                    {
                        "size": attributes.st_size,
                        "modify": datetime.fromtimestamp(attributes.st_mtime, UTC) if attributes.st_mtime else None,
                    },
                )
                for attributes in session.sftp.listdir_attr(path)
                # only the regular files: not the folders, links, ... A file whose type is not sent by
                # the server (no st_mode) is kept: reading it fails loudly if it is a folder, whereas
                # skipping it would silently never process it.
                if attributes.st_mode is None or stat.S_ISREG(attributes.st_mode)
            ]

    def _file_exists(self, session, path):
        if self.transport != "sftp":
            return super()._file_exists(session, path)
        with _convert_sftp_errors():
            try:
                session.sftp.stat(path)
            except FileNotFoundError:
                return False
            return True

    def _file_read(self, session, path):
        if self.transport != "sftp":
            return super()._file_read(session, path)
        content = io.BytesIO()
        with _convert_sftp_errors():
            session.sftp.getfo(path, content)
        return content.getvalue()

    def _file_write(self, session, path, content):
        if self.transport != "sftp":
            return super()._file_write(session, path, content)
        with _convert_sftp_errors():
            session.sftp.putfo(io.BytesIO(content), path)

    def _file_rename(self, session, path, new_path):
        if self.transport != "sftp":
            return super()._file_rename(session, path, new_path)
        with _convert_sftp_errors():
            session.sftp.rename(path, new_path)

    def _file_replace(self, session, path, target):
        if self.transport != "sftp":
            return super()._file_replace(session, path, target)
        try:
            # atomic overwrite (posix-rename@openssh.com), supported by OpenSSH servers
            session.sftp.posix_rename(path, target)
        except OSError as e:
            _logger.info("EDI connection %s: posix-rename not available (%s), replacing in two steps", self.id, e)
            return super()._file_replace(session, path, target)

    def _file_delete(self, session, path):
        if self.transport != "sftp":
            return super()._file_delete(session, path)
        with _convert_sftp_errors():
            session.sftp.remove(path)

    def _sftp_get_saved_host_key(self):
        """Return ``sftp_host_key`` as a paramiko key, ``None`` if there is none yet.

        :raise EdiTransportError: if it is not a valid key
        """
        if not self.sftp_host_key:
            return None
        key_type, _sep, key_data = self.sftp_host_key.strip().partition(" ")
        try:
            # ignore a known_hosts comment after the key
            return paramiko.PKey.from_type_string(key_type, base64.b64decode(key_data.split()[0]))
        except (paramiko.SSHException, paramiko.UnknownKeyType, IndexError, ValueError) as e:  # binascii.Error too
            raise EdiTransportError(
                self.env._(
                    "The Host Key of connection %s is not valid: clear it to save the key of the server on "
                    "the next connection.",
                    self.name,
                )
            ) from e

    def _sftp_save_host_key(self, key):
        """Save the key presented by the server on its first connection.

        The key is saved on a separate cursor, so that it is kept even if the connection attempt
        fails afterwards (e.g. wrong password) or the current transaction is rolled back.

        :param paramiko.PKey key: the key presented by the server
        """
        with self.env.registry.cursor() as cr:
            connection = self.with_env(self.env(cr=cr, su=True))
            if connection.sftp_host_key:  # saved meanwhile by another connection attempt
                return
            connection.sftp_host_key = f"{key.get_name()} {key.get_base64()}"
            connection.message_post(
                body=self.env._(
                    "Host key saved on the first connection: %(type)s %(fingerprint)s. "
                    "Check that it is the fingerprint given by the remote system.",
                    type=key.get_name(),
                    fingerprint=key.fingerprint,
                )
            )
        _logger.info("EDI connection %s: host key %s saved", self.id, key.fingerprint)

    def _sftp_load_private_key(self):
        """Load ``ssh_private_key`` (RSA, ECDSA or Ed25519, OpenSSH or PEM format).

        :rtype: paramiko.PKey
        :raise EdiTransportError: if the key cannot be loaded (format, passphrase)
        """
        errors = []
        for key_class in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
            try:
                return key_class.from_private_key(io.StringIO(self.ssh_private_key or ""), self.ssh_passphrase or None)
            except paramiko.PasswordRequiredException as e:
                raise EdiTransportError(self.env._("The private key is encrypted: set its passphrase.")) from e
            except (paramiko.SSHException, ValueError) as e:
                errors.append(f"{key_class.__name__}: {e}")
        raise EdiTransportError(
            self.env._("Invalid private key on connection %(name)s (%(errors)s)", name=self.name, errors=errors)
        )
