import json
import logging

from odoo.upgrade import util


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """19.0 -> 20.0: split the connection into ``transport`` + ``auth_type`` with real credential fields.

    19.0 had a ``type`` (api, ftp, sftp, ...), and either dedicated fields for ``api``
    (``auth_method`` + ``credential_type``) or a free JSON ``configuration`` for ftp/sftp.

    ``configuration`` and the ftp-specific fields are kept until ``end-`` so that the
    transport modules and the flow migration can still read the folders, limits, keys, ...
    """
    # --- transport ---
    util.rename_field(cr, "edi.connection", "type", "transport")
    util.change_field_selection_values(cr, "edi.connection", "transport", {"api": "http"})

    # --- api credentials ---
    util.rename_field(cr, "edi.connection", "auth_method", "auth_type")
    util.change_field_selection_values(
        cr,
        "edi.connection",
        "auth_type",
        {"public": "none", "http_bearer": "api_key", "http_basic": "basic", "http_oauth2": "oauth2"},
    )
    for old, new in [
        ("key", "api_key"),
        ("key_header_name", "api_key_name"),
        ("client_id", "oauth2_client_id"),
        ("client_secret", "oauth2_client_secret"),
        ("scope", "oauth2_scope"),
        ("cached_token", "oauth2_access_token"),
        ("cached_token_expires", "oauth2_token_expiry"),
    ]:
        util.rename_field(cr, "edi.connection", old, new)

    util.create_column(cr, "edi_connection", "api_key_location", "varchar")
    util.create_column(cr, "edi_connection", "api_key_prefix", "varchar")
    util.create_column(cr, "edi_connection", "oauth2_grant_type", "varchar")
    util.create_column(cr, "edi_connection", "oauth2_token_url", "varchar")
    cr.execute(
        """
        UPDATE edi_connection
           SET api_key_location = 'header',
               -- "Bearer {}" -> "Bearer", "{}" -> NULL
               api_key_prefix = NULLIF(TRIM(REPLACE(COALESCE(key_format, 'Bearer {}'), '{}', '')), ''),
               oauth2_grant_type = CASE
                   WHEN credential_type IN ('user_pass', 'user_key_secret') THEN 'password'
                   ELSE 'client_credentials'
               END,
               oauth2_token_url = CASE
                   WHEN token_path ~* '^https?://' THEN token_path
                   WHEN token_path IS NOT NULL
                   THEN RTRIM(url, '/') || '/' || LTRIM(token_path, '/')
               END,
               auth_type = COALESCE(auth_type, 'none')
         WHERE transport = 'http'
        """
    )
    for field in ("credential_type", "key_format", "token_path"):
        util.remove_field(cr, "edi.connection", field)

    # --- ftp / sftp: credentials were in the JSON configuration ---
    util.create_column(cr, "edi_connection", "ssh_private_key", "text")
    cr.execute("SELECT id, transport, configuration FROM edi_connection WHERE transport IN ('ftp', 'sftp')")
    for connection_id, transport, configuration in cr.fetchall():
        try:
            config = json.loads(configuration or "{}")
        except ValueError:
            _logger.warning("edi.connection %s: invalid JSON configuration, credentials not migrated", connection_id)
            continue
        host = config.get("host") or "localhost"
        port = config.get("port")
        url = f"{transport}://{host}:{port}" if port else f"{transport}://{host}"
        ssh_key = transport == "sftp" and config.get("key")
        cr.execute(
            """
            UPDATE edi_connection
               SET url = %s, username = %s, password = %s, auth_type = %s, ssh_private_key = %s
             WHERE id = %s
            """,
            [
                url,
                config.get("user"),
                config.get("password"),
                "ssh_key" if ssh_key else "basic",
                ssh_key or None,
                connection_id,
            ],
        )

    # the URL is stored without surrounding spaces, and used as is
    cr.execute("UPDATE edi_connection SET url = TRIM(url) WHERE url != TRIM(url)")

    # views of the transport modules extend fields that do not exist anymore
    util.remove_view(cr, "edi_sftp_connection.connection_form_view_sftp")
    util.remove_view(cr, "edi_ftp_connection.connection_form_view_ftp")
    util.rename_xmlid(cr, "edi_base.connection_tree_view", "edi_base.connection_list_view")
