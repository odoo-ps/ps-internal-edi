# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import contextlib
import fnmatch
import hashlib
import logging
import os
import posixpath
import re
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import requests
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import consteq
from odoo.tools.misc import hmac
from odoo.tools.urls import urljoin

from ..tools import EdiHttpSession, EdiMessage, EdiTransportError


_logger = logging.getLogger(__name__)

OAUTH2_TOKEN_EXPIRY_FALLBACK = 3600  # seconds, when the token response has no expires_in
OAUTH2_TOKEN_EXPIRY_MARGIN = 60  # seconds, renew the token a bit before it expires
OAUTH2_REDIRECT_PATH = "/edi/oauth2/callback"
HTTP_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")  # RFC 9110 token
# changing one of those fields invalidates the cached OAuth2 access token
OAUTH2_TOKEN_FIELDS = {
    "url",
    "auth_type",
    "username",
    "password",
    "oauth2_grant_type",
    "oauth2_token_url",
    "oauth2_client_id",
    "oauth2_client_secret",
    "oauth2_scope",
}
# changing one of those fields invalidates the OAuth2 refresh token (authorization code grant):
# the consent has to be given again
OAUTH2_REFRESH_TOKEN_FIELDS = {
    "auth_type",
    "oauth2_grant_type",
    "oauth2_token_url",
    "oauth2_client_id",
    "oauth2_scope",
}


class EdiConnection(models.Model):
    """A remote system Odoo exchanges data with.

    Two independent dimensions:

    - ``transport``: how bytes are moved (HTTP, FTP, SFTP, ...)
    - ``auth_type``: how we prove who we are (API key, username/password, OAuth2, SSH key, ...)

    Any auth type supported by the transport can be combined with it (see ``_get_transports``).
    All the authentications and their credential fields are defined here, in ``edi_base``, so that
    transport modules only declare which ones they accept and use them: the credentials part of
    the form is never overridden (see ``_get_auth_fields``).
    What is exchanged (format, endpoint/folder, mapping) belongs to the flows, not to the connection.

    Transport API, the same for every transport (like ``ir.mail_server.connect`` / ``send_email``)::

        with connection._edi_connect() as session:
            for message in connection._edi_receive(session, "/in"):
                ...
                connection._edi_acknowledge(session, message, success=True)

    A module adding a transport extends the ``transport`` selection and ``_get_transports``, and
    overrides the ``_edi_*`` methods for its own transport, calling ``super()`` for the others.

    Extension points are tagged in their docstring (see ``doc/developer_guide.rst``):

    - ``:edi-hook: framework``: extended by EDI framework modules (new transport, format, ...)
    - ``:edi-hook: client``: may be overridden by client modules implementing an integration
    """

    _name = "edi.connection"
    _description = "EDI Connection"
    _inherit = ["mail.thread"]
    _order = "name, id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one("res.company", tracking=True)
    transport = fields.Selection(
        selection=[("http", "HTTP(S)")],
        required=True,
        default="http",
        tracking=True,
    )
    url = fields.Char(
        string="URL",
        required=True,
        tracking=True,
        help="Base URL of the remote system, e.g. https://api.example.com/v1 or sftp://files.example.com:22/acme. "
        "For file transfers, without path (sftp://files.example.com) the folders are relative to the "
        "login directory.",
    )
    timeout = fields.Integer(default=30, help="Seconds to wait for the remote system to answer.")
    show_advanced = fields.Boolean(
        string="Advanced Settings",
        compute="_compute_show_advanced",
        store=True,
        readonly=False,
        copy=False,
        help="Show the settings that rarely need a change (client certificate, extra headers, timeout, ...). "
        "Checked automatically when one of them is changed, so that it is never hidden.",
    )
    transport_file_based = fields.Boolean(compute="_compute_transport_file_based")
    file_on_conflict = fields.Selection(
        selection=[
            ("fail", "Fail"),
            ("rename", "Rename the existing file"),
            ("replace", "Replace the existing file"),
        ],
        string="If File Exists",
        default="fail",
        required=True,
        tracking=True,
        help="What to do when a file is sent or moved where a file with the same name already exists. "
        "Rename: the existing file gets a -<UTC date>.old suffix.",
    )
    file_processed_action = fields.Selection(
        selection=[
            ("move", "Move"),
            ("delete", "Delete"),
            ("keep", "Leave in Place"),
        ],
        string="Processed Files",
        default="move",
        required=True,
        tracking=True,
        help="Incoming flows only: what to do with a received file once processed successfully (a failed one is "
        "moved to the failed folder with Move, left in place otherwise). The files "
        "sent by Odoo (outgoing flows) are not affected: once uploaded, they belong to the remote system.\n"
        "When the files are moved or deleted, the most recent ones are received first. When they are left in "
        "place, the oldest ones are received first, and only the files more recent than the last processed one.",
    )
    file_success_folder = fields.Char(
        string="Success Folder",
        default="done",
        tracking=True,
        help="Incoming flows only: where to move the received files processed successfully: relative to the "
        "folder of the file (e.g. done for in/done), or to the path of the URL if it starts with /.",
    )
    file_failed_folder = fields.Char(
        string="Failed Folder",
        default="error",
        tracking=True,
        help="Incoming flows only, when the processed files are moved: where to move the received files whose "
        "processing failed: relative to the folder of the file (e.g. error for in/error), or to the path of the "
        "URL if it starts with /. Leave empty to leave them in place: they are received again, to be retried, on "
        "the next run. When the processed files are deleted or left in place, the failed ones are left in place.",
    )
    auth_type = fields.Selection(
        selection=[
            ("none", "None"),
            ("basic", "Username / Password"),
            ("api_key", "API Key"),
            ("oauth2", "OAuth2"),
            ("ssh_key", "SSH Key"),
        ],
        string="Authentication",
        required=True,
        default="none",
        tracking=True,
    )
    auth_field_names = fields.Json(
        compute="_compute_auth_field_names",
        help="Technical: credential fields used by the authentication, to show them in the form.",
    )

    # basic (also used by the OAuth2 password grant and by the SSH key)
    username = fields.Char(tracking=True)
    password = fields.Char(copy=False)

    # api_key
    api_key = fields.Char(string="API Key", copy=False)
    api_key_location = fields.Selection(
        selection=[("header", "Header"), ("query", "Query Parameter")],
        string="Send Key In",
        default="header",
        tracking=True,
    )
    api_key_name = fields.Char(
        string="Key Name",
        default="Authorization",
        tracking=True,
        help="Name of the header or query parameter carrying the key, e.g. Authorization, X-API-Key, api_key.",
    )
    api_key_prefix = fields.Char(
        string="Key Prefix",
        default="Bearer",
        tracking=True,
        help="Prepended to the key, separated by a space, e.g. Bearer or Token. Leave empty to send the key as is.",
    )

    # oauth2
    oauth2_grant_type = fields.Selection(
        selection=[
            ("client_credentials", "Client Credentials"),
            ("password", "Password"),
            ("authorization_code", "Authorization Code"),
        ],
        string="Grant Type",
        default="client_credentials",
        tracking=True,
    )
    oauth2_authorize_url = fields.Char(
        string="Authorization URL",
        tracking=True,
        help="Page of the remote system where a user logs in and gives Odoo access (authorization code grant).",
    )
    oauth2_redirect_uri = fields.Char(
        string="Redirect URI",
        compute="_compute_oauth2_redirect_uri",
        help="To register in the application configured on the remote system (authorization code grant).",
    )
    oauth2_use_pkce = fields.Boolean(
        string="Use PKCE",
        default=True,
        help="Protect the authorization code with PKCE (RFC 7636). Disable it only if the remote system rejects it.",
    )
    oauth2_token_url = fields.Char(string="Token URL", tracking=True)
    oauth2_client_id = fields.Char(string="Client ID", tracking=True)
    oauth2_client_secret = fields.Char(string="Client Secret", copy=False)
    oauth2_scope = fields.Char(string="Scope", tracking=True, help="Space-separated list of scopes.")
    oauth2_access_token = fields.Char(string="Access Token", readonly=True, copy=False)
    oauth2_token_expiry = fields.Datetime(string="Token Expiry", readonly=True, copy=False)
    oauth2_refresh_token = fields.Char(string="Refresh Token", readonly=True, copy=False)
    oauth2_code_verifier = fields.Char(readonly=True, copy=False, help="Technical: pending authorization request.")

    # ssh_key
    ssh_private_key = fields.Text(
        string="Private Key",
        copy=False,
        help="Private key in OpenSSH or PEM format, e.g. -----BEGIN OPENSSH PRIVATE KEY-----...",
    )
    ssh_passphrase = fields.Char(string="Key Passphrase", copy=False, help="Leave empty if the key is not encrypted.")

    # client certificate (mutual TLS), combinable with any auth_type
    tls_client_auth = fields.Boolean(
        string="Client Certificate",
        tracking=True,
        help="Authenticate with a TLS client certificate (mutual TLS), in addition to the authentication above.",
    )
    tls_client_auth_supported = fields.Boolean(compute="_compute_tls_client_auth_supported")
    tls_client_certificate = fields.Binary(
        string="Certificate File",
        attachment=True,
        copy=False,
        help="PEM file (certificate, optionally with its private key) or PKCS#12 file (.p12, .pfx).",
    )
    tls_client_certificate_name = fields.Char(string="Certificate File Name", copy=False)
    tls_client_key = fields.Binary(
        string="Private Key File",
        attachment=True,
        copy=False,
        help="PEM private key, if not included in the certificate file.",
    )
    tls_client_key_name = fields.Char(string="Private Key File Name", copy=False)
    tls_client_password = fields.Char(
        string="Certificate Password", copy=False, help="Password of the PKCS#12 file or of the private key, if any."
    )
    tls_client_certificate_expiry = fields.Datetime(
        string="Certificate Expiry", compute="_compute_tls_client_certificate_expiry"
    )

    # http
    http_use_headers = fields.Boolean(
        string="Extra Headers",
        tracking=True,
        help="Send extra headers with every request, e.g. a subscription key or an API version.",
    )
    http_headers = fields.Text(
        string="Headers",
        copy=False,
        help="One header per line, as in HTTP: Name: value",
    )

    @api.depends(lambda self: list(self._get_advanced_defaults()))
    def _compute_show_advanced(self):
        defaults = self._get_advanced_defaults()
        for connection in self:
            if any(connection[field] != default for field, default in defaults.items()):
                connection.show_advanced = True

    @api.model
    def _get_advanced_defaults(self):
        """Map the advanced fields (hidden unless ``show_advanced``) to their default value.

        ``show_advanced`` is checked as soon as one of them is changed. Extended by the transport
        modules for their own advanced fields.

        :edi-hook: framework
        :rtype: dict[str, Any]
        """
        return {
            "timeout": 30,
            "file_on_conflict": "fail",
            "api_key_location": "header",
            "api_key_name": "Authorization",
            "api_key_prefix": "Bearer",
            "oauth2_use_pkce": True,
            "oauth2_scope": False,
            "tls_client_auth": False,
            "http_use_headers": False,
        }

    @api.depends("auth_type", "oauth2_grant_type")
    def _compute_auth_field_names(self):
        auth_fields = self._get_auth_fields()
        for connection in self:
            names = list(auth_fields.get(connection.auth_type, ()))
            if connection.auth_type == "oauth2":
                names += auth_fields.get(f"oauth2_{connection.oauth2_grant_type}", ())
            connection.auth_field_names = names

    def _compute_oauth2_redirect_uri(self):
        for connection in self:
            connection.oauth2_redirect_uri = urljoin(connection.get_base_url(), OAUTH2_REDIRECT_PATH)

    @api.depends("transport")
    def _compute_transport_file_based(self):
        for connection in self:
            connection.transport_file_based = connection._get_transport_info().get("file_based", False)

    @api.depends("transport")
    def _compute_tls_client_auth_supported(self):
        for connection in self:
            connection.tls_client_auth_supported = connection._get_transport_info().get("client_certificate", False)

    @api.depends("tls_client_auth", "tls_client_certificate", "tls_client_key", "tls_client_password")
    def _compute_tls_client_certificate_expiry(self):
        for connection in self:
            expiry = False
            if connection.tls_client_auth and connection.tls_client_certificate:
                with contextlib.suppress(ValueError):
                    certificates, _key = connection._tls_load_client_certificate()
                    expiry = min(cert.not_valid_after_utc for cert in certificates).replace(tzinfo=None)
            connection.tls_client_certificate_expiry = expiry

    @api.model
    def _get_transports(self):
        """Describe each ``transport``: the URL schemes and the ``auth_type`` it accepts.

        Extended by the EDI modules adding a transport (e.g. ``edi_ftp_connection``), together with
        the ``transport`` selection and the ``_edi_*`` methods::

            def _get_transports(self):
                return {
                    **super()._get_transports(),
                    "ftp": {
                        "schemes": ("ftp", "ftps"),
                        "auth_types": ("none", "basic"),
                        "client_certificate": True,  # optional, default False
                        "file_based": True,  # optional, default False, see the _file_* methods
                    },
                }

        A module adding an authentication adds it to the ``auth_types`` of the transports it
        supports (see ``_http_authenticate``)::

            def _get_transports(self):
                transports = super()._get_transports()
                transports["http"]["auth_types"] += ("acme_hmac",)
                return transports

        :edi-hook: framework
        :rtype: dict[str, dict]
        """
        return {
            "http": {
                "schemes": ("http", "https"),
                "auth_types": ("none", "basic", "api_key", "oauth2"),
                "client_certificate": True,
            },
        }

    def _get_transport_info(self):
        """Return the description of the transport of this connection (see ``_get_transports``).

        :rtype: dict
        """
        return self._get_transports().get(self.transport, {}) if self.transport else {}

    @api.model
    def _get_auth_fields(self):
        """Map each ``auth_type`` to the credential fields it uses, shown in the form.

        ``oauth2_<grant type>`` entries list the extra fields of each OAuth2 grant type. Extended by
        a module adding an authentication (see ``_http_authenticate``).

        :edi-hook: client
        :rtype: dict[str, tuple[str]]
        """
        return {
            "none": (),
            "basic": ("username", "password"),
            "api_key": ("api_key", "api_key_location", "api_key_name", "api_key_prefix"),
            "oauth2": (
                "oauth2_grant_type",
                "oauth2_token_url",
                "oauth2_client_id",
                "oauth2_client_secret",
                "oauth2_scope",
                "oauth2_token_expiry",
            ),
            "oauth2_client_credentials": (),
            "oauth2_password": ("username", "password"),
            "oauth2_authorization_code": ("oauth2_authorize_url", "oauth2_redirect_uri", "oauth2_use_pkce"),
            "ssh_key": ("username", "ssh_private_key", "ssh_passphrase"),
        }

    @api.constrains("transport", "auth_type")
    def _check_auth_type(self):
        for connection in self:
            if connection.auth_type not in connection._get_transports()[connection.transport]["auth_types"]:
                raise ValidationError(
                    self.env._(
                        "Authentication %(auth)s cannot be used with transport %(transport)s.",
                        auth=connection._fields["auth_type"].convert_to_export(connection.auth_type, connection),
                        transport=connection._fields["transport"].convert_to_export(connection.transport, connection),
                    )
                )

    @api.constrains("transport", "url")
    def _check_url(self):
        for connection in self:
            schemes = connection._get_transports()[connection.transport]["schemes"]
            parts = urlsplit(connection.url)
            if parts.scheme not in schemes or not parts.hostname:
                raise ValidationError(
                    self.env._(
                        "The URL of connection %(name)s must look like %(example)s.",
                        name=connection.name,
                        example=f"{schemes[0]}://host/...",
                    )
                )

    @api.constrains("transport", "tls_client_auth", "tls_client_certificate", "tls_client_key", "tls_client_password")
    def _check_tls_client_certificate(self):
        for connection in self.filtered("tls_client_auth"):
            if not connection.tls_client_auth_supported:
                raise ValidationError(
                    self.env._("Transport of connection %s does not support client certificates.", connection.name)
                )
            if not connection.tls_client_certificate:
                raise ValidationError(self.env._("Upload the client certificate of connection %s.", connection.name))
            try:
                connection._tls_load_client_certificate()
            except ValueError as e:
                raise ValidationError(
                    self.env._(
                        "Invalid client certificate on connection %(name)s: %(error)s", name=connection.name, error=e
                    )
                ) from e

    @api.constrains("http_use_headers", "http_headers")
    def _check_http_headers(self):
        for connection in self.filtered("http_use_headers"):
            try:
                connection._http_parse_headers()
            except ValueError as e:
                raise ValidationError(
                    self.env._("Invalid headers on connection %(name)s: %(error)s", name=connection.name, error=e)
                ) from e

    @api.onchange("transport")
    def _onchange_transport(self):
        auth_types = self._get_transports().get(self.transport, {}).get("auth_types", ())
        if auth_types and self.auth_type not in auth_types:
            self.auth_type = auth_types[0]

    def write(self, vals):
        vals = self._normalize_vals(vals)
        if OAUTH2_TOKEN_FIELDS & vals.keys():
            # credentials changed: the cached token belongs to the previous ones
            vals = {"oauth2_access_token": False, "oauth2_token_expiry": False, **vals}
        if OAUTH2_REFRESH_TOKEN_FIELDS & vals.keys():
            vals = {"oauth2_refresh_token": False, **vals}
        return super().write(vals)

    @api.model
    def _normalize_vals(self, vals):
        """Store the URL without surrounding spaces, so that it can be used as is everywhere."""
        if vals.get("url"):
            vals = {**vals, "url": vals["url"].strip()}
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._normalize_vals(vals) for vals in vals_list])

    def action_test(self):
        self.ensure_one()
        try:
            with self._edi_connect() as session:
                self._edi_test(session)
        except EdiTransportError as e:
            raise UserError(self.env._("Connection test failed: %s", e)) from e
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("Connection test succeeded."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_reset_token(self):
        self.sudo().write({"oauth2_refresh_token": False})
        self._oauth2_reset_token()

    def action_oauth2_authorize(self):
        """Redirect the user to the remote system, to give Odoo access (authorization code grant).

        The remote system then redirects the user to ``OAUTH2_REDIRECT_PATH`` with an authorization
        code, exchanged for tokens by ``_oauth2_authorization_callback``.
        """
        self.ensure_one()
        if not self.oauth2_authorize_url:
            raise UserError(self.env._("Set the Authorization URL of connection %s first.", self.name))
        verifier = secrets.token_urlsafe(64)
        self.sudo().oauth2_code_verifier = verifier
        params = {
            "response_type": "code",
            "client_id": self.oauth2_client_id,
            "redirect_uri": self.oauth2_redirect_uri,
            "scope": self.oauth2_scope,
            "state": f"{self.id}.{self._oauth2_state_signature(verifier)}",
        }
        if self.oauth2_use_pkce:
            digest = hashlib.sha256(verifier.encode()).digest()
            params["code_challenge"] = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            params["code_challenge_method"] = "S256"
        query = urlencode({key: value for key, value in params.items() if value})
        separator = "&" if "?" in self.oauth2_authorize_url else "?"
        return {"type": "ir.actions.act_url", "url": f"{self.oauth2_authorize_url}{separator}{query}", "target": "self"}

    # ------------------------------------------------------------
    # Transport API
    # ------------------------------------------------------------

    def _edi_connect(self):
        """Open a session with the remote system.

        :edi-hook: framework
        :return: a transport-specific session, to use as a context manager (closed on exit)
        :raise EdiTransportError: if the remote system cannot be reached or refuses the credentials
        """
        self.ensure_one()
        if self.transport != "http":
            raise NotImplementedError(f"Transport {self.transport} is not implemented")
        return self._http_connect()

    def _edi_receive(self, session, location, *, params=None, method="get", pattern=None, limit=0, modified_after=None):
        """Fetch the data available at ``location``.

        :edi-hook: framework
        :param session: a session returned by ``_edi_connect``
        :param str location: endpoint path or remote folder, relative to the connection URL
        :param dict params: query parameters (request based transports)
        :param str method: request method (request based transports)
        :param str pattern: file name pattern, e.g. ``*.csv`` (file based transports)
        :param int limit: max number of messages to return, 0 for no limit (file based transports)
        :param datetime modified_after: only the files modified after this date, e.g. the date of
            the last file processed, when they are left in place (file based transports)
        :rtype: list[EdiMessage]
        """
        self.ensure_one()
        if self.transport_file_based:
            return self._file_receive(session, location, pattern=pattern, limit=limit, modified_after=modified_after)
        if self.transport != "http":
            raise NotImplementedError(f"Transport {self.transport} is not implemented")
        return [self._http_request(session, method, location, params=params)]

    def _edi_send(self, session, location, content, *, name=None, params=None, method="post", content_type=None):
        """Send ``content`` to ``location``.

        :edi-hook: framework
        :param session: a session returned by ``_edi_connect``
        :param str location: endpoint path or remote folder, relative to the connection URL
        :param bytes content: payload to send
        :param str name: file name (file based transports)
        :param dict params: query parameters (request based transports)
        :param str method: request method (request based transports)
        :param str content_type: MIME type of the payload (request based transports)
        :return: the answer of the remote system, if any
        :rtype: EdiMessage | None
        """
        self.ensure_one()
        if self.transport_file_based:
            return self._file_send(session, location, content, name)
        if self.transport != "http":
            raise NotImplementedError(f"Transport {self.transport} is not implemented")
        headers = {"Content-Type": content_type} if content_type else None
        return self._http_request(session, method, location, params=params, data=content, headers=headers)

    def _edi_acknowledge(self, session, message, success):
        """Tell the remote system that a received message has been handled.

        File based transports move or delete the file, according to ``file_processed_action`` and
        ``file_failed_folder``. Default: nothing to do.

        :edi-hook: framework
        :param session: a session returned by ``_edi_connect``
        :param EdiMessage message: a message returned by ``_edi_receive``
        :param bool success: whether the message has been processed successfully
        """
        self.ensure_one()
        if self.transport_file_based:
            self._file_acknowledge(session, message, success)

    def _edi_test(self, session):
        """Check that the session is usable, beyond being opened.

        :edi-hook: framework
        :param session: a session returned by ``_edi_connect``
        :raise EdiTransportError: if it is not
        """
        self.ensure_one()
        if self.transport != "http" or self.auth_type == "oauth2":
            # opening the session already proved the credentials (token fetched, login done, ...)
            return
        response = self._http_send_request(session, "get", self._http_url(""))
        if response.status_code in (401, 403):
            raise EdiTransportError(f"{response.status_code} {response.reason}", response.content)

    # ------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------

    def _http_connect(self):
        connection = self.sudo()  # credentials are restricted to administrators
        session = EdiHttpSession()
        try:
            if connection.http_use_headers:
                session.headers.update(connection._http_parse_headers())
            if connection.tls_client_auth:
                # removed when the session is closed
                session.cert = session.exit_stack.enter_context(connection._tls_client_certificate_files())
            connection._http_authenticate(session)
        except Exception:
            session.close()
            raise
        return session

    def _http_authenticate(self, session):
        """Set the credentials of ``auth_type`` on ``session``, when it is opened.

        A module adding an authentication specific to one remote system (request signing, login call
        returning a session cookie, ...) adds it to the ``auth_type`` selection, to the ``auth_types``
        of the transports (``_get_transports``) and to ``_get_auth_fields`` with its credential
        fields, then implements it here, calling ``super()`` for the others::

            def _http_authenticate(self, session):
                if self.auth_type != "acme_hmac":
                    return super()._http_authenticate(session)
                session.auth = AcmeHmacAuth(self.sudo().acme_key)  # a requests.auth.AuthBase

        :edi-hook: client
        :param EdiHttpSession session: the session to authenticate, a ``requests.Session``
        :raise EdiTransportError: if the remote system refuses the credentials
        """
        connection = self.sudo()
        if connection.auth_type == "basic":
            session.auth = (connection.username or "", connection.password or "")
        elif connection.auth_type == "api_key":
            key = connection.api_key or ""
            value = f"{connection.api_key_prefix} {key}" if connection.api_key_prefix else key
            if connection.api_key_location == "query":
                session.params[connection.api_key_name] = value
            else:
                session.headers[connection.api_key_name] = value
        elif connection.auth_type == "oauth2":
            session.headers["Authorization"] = f"Bearer {connection._oauth2_get_token(session)}"
        elif connection.auth_type != "none":
            raise EdiTransportError(self.env._("Authentication %s is not implemented for HTTP.", connection.auth_type))

    def _http_parse_headers(self):
        """Parse ``http_headers``, one ``Name: value`` per line.

        :rtype: dict[str, str]
        :raise ValueError: on a line that is not a valid header
        """
        headers = {}
        for line in (self.http_headers or "").splitlines():
            if not line.strip():
                continue
            name, separator, value = line.partition(":")
            name = name.strip()
            if not separator or not HTTP_HEADER_NAME_RE.fullmatch(name):
                raise ValueError(self.env._("%s is not a valid header, expected Name: value", line.strip()))
            headers[name] = value.strip()
        return headers

    def _http_url(self, location):
        return urljoin(self.url, location.strip()) if location else self.url

    def _http_send_request(self, session, method, url, **kwargs):
        try:
            return session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.exceptions.RequestException as e:
            # no answer at all (DNS, timeout, TLS, ...)
            raise EdiTransportError(str(e)) from e

    def _http_request(self, session, method, location, **kwargs):
        """Send a request to ``location`` and return the answer, raising on HTTP errors.

        :rtype: EdiMessage
        :raise EdiTransportError: on network or HTTP error
        """
        url = self._http_url(location)
        response = self._http_send_request(session, method, url, **kwargs)
        if response.status_code == 401 and self.auth_type == "oauth2":
            # the cached token may have been revoked before its expiry: renew it once
            self._oauth2_reset_token()
            session.headers["Authorization"] = f"Bearer {self._oauth2_get_token(session)}"
            response = self._http_send_request(session, method, url, **kwargs)
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise EdiTransportError(str(e), response.content) from e
        return EdiMessage(
            name=urlsplit(url).path.rsplit("/", 1)[-1] or url,
            content=response.content,
            ref=url,
            meta={"status": response.status_code, "content_type": response.headers.get("Content-Type")},
        )

    # ------------------------------------------------------------
    # File based transports (FTP, SFTP, ...)
    # ------------------------------------------------------------
    # The transport modules implement the _file_* primitives below, the logic (sorting,
    # filtering, temporary upload, conflicts, ...) is common to all of them.

    def _file_root(self):
        """Folder of the URL, all the locations are relative to it.

        :return: the path of the URL (``sftp://host/acme``: ``/acme``, ``sftp://host/``: ``/``), or
            ``.`` (the login directory of the user) if it has none (``sftp://host``)
        """
        path = urlsplit(self.url).path
        return posixpath.normpath(path) if path else "."

    def _file_path(self, location, name=None):
        """Remote path of ``location`` (and ``name``), relative to the folder of the URL.

        :return: an absolute path, or a path relative to the login directory if the URL has no path
        :raise EdiTransportError: if the path is outside of the folder of the URL (e.g. with ``..``),
            or if ``name`` is not a plain file name
        """
        root = self._file_root()
        path = posixpath.normpath(posixpath.join(root, (location or "").lstrip("/")))
        if name is not None:
            if name in ("", ".", "..") or "/" in name or "\0" in name:
                raise EdiTransportError(self.env._("%s is not a valid file name.", repr(name)))
            path = posixpath.normpath(posixpath.join(path, name))
        if root == ".":
            inside = path != ".." and not path.startswith("../")
        else:
            inside = path == root or path.startswith(root.rstrip("/") + "/")
        if not inside:
            raise EdiTransportError(
                self.env._(
                    "%(path)s is outside of the folder %(root)s of connection %(name)s.",
                    path=path,
                    root=root,
                    name=self.name,
                )
            )
        return path

    def _file_receive(self, session, location, *, pattern=None, limit=0, modified_after=None):
        folder = self._file_path(location)
        files = self._file_list(session, folder)
        if pattern:
            files = [(name, meta) for name, meta in files if fnmatch.fnmatch(name, pattern)]
        if modified_after:
            # files without date are kept: better received twice than never
            files = [(name, meta) for name, meta in files if not meta.get("modify") or meta["modify"] > modified_after]
        # the processed files leaving the folder, the most recent ones first; otherwise the oldest ones
        # first, the caller asking for the ones after the last processed one (modified_after)
        files = self._file_sort(files, newest_first=self.file_processed_action != "keep")
        if limit:
            files = files[:limit]
        messages = []
        for name, meta in files:
            path = posixpath.normpath(posixpath.join(folder, name))
            messages.append(EdiMessage(name=name, content=self._file_read(session, path), ref=path, meta=meta))
        return messages

    def _file_send(self, session, location, content, name):
        if not name:
            raise EdiTransportError(self.env._("A file name is required to send a file."))
        path = self._file_path(location, name)
        replace = self._file_prepare_target(session, path)
        # upload under a temporary name, so that the remote system never picks up a partial file
        uploading_path = f"{path}.{secrets.token_urlsafe(8)}.filepart"
        try:
            self._file_write(session, uploading_path, content)
            if replace:
                self._file_replace(session, uploading_path, path)
            else:
                self._file_rename(session, uploading_path, path)
        except EdiTransportError:
            self._file_delete_quietly(session, uploading_path)  # do not leave a partial upload behind
            raise

    def _file_acknowledge(self, session, message, success):
        if success and self.file_processed_action == "delete":
            self._file_delete(session, message.ref)
            return
        if self.file_processed_action != "move":
            return  # left in place: once processed (keep), or to be retried (failed)
        folder = self.file_success_folder if success else self.file_failed_folder
        if not folder:
            return  # failed, without failed folder: left in place, to be retried
        path = self._file_path(self._file_processed_location(message, folder), message.name)
        if self._file_prepare_target(session, path):
            self._file_replace(session, message.ref, path)
        else:
            self._file_rename(session, message.ref, path)

    def _file_processed_location(self, message, folder):
        """Location of ``folder`` (success or failed folder) for a received file.

        :return: ``folder`` relative to the folder of the file, or as is if it starts with ``/``,
            relative to the path of the URL (as expected by ``_file_path``)
        """
        if folder.startswith("/"):
            return folder
        file_folder = posixpath.dirname(message.ref) or "."
        return posixpath.join(posixpath.relpath(file_folder, self._file_root()), folder)

    def _file_prepare_target(self, session, path):
        """Make room for a file at ``path``, according to ``file_on_conflict``.

        :return: whether a file exists at ``path`` and must be replaced (with ``_file_replace``)
        :rtype: bool
        :raise EdiTransportError: if a file exists and ``file_on_conflict`` is ``fail``
        """
        if not self._file_exists(session, path):
            return False
        if self.file_on_conflict == "replace":
            return True
        if self.file_on_conflict == "rename":
            self._file_rename(session, path, f"{path}-{datetime.now(UTC):%Y%m%d-%H%M%S}-utc.old")
            return False
        raise EdiTransportError(self.env._("File %s already exists on the remote system.", path))

    def _file_sort(self, files, newest_first):
        """Sort the listed files by date when the transport gives it, by name otherwise.

        :param list[tuple[str, dict]] files: as returned by ``_file_list``
        :param bool newest_first: most recent first, or oldest first
        :rtype: list[tuple[str, dict]]
        """
        if all(meta.get("modify") for _name, meta in files):
            return sorted(files, key=lambda file: (file[1]["modify"], file[0]), reverse=newest_first)
        return sorted(files, reverse=newest_first)

    def _file_delete_quietly(self, session, path):
        """Delete ``path`` if possible, for a cleanup that must not hide the original error."""
        try:
            self._file_delete(session, path)
        except EdiTransportError as e:
            _logger.warning("EDI connection %s: could not delete %s: %s", self.id, path, e)

    def _file_list(self, session, path):
        """List the files (not the folders) of the remote folder ``path``, in any order.

        The caller filters, sorts (most recent first) and limits them.

        :edi-hook: framework
        :return: the (file name, meta) tuples, meta being transport specific, with if possible
            ``size`` (int) and ``modify`` (aware datetime of the last modification, to sort them)
        :rtype: list[tuple[str, dict]]
        :raise EdiTransportError: on any transport error
        """
        raise NotImplementedError(f"Transport {self.transport} is not file based")

    def _file_exists(self, session, path):
        """Return whether the remote file ``path`` exists.

        :edi-hook: framework
        :rtype: bool
        :raise EdiTransportError: on any transport error
        """
        folder, name = posixpath.split(path)
        return any(file_name == name for file_name, _meta in self._file_list(session, folder))

    def _file_read(self, session, path):
        """Return the content of the remote file ``path``.

        :edi-hook: framework
        :rtype: bytes
        :raise EdiTransportError: on any transport error
        """
        raise NotImplementedError(f"Transport {self.transport} is not file based")

    def _file_write(self, session, path, content):
        """Write ``content`` to the remote file ``path``, replacing it if it exists.

        :edi-hook: framework
        :param bytes content: content of the file
        :raise EdiTransportError: on any transport error
        """
        raise NotImplementedError(f"Transport {self.transport} is not file based")

    def _file_rename(self, session, path, new_path):
        """Rename (move) the remote file ``path`` to ``new_path``.

        :edi-hook: framework
        :raise EdiTransportError: on any transport error
        """
        raise NotImplementedError(f"Transport {self.transport} is not file based")

    def _file_replace(self, session, path, target):
        """Move the remote file ``path`` over the existing file ``target``.

        Default: ``target`` is first renamed aside, and deleted once ``path`` took its place (or put
        back if that fails), so that it is never lost; but ``target`` is missing for an instant.
        Override it with an atomic rename when the transport has one (e.g. SFTP posix-rename).

        :edi-hook: framework
        :raise EdiTransportError: on any transport error
        """
        backup_path = f"{target}.{secrets.token_urlsafe(8)}.replaced"
        self._file_rename(session, target, backup_path)
        try:
            self._file_rename(session, path, target)
        except EdiTransportError:
            try:
                self._file_rename(session, backup_path, target)
            except EdiTransportError as e:
                _logger.error("EDI connection %s: could not restore %s from %s: %s", self.id, target, backup_path, e)
            raise
        self._file_delete_quietly(session, backup_path)

    def _file_delete(self, session, path):
        """Delete the remote file ``path``.

        :edi-hook: framework
        :raise EdiTransportError: on any transport error
        """
        raise NotImplementedError(f"Transport {self.transport} is not file based")

    # ------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------

    def _oauth2_get_token(self, session=None):
        """Return a valid OAuth2 access token, fetching a new one if the cached one expired.

        :param EdiHttpSession session: session used to reach the token endpoint (client certificate)
        :rtype: str
        :raise EdiTransportError: if the token endpoint refuses the credentials
        """
        self.ensure_one()
        connection = self.sudo()
        if (
            connection.oauth2_access_token
            and connection.oauth2_token_expiry
            and connection.oauth2_token_expiry > fields.Datetime.now()
        ):
            return connection.oauth2_access_token

        if connection.oauth2_grant_type == "authorization_code":
            if not connection.oauth2_refresh_token:
                raise EdiTransportError(
                    self.env._("Connection %s is not authorized yet: click on Connect.", connection.name)
                )
            payload = {"grant_type": "refresh_token", "refresh_token": connection.oauth2_refresh_token}
        else:
            payload = {"grant_type": connection.oauth2_grant_type, "scope": connection.oauth2_scope}
            if connection.oauth2_grant_type == "password":
                payload.update(username=connection.username, password=connection.password)
        return connection._oauth2_request_token(payload, session)

    def _oauth2_request_token(self, payload, session=None):
        """Call the token endpoint with ``payload`` and store the tokens it answers.

        :param dict payload: grant specific parameters, the client credentials are added
        :param EdiHttpSession session: session used to reach the token endpoint (client certificate)
        :return: the access token
        :rtype: str
        :raise EdiTransportError: if the token endpoint refuses the request
        """
        self.ensure_one()
        connection = self.sudo()
        if not connection.oauth2_token_url:
            raise EdiTransportError(self.env._("No token URL configured on connection %s.", connection.name))
        payload = {
            "client_id": connection.oauth2_client_id,
            "client_secret": connection.oauth2_client_secret,
            **payload,
        }
        _logger.info(
            "Requesting OAuth2 token (%s) for EDI connection %s from %s",
            payload["grant_type"],
            connection.id,
            connection.oauth2_token_url,
        )
        with contextlib.ExitStack() as stack:
            if session is None:
                session = stack.enter_context(EdiHttpSession())
                if connection.tls_client_auth:
                    session.cert = stack.enter_context(connection._tls_client_certificate_files())
            try:
                # a dedicated request: the session headers carry the resource credentials, not these
                response = requests.post(
                    connection.oauth2_token_url,
                    data={key: value for key, value in payload.items() if value},
                    cert=session.cert,
                    timeout=connection.timeout,
                )
                response.raise_for_status()
                token_data = response.json()
            except requests.exceptions.RequestException as e:
                response = e.response
                content = response.content if response is not None else None
                if (
                    payload["grant_type"] == "refresh_token"
                    and response is not None
                    and response.status_code in (400, 401)
                ):
                    # the refresh token has been revoked or expired (nothing to store: the transaction is rolled back)
                    raise EdiTransportError(
                        self.env._(
                            "The authorization of connection %s has been revoked or expired: click on Connect again.",
                            connection.name,
                        ),
                        content,
                    ) from e
                raise EdiTransportError(self.env._("OAuth2 authentication failed: %s", e), content) from e
            except ValueError as e:
                raise EdiTransportError(
                    self.env._("OAuth2 token endpoint did not answer JSON."), response.content
                ) from e

        token = token_data.get("access_token")
        if not token:
            raise EdiTransportError(self.env._("No access_token in the OAuth2 answer."), response.content)
        expires_in = int(token_data.get("expires_in") or OAUTH2_TOKEN_EXPIRY_FALLBACK)
        vals = {
            "oauth2_access_token": token,
            "oauth2_token_expiry": fields.Datetime.now()
            + timedelta(seconds=max(expires_in - OAUTH2_TOKEN_EXPIRY_MARGIN, 0)),
        }
        if token_data.get("refresh_token"):  # first authorization, or rotation of the refresh token
            vals["oauth2_refresh_token"] = token_data["refresh_token"]
        connection.write(vals)
        return token

    def _oauth2_reset_token(self):
        self.sudo().write({"oauth2_access_token": False, "oauth2_token_expiry": False})

    def _oauth2_state_signature(self, verifier):
        """Sign the authorization request, bound to the connection, the user and the pending verifier."""
        self.ensure_one()
        return hmac(self.env(su=True), "edi_oauth2_state", f"{self.id}:{self.env.uid}:{verifier}")

    def _oauth2_authorization_callback(self, state, code):
        """Exchange the authorization ``code`` received on the redirect URI for tokens.

        :param str state: the state sent by ``action_oauth2_authorize``, echoed by the remote system
        :param str code: the authorization code
        :raise UserError: if the state does not match a pending authorization of the current user
        :raise EdiTransportError: if the token endpoint refuses the code
        """
        self.ensure_one()
        connection = self.sudo()
        verifier = connection.oauth2_code_verifier
        signature = (state or "").partition(".")[2]
        if not verifier or not consteq(signature, self._oauth2_state_signature(verifier)):
            raise UserError(self.env._("This authorization request is invalid or expired, click on Connect again."))
        connection.oauth2_code_verifier = False  # one-time
        payload = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.oauth2_redirect_uri}
        if connection.oauth2_use_pkce:
            payload["code_verifier"] = verifier
        connection._oauth2_request_token(payload)
        self.message_post(body=self.env._("Access authorized by %s.", self.env.user.name))

    # ------------------------------------------------------------
    # Client certificate (mutual TLS)
    # ------------------------------------------------------------

    def _tls_load_client_certificate(self):
        """Load the client certificate chain and its private key.

        :return: (certificates, private key), the first certificate being the client's one
        :rtype: tuple[list[x509.Certificate], PrivateKeyTypes]
        :raise ValueError: if the files or the password are invalid
        """
        self.ensure_one()
        connection = self.sudo()
        data = connection.tls_client_certificate.content
        password = connection.tls_client_password.encode() if connection.tls_client_password else None
        if b"-----BEGIN" in data:
            certificates = x509.load_pem_x509_certificates(data)
            key_data = connection.tls_client_key.content if connection.tls_client_key else data
            try:
                key = serialization.load_pem_private_key(key_data, password)
            except TypeError as e:  # password missing or not expected
                raise ValueError(str(e)) from e
        else:
            key, certificate, extra_certificates = pkcs12.load_key_and_certificates(data, password)
            if not certificate:
                raise ValueError(self.env._("No certificate found in the PKCS#12 file."))
            certificates = [certificate, *extra_certificates]
        if not key:
            raise ValueError(self.env._("No private key found."))
        return certificates, key

    @contextlib.contextmanager
    def _tls_client_certificate_files(self):
        """Write the client certificate and its key in temporary files, as most TLS libraries need.

        The key is written unencrypted, readable by the Odoo user only, and removed on exit.

        :return: context manager yielding the (certificate path, key path) tuple
        """
        certificates, key = self._tls_load_client_certificate()
        with tempfile.TemporaryDirectory(prefix="edi_tls_") as directory:
            cert_path = os.path.join(directory, "cert.pem")
            key_path = os.path.join(directory, "key.pem")
            for path, content in [
                (cert_path, b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in certificates)),
                (
                    key_path,
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    ),
                ),
            ]:
                with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "wb") as file:
                    file.write(content)
            yield cert_path, key_path
