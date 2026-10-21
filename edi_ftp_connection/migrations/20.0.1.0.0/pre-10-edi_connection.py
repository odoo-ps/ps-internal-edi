import json

from odoo.upgrade import util


def migrate(cr, version):
    """19.0 -> 20.0: active mode was set in the JSON configuration ("is_active": "True")."""
    util.create_column(cr, "edi_connection", "ftp_passive", "boolean", default=True)
    cr.execute("SELECT id, configuration FROM edi_connection WHERE transport = 'ftp'")
    active_ids = []
    for connection_id, configuration in cr.fetchall():
        try:
            config = json.loads(configuration or "{}")
        except ValueError:
            continue
        if str(config.get("is_active")) == "True":
            active_ids.append(connection_id)
    if active_ids:
        cr.execute("UPDATE edi_connection SET ftp_passive = FALSE WHERE id IN %s", [tuple(active_ids)])
