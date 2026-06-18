# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.addons.edi_base.decorators import IntegrationCheck
from odoo.exceptions import UserError, ValidationError
from odoo.tools import ormcache


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
API_TOKEN_EXPIRY_FALLBACK = 3600  # the real value should always come from the endpoint token response


class ConnectionApi(models.Model):
    _inherit = "edi.connection"
    _description = "EDI Connection"

    type = fields.Selection(selection_add=[("api", "HTTP API")], ondelete={"api": "cascade"})
    api_endpoint_ids = fields.One2many("edi.endpoint", "connection_id", string="Endpoints")
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
    api_token_endpoint_id = fields.Many2one(
        "edi.endpoint",
        string="Token Endpoint",
        compute="_compute_api_token_endpoint_id",
        inverse="_inverse_api_token_endpoint_id",
        help="Endpoint used to obtain an OAuth2 access token.",
        store=False,
    )
    api_token = fields.Char(readonly=True, copy=False, groups="base.group_system")
    api_token_expires = fields.Datetime(
        string="Token Expires On", readonly=True, copy=False, groups="base.group_system"
    )

    @api.depends("api_endpoint_ids.role")
    def _compute_api_token_endpoint_id(self):
        for conn in self:
            conn.api_token_endpoint_id = conn.api_endpoint_ids.filtered(lambda e: e.role == "token")[:1]

    def _inverse_api_token_endpoint_id(self):
        for conn in self:
            if conn.api_token_endpoint_id and conn.api_token_endpoint_id.connection_id != conn:
                raise ValidationError(
                    self.env._(
                        "Token endpoint '%s' does not belong to this connection.",
                        conn.api_token_endpoint_id.name,
                    )
                )
            conn.api_endpoint_ids.filtered(lambda e: e.role == "token").write({"role": "resource"})
            if conn.api_token_endpoint_id:
                conn.api_token_endpoint_id.role = "token"

    def _api_call(self, endpoint, payload=None, headers=None, *args, **kwargs):
        """Default HTTP implementation for API connections.

        Uses _api_get_session(endpoint) for authentication (dispatches on api_auth_type).
        GET
            → payload sent as query params
        POST/PUT/PATCH
            → dict/list sent as JSON body
            → str sent as raw body
              for XML/CSV/pre-serialized JSON — caller sets Content-Type via `headers=` when needed
            → None → no body

        Always returns response.text (raw string). Callers are responsible for
        parsing the content based on the expected format.
        """
        self.ensure_one()

        if endpoint.method in ("get", "delete"):
            req_kwargs = {"params": payload}
        elif isinstance(payload, (dict, list)):
            req_kwargs = {"json": payload}
        else:
            req_kwargs = {"data": payload}

        if headers:
            req_kwargs["headers"] = headers

        with self._api_get_session(endpoint) as session:
            fn = getattr(session, endpoint.method)
            try:
                response = fn(endpoint.url, timeout=API_DEFAULT_TIMEOUT, **req_kwargs)
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                raise ValidationError(self.env._("HTTP call failed: %s", e)) from e

        return response.text

    def _api_get_session(self, endpoint=None):
        """Return an authenticated requests.Session for this connection.

        Usable as a context manager: ``with self._api_get_session(endpoint) as session:``.

        Default behavior dispatches on api_auth_type:
            - public   → plain session
            - api_key  → Authorization: Bearer <endpoint.api_key>
            - basic    → HTTPBasicAuth(endpoint.username, endpoint.password) — RFC 7617
            - oauth2   → Authorization: Bearer <token from _api_get_token()>
        """
        self.ensure_one()
        session = requests.Session()
        if self.api_auth_type == "api_key":
            api_key = endpoint.api_key if endpoint else ""
            if not api_key:
                raise ValidationError(self.env._("No API key defined on the endpoint."))
            session.headers["Authorization"] = "Bearer " + api_key
        elif self.api_auth_type == "basic":
            username = endpoint.username if endpoint else ""
            password = endpoint.password if endpoint else ""
            if not username:
                raise ValidationError(self.env._("No username defined on the endpoint."))
            session.auth = (username, password or "")
        elif self.api_auth_type == "oauth2":
            session.headers["Authorization"] = "Bearer " + self._api_get_token()
        return session

    def _api_get_token(self):
        """Obtain and cache an access token via api_token_endpoint_id.

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

        url = self.api_token_endpoint_id.url
        if not url:
            raise ValidationError(self.env._("No token endpoint properly configured on this connection."))
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
        endpoint = self.api_token_endpoint_id
        token_fields = ["grant_type", "username", "password", "client_id", "client_secret", "scope"]
        return {f: getattr(endpoint, f) for f in token_fields if getattr(self.api_token_endpoint_id, f)}

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
            # raise UserError(self.env._("Authentication succeeded — token obtained."))
            return self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                "simple_notification",
                {
                    "type": "success",
                    "message": self.env._("Authentication succeeded — token obtained."),
                },
            )

        raise UserError(_("Not applicable for this type of connection"))
