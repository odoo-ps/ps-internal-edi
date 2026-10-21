from odoo.upgrade import util


def migrate(cr, version):
    """Drop the 19.0 connection fields once every module (transports, flows) migrated what it needed."""
    for field in ("configuration", "ftp_in_done_let", "ftp_load_content", "ftp_limit_in_files"):
        util.remove_field(cr, "edi.connection", field)
