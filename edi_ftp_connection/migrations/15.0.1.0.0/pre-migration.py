import logging

logger = logging.getLogger(__name__)


def migrate(cr, version):
    # rename field in_done_let to ftp_in_done_let
    logger.info("Rename field edi.connection.in_done_let into edi.connection.ftp_in_done_let")

    cr.execute("ALTER TABLE edi_connection ADD COLUMN ftp_in_done_let boolean;")
    cr.execute("UPDATE edi_connection SET ftp_in_done_let = in_done_let;")
    cr.execute("ALTER TABLE edi_connection DROP COLUMN in_done_let;")
    cr.execute("DELETE FROM ir_model_fields WHERE name='in_done_let' AND model='edi.connection';")
