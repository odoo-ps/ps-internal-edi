from odoo.upgrade import util


def migrate(cr, version):
    util.remove_record(cr, "edi_archiving.archive_outdated_synchronizations_cron")
