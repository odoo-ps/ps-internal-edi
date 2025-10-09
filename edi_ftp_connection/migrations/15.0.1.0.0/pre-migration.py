import logging

from odoo import tools


logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Rename field in_done_let to ftp_in_done_let"""
    if tools.column_exists(cr, "edi_connection", "in_done_let"):
        tools.rename_column(cr, "edi_connection", "in_done_let", "ftp_in_done_let")
        cr.execute("DELETE FROM ir_model_fields WHERE name='in_done_let' AND model='edi.connection'")
