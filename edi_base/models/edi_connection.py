# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.addons.edi_base.decorators import IntegrationCheck
from odoo.exceptions import UserError, ValidationError
from odoo.tools import ormcache
from odoo.tools.urls import urljoin as url_join


_logger = logging.getLogger(__name__)


class Connection(models.Model):
    """
    Object used by the integration performing the gateway between odoo and the third party component
    """

    _name = "edi.connection"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "EDI Connection"

    name = fields.Char(required=True)
    type = fields.Selection(selection=[], required=True, string="EDI Type", tracking=True)
    configuration = fields.Text(tracking=True)
    company_id = fields.Many2one("res.company", tracking=True)
    integration_ids = fields.One2many("edi.integration", "connection_id", readonly=True, context={"active_test": False})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)

        for rec in records:
            # set-default config if not given
            rec._set_default_configuration()

        return records

    def reset_config(self):
        self.ensure_one()
        self.configuration = json.dumps(self._get_default_configuration(), indent=4, sort_keys=True)

    def test(self):
        """
        Test the connection is successful with the third party component

        Should raise an exception with Success or Failed message

        To implement in each connection
        ....
        """
        raise NotImplementedError("No test method implemented for this type of connection")

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """
        Send the content to the third party component (out flows)

        To implement in each connection
        ....

        :param filename: str
        :param content: str
        """
        raise NotImplementedError("No send_synchronization method implemented for this type of connection")

    def _fetch_synchronizations(self, *args, **kwargs):
        """Fetch the content to process (in flows)

        To implement in each connection
        ....

        :return: list of dict
            the dict should be {
                'filename': FILENAME (str),
                'content': str or dict: will be handled by in edi.integration._process_content & will be written
                in edi.synchronization.content field
            }
        """
        raise NotImplementedError("No fetch_synchronizations method implemented for this type of connection")

    def _clean_synchronization_in(self, data, status, *args, **kwargs):
        """Clean the synchronization (in flows)

        To implement in each connection
        ....

        :param data: dict (returned from _fetch_synchronizations)
        :param status: str
            - done if everything went well
            - error if there is something that went wrong

        Default behavior: Do Nothing
        """
        return

    def _clean_synchronization_out(self, filename, status, *args, **kwargs):
        """Clean the synchronization (out flows)

        To implement in each connection
        ....

        :param filename: str
        :param status: str
            - done if everything went well
            - error if there is something that went wrong

        Default behavior: Do Nothing
        """
        return

    def _get_default_configuration(self):
        """
        Return a dictionnary with the template configuration for
        this type of connection

        To implement in each connection
        self.ensure_one()
        ....

        :return: dict
        """
        self.ensure_one()
        return {}

    ###################################
    #    End of abstract interface    #
    #  don't override these methods   #
    ###################################

    @api.onchange("type")
    def _set_default_configuration(self):
        self.ensure_one()
        if not self.configuration or self.configuration == "{}":
            self.configuration = json.dumps(self._get_default_configuration(), indent=4, sort_keys=True)

    def _read_configuration(self):
        """
        :return: dict
        """
        self.ensure_one()
        return json.loads(self.configuration)

    @property
    @ormcache("self.configuration")
    def json_configuration(self):
        return self._read_configuration()

    def _get_configuration_value(self, name, raise_if_not_found=False):
        self.ensure_one()
        value = self.json_configuration.get(name)
        if raise_if_not_found and not value:
            raise ValidationError(_("No %s defined in configuration", name))
        return value


API_DEFAULT_TIMEOUT = 30  # TODO make it configurable ?
API_TOKEN_EXPIRY_FALLBACK = 3600  # the real value should always come from the token response


class ConnectionApi(models.Model):
    _inherit = "edi.connection"
    _description = "EDI Connection"

    type = fields.Selection(selection_add=[("api", "HTTP API")], ondelete={"api": "cascade"})

    # Base URL shared by all integrations on this connection
    url = fields.Char(string="Base URL", help="e.g. https://api.example.com")

    # Auth type
    api_auth_type = fields.Selection(
        [
            ("public", "Public"),
            ("api_key", "API Key"),
            ("basic", "Basic Auth (Login/Password)"),
            ("oauth2", "OAuth2 (Get a token first)"),
        ],
        default=False,
        string="Auth Type",
        tracking=True,
        help="OAuth2 Auth Type does rely on RFC 6749",
    )

    # Credentials — used depending on api_auth_type
    key = fields.Char(string="Key", copy=False)
    username = fields.Char()
    password = fields.Char()

    # OAuth2 token endpoint
    grant_type = fields.Selection(
        [
            ("client_credentials", "Client Credentials"),
            ("password", "Password"),
        ],
        string="Grant Type",
        default="client_credentials",
    )
    client_id = fields.Char()
    client_secret = fields.Char()
    scope = fields.Char()
    token_path = fields.Char(
        string="Token Path",
        help="Relative path of the OAuth2 token endpoint, e.g. /oauth/token",
    )

    # Cached OAuth2 token
    api_token = fields.Char(readonly=True, copy=False, groups="base.group_system")
    api_token_expires = fields.Datetime(
        string="Token Expires On", readonly=True, copy=False, groups="base.group_system"
    )

    def _api_call(self, path, method, payload=None, headers=None, *args, **kwargs):
        """Default HTTP implementation for API connections.

        Uses _api_get_session() for authentication (dispatches on api_auth_type).
        GET/DELETE
            → payload sent as query params
        POST/PUT/PATCH
            → dict/list sent as JSON body
            → str sent as raw body
              for XML/CSV/pre-serialized JSON — caller sets Content-Type via `headers=` when needed
            → None → no body

        Always returns response.text (raw string). Callers are responsible for
        parsing the content based on the expected format.

        :param path: str — relative URL path, joined to self.url
        :param method: str — HTTP method (get/post/put/patch/delete)
        """
        self.ensure_one()

        url = url_join(self.url.strip(), path.strip()) if path else self.url.strip()

        if method in ("get", "delete"):
            req_kwargs = {"params": payload}
        elif isinstance(payload, (dict, list)):
            req_kwargs = {"json": payload}
        else:
            req_kwargs = {"data": payload}

        if headers:
            req_kwargs["headers"] = headers

        with self._api_get_session() as session:
            fn = getattr(session, method)
            try:
                response = fn(url, timeout=API_DEFAULT_TIMEOUT, **req_kwargs)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                raise ValidationError(self.env._("HTTP call failed: %s", e)) from e

        return response.text

    def _api_get_session(self):
        """Return an authenticated requests.Session for this connection.

        Usable as a context manager: ``with self._api_get_session() as session:``.

        Default behavior dispatches on api_auth_type:
            - public   → plain session
            - api_key  → Authorization: Bearer <self.key>
            - basic    → HTTPBasicAuth(self.username, self.password) — RFC 7617
            - oauth2   → Authorization: Bearer <token from _api_get_token()>
        """
        self.ensure_one()
        session = requests.Session()
        if self.api_auth_type == "api_key":
            if not self.key:
                raise ValidationError(self.env._("No API key defined on the connection."))
            session.headers["Authorization"] = "Bearer " + self.key
        elif self.api_auth_type == "basic":
            if not self.username:
                raise ValidationError(self.env._("No username defined on the connection."))
            session.auth = (self.username, self.password or "")
        elif self.api_auth_type == "oauth2":
            session.headers["Authorization"] = "Bearer " + self._api_get_token()
        return session

    def _api_get_token(self):
        """Obtain and cache an access token via token_path.

        Uses the cached token if still valid. Override _api_get_token_payload() to
        customize the request body.

        Call api_reset_token() first to force re-authentication (e.g. from test()).

        :return: str — the access token
        :raises: ValidationError on auth failure or missing configuration
        """
        self.ensure_one()

        sudo_conn = self.sudo()  # for api_token, since the integration is run with integration.user_id
        if sudo_conn.api_token and sudo_conn.api_token_expires and sudo_conn.api_token_expires > fields.Datetime.now():
            _logger.info("Using cached token (expires %s)", sudo_conn.api_token_expires)
            return sudo_conn.api_token

        if not self.url or not self.token_path:
            raise ValidationError(self.env._("No token endpoint properly configured on this connection."))

        url = url_join(self.url.strip(), self.token_path.strip())
        _logger.info("Fetching token from %s", url)

        payload = self._api_get_token_payload()
        try:
            response = requests.post(url, data=payload, timeout=API_DEFAULT_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise ValidationError(self.env._("Authentication failed: %s", e)) from e

        token, expires_in = self._api_parse_token_response(response)
        if not token:
            raise ValidationError(self.env._("No access_token in response: %s", response))

        sudo_conn.write(
            {
                "api_token": token,
                "api_token_expires": fields.Datetime.now() + timedelta(seconds=expires_in),
            }
        )
        return token

    def _api_get_token_payload(self):
        """Build the payload for the token request.

        :return: dict
        """
        self.ensure_one()
        token_fields = ["grant_type", "username", "password", "client_id", "client_secret", "scope"]
        return {f: getattr(self, f) for f in token_fields if getattr(self, f)}

    @api.model
    def _api_parse_token_response(self, response):
        data = response.json()
        return data.get("access_token"), data.get("expires_in", API_TOKEN_EXPIRY_FALLBACK)

    def api_reset_token(self):
        """Clear the cached token, forcing re-authentication on the next _api_get_token() call."""
        self.ensure_one()
        self.sudo().write({"api_token": False, "api_token_expires": False})

    @IntegrationCheck(["api"])
    def test(self):
        self.ensure_one()
        if self.api_auth_type == "oauth2":
            self.api_reset_token()
            self._api_get_token()
            return self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                "simple_notification",
                {
                    "type": "success",
                    "message": self.env._("Authentication succeeded — token obtained."),
                },
            )

        raise UserError(_("Not applicable for this type of connection"))
