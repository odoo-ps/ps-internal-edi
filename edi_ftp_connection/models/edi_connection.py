# Part of Odoo. See LICENSE file for full copyright and licensing details.
import contextlib
import ftplib
import io
import posixpath
import ssl
from datetime import UTC, datetime
from urllib.parse import urlsplit

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.edi_base.tools import EdiTransportError


FTP_DEFAULT_PORT = 21


@contextlib.contextmanager
def _convert_ftp_errors():
    """Turn the FTP errors (protocol, network, TLS) into ``EdiTransportError``."""
    try:
        yield
    except ftplib.all_errors as e:
        raise EdiTransportError(str(e)) from e


def _ftp_time(value):
    """Parse a MLSD/MDTM time (``YYYYMMDDHHMMSS[.sss]``, always UTC) into an aware datetime, or ``None``."""
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


class EdiConnection(models.Model):
    _inherit = "edi.connection"

    transport = fields.Selection(selection_add=[("ftp", "FTP(S)")], ondelete={"ftp": "cascade"})
    ftp_passive = fields.Boolean(
        string="Passive Mode",
        default=True,
        tracking=True,
        help="Let the client open the data connections. Disable only if the server requires the active mode.",
    )

    @api.model
    def _get_transports(self):
        return {
            **super()._get_transports(),
            "ftp": {
                "schemes": ("ftp", "ftps"),
                "auth_types": ("none", "basic"),
                "client_certificate": True,
                "file_based": True,
            },
        }

    @api.constrains("transport", "url", "tls_client_auth")
    def _check_ftp_client_certificate(self):
        for connection in self:
            if connection.transport == "ftp" and connection.tls_client_auth and not connection._ftp_is_tls():
                raise ValidationError(
                    self.env._("A client certificate needs TLS: use an ftps:// URL on connection %s.", connection.name)
                )

    def _edi_connect(self):
        self.ensure_one()
        if self.transport != "ftp":
            return super()._edi_connect()
        connection = self.sudo()  # credentials are restricted to administrators
        parts = urlsplit(connection.url)
        if connection._ftp_is_tls():
            ftp = ftplib.FTP_TLS(context=connection._ftp_ssl_context(), timeout=connection.timeout)
        else:
            ftp = ftplib.FTP(timeout=connection.timeout)
        with _convert_ftp_errors():
            try:
                ftp.connect(parts.hostname, parts.port or FTP_DEFAULT_PORT)
                if connection.auth_type == "basic":
                    ftp.login(connection.username or "", connection.password or "")
                else:
                    ftp.login()  # anonymous
                if connection._ftp_is_tls():
                    ftp.prot_p()  # encrypt the data connections too, not only the commands
                ftp.set_pasv(connection.ftp_passive)
                ftp.voidcmd("TYPE I")  # binary, also needed by SIZE
            except BaseException:
                ftp.close()
                raise
        return ftp

    def _file_list(self, session, path):
        if self.transport != "ftp":
            return super()._file_list(session, path)
        with _convert_ftp_errors():
            try:
                return [
                    (
                        name,
                        {
                            "size": int(facts["size"]) if "size" in facts else None,
                            "modify": _ftp_time(facts.get("modify")),
                        },
                    )
                    for name, facts in session.mlsd(path, facts=["type", "size", "modify"])
                    if facts.get("type") == "file"
                ]
            except ftplib.error_perm as e:
                if not str(e).startswith(("500", "501", "502")):  # MLSD not supported: list with NLST
                    raise
            return self._ftp_list_nlst(session, path)

    def _file_exists(self, session, path):
        if self.transport != "ftp":
            return super()._file_exists(session, path)
        with _convert_ftp_errors():
            try:
                session.size(path)
            except ftplib.error_perm:  # 550: no such file (or a folder)
                return False
            return True

    def _file_read(self, session, path):
        if self.transport != "ftp":
            return super()._file_read(session, path)
        content = io.BytesIO()
        with _convert_ftp_errors():
            session.retrbinary(f"RETR {path}", content.write)
        return content.getvalue()

    def _file_write(self, session, path, content):
        if self.transport != "ftp":
            return super()._file_write(session, path, content)
        with _convert_ftp_errors():
            session.storbinary(f"STOR {path}", io.BytesIO(content))

    def _file_rename(self, session, path, new_path):
        if self.transport != "ftp":
            return super()._file_rename(session, path, new_path)
        with _convert_ftp_errors():
            session.rename(path, new_path)

    def _file_delete(self, session, path):
        if self.transport != "ftp":
            return super()._file_delete(session, path)
        with _convert_ftp_errors():
            session.delete(path)

    def _ftp_is_tls(self):
        return urlsplit(self.url).scheme == "ftps"

    def _ftp_ssl_context(self):
        context = ssl.create_default_context()
        if self.tls_client_auth:
            # the certificate is loaded in memory: the files are not needed afterwards
            with self._tls_client_certificate_files() as (cert_path, key_path):
                context.load_cert_chain(cert_path, key_path)
        return context

    def _ftp_list_nlst(self, session, path):
        """List the files of ``path`` on servers without MLSD.

        NLST lists the folders too, SIZE tells them apart; MDTM gives the date, to sort them.
        """
        try:
            names = session.nlst(path)
        except ftplib.error_perm as e:
            if str(e).startswith("550"):  # some servers answer an error for an empty folder
                return []
            raise
        files = []
        for name in names:
            name = posixpath.basename(name)
            if name in (".", ".."):
                continue
            file_path = posixpath.join(path, name)
            try:
                size = session.size(file_path)
            except ftplib.error_perm:  # a folder
                continue
            try:
                modify = _ftp_time(session.voidcmd(f"MDTM {file_path}").split()[-1])
            except ftplib.error_perm:  # MDTM not supported either: sorted by name
                modify = None
            files.append((name, {"size": size, "modify": modify}))
        return files
