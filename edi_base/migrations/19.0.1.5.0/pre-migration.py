from odoo.upgrade.util import column_exists, create_column


def migrate(cr, version):
    if not column_exists(cr, "edi_synchronization", "triggered_date"):
        create_column(cr, "edi_synchronization", "triggered_date", "character varying")
        cr.execute("UPDATE edi_synchronization SET triggered_date = synchronization_date" "")
