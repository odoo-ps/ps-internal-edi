# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from ..models.edi_connection import OAUTH2_REDIRECT_PATH
from ..tools import EdiTransportError


_logger = logging.getLogger(__name__)


class EdiOAuth2Controller(http.Controller):
    @http.route(OAUTH2_REDIRECT_PATH, type="http", auth="user", methods=["GET"])
    def edi_oauth2_callback(self, state=None, code=None, error=None, error_description=None, **kwargs):
        """Redirect URI of the OAuth2 authorization code grant, see ``edi.connection.action_oauth2_authorize``."""
        connection_id = (state or "").partition(".")[0]
        try:
            connection = request.env["edi.connection"].browse(int(connection_id))
            connection.check_access("write")
        except (ValueError, AccessError, MissingError):
            _logger.warning("EDI OAuth2: invalid state %r", state)
            raise Forbidden() from None

        if error or not code:
            connection.message_post(
                body=request.env._("Authorization refused: %s", error_description or error or "no code received")
            )
        else:
            try:
                connection._oauth2_authorization_callback(state, code)
            except (UserError, EdiTransportError) as e:
                connection.message_post(body=request.env._("Authorization failed: %s", e))
        return request.redirect(f"/odoo/edi.connection/{connection.id}")
