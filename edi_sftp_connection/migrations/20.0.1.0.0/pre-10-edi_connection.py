import json

from odoo.upgrade import util


def migrate(cr, version):
    """19.0 -> 20.0: the host key was an RSA public key (base64) in the JSON configuration.

    Connections without host key keep working without verification: the key is saved on their
    first connection (see ``edi.connection._sftp_check_host_key``).
    """
    util.create_column(cr, "edi_connection", "sftp_host_key", "varchar")
    cr.execute("SELECT id, configuration FROM edi_connection WHERE transport = 'sftp'")
    for connection_id, configuration in cr.fetchall():
        try:
            host_key = json.loads(configuration or "{}").get("host_key")
        except ValueError:
            continue
        if host_key and host_key.strip():
            cr.execute(
                "UPDATE edi_connection SET sftp_host_key = %s WHERE id = %s",
                [f"ssh-rsa {host_key.strip()}", connection_id],
            )
