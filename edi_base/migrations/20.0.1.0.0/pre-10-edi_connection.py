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
    for column in ("file_on_conflict", "file_processed_action", "file_success_folder", "file_failed_folder"):
        util.create_column(cr, "edi_connection", column, "varchar")
    cr.execute(
        """
        UPDATE edi_connection
           SET file_on_conflict = 'fail', file_processed_action = 'move',
               file_success_folder = 'done', file_failed_folder = 'error'
        """
    )
    # "Let in in_folder" (ftp_in_done_let) only exists if edi_ftp_connection was installed
    if util.column_exists(cr, "edi_connection", "ftp_in_done_let"):
        query = "SELECT id, transport, configuration, ftp_in_done_let FROM edi_connection"
    else:
        query = "SELECT id, transport, configuration, FALSE FROM edi_connection"
    cr.execute(query + " WHERE transport IN ('ftp', 'sftp')")
    for connection_id, transport, configuration, leave_processed_files in cr.fetchall():
        try:
            config = json.loads(configuration or "{}")
        except ValueError:
            _logger.warning("edi.connection %s: invalid JSON configuration, credentials not migrated", connection_id)
            continue
        host = config.get("host") or "localhost"
        port = config.get("port")
        folders = {
            key: value.strip()
            for key in ("in_folder", "out_folder", "in_folder_done", "in_folder_error")
            if (value := config.get(key)) and value.strip() and "<" not in value  # "<PATH HERE>" template
        }
        # 19.0 folders not starting with / were relative to the login directory: so is a URL without
        # path. The folders of the flows and the success/failed folders are then relative to the URL.
        relative = any(not folder.startswith("/") for folder in folders.values())
        if relative and any(folder.startswith("/") for folder in folders.values()):
            _logger.warning(
                "edi.connection %s: absolute and relative folders, check them (now relative to the login "
                "directory): %s",
                connection_id,
                folders,
            )
        url = f"{transport}://{host}:{port}" if port else f"{transport}://{host}"
        url += "" if relative else "/"
        ssh_key = transport == "sftp" and config.get("key")
        # 19.0 "raise" (the default), "rename" (with a configurable extension, now always .old), "replace"
        on_conflict = {"rename": "rename", "replace": "replace"}.get(config.get("on_conflict"), "fail")
        # 19.0, once processed: left in place if ftp_in_done_let, else moved to in_folder_done if set,
        # else deleted; once failed: moved to in_folder_error if set, else left in place
        # starting with /: relative to the folder of the URL, not to the folder of the file
        done_folder, error_folder = (
            "/" + folders[key].lstrip("/") if key in folders else None for key in ("in_folder_done", "in_folder_error")
        )
        if leave_processed_files:
            processed_action = "keep"
        else:
            processed_action = "move" if done_folder else "delete"
        cr.execute(
            """
            UPDATE edi_connection
               SET url = %s, username = %s, password = %s, auth_type = %s, ssh_private_key = %s,
                   file_on_conflict = %s, file_processed_action = %s, file_success_folder = %s,
                   file_failed_folder = %s
             WHERE id = %s
            """,
            [
                url,
                config.get("user"),
                config.get("password"),
                "ssh_key" if ssh_key else "basic",
                ssh_key or None,
                on_conflict,
                processed_action,
                done_folder or "done",
                error_folder,
                connection_id,
            ],
        )

    # the URL is stored without surrounding spaces, and used as is
    cr.execute("UPDATE edi_connection SET url = TRIM(url) WHERE url != TRIM(url)")

    # views of the transport modules extend fields that do not exist anymore
    util.remove_view(cr, "edi_sftp_connection.connection_form_view_sftp")
    util.remove_view(cr, "edi_ftp_connection.connection_form_view_ftp")
    util.rename_xmlid(cr, "edi_base.connection_tree_view", "edi_base.connection_list_view")
