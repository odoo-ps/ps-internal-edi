import logging

from odoo.tools import create_column

logger = logging.getLogger(__name__)


def migrate(cr, version):
    # migrate field synchronization_creation (SQL)
    # selection field -> integer field
    # multi -> 0
    # one -> 1
    # force synchronization_creation = 1 for all in flow
    logger.info("Migrate field edi.integration.synchronization_creation from selection to integer")

    cr.execute("ALTER TABLE edi_integration ADD COLUMN synchronization_creation_temp varchar;")
    cr.execute("UPDATE edi_integration SET synchronization_creation_temp = synchronization_creation;")
    cr.execute("UPDATE edi_integration SET synchronization_creation = NULL;")
    cr.execute(
        "ALTER TABLE edi_integration ALTER COLUMN synchronization_creation TYPE integer USING (synchronization_creation::integer);"
    )
    cr.execute(
        """
        UPDATE edi_integration SET synchronization_creation = CASE
        WHEN synchronization_creation_temp = 'one' THEN 1
        ELSE 0 END;"""
    )
    cr.execute("UPDATE edi_integration SET synchronization_creation = 1 WHERE integration_flow = 'in';")
    cr.execute("ALTER TABLE edi_integration DROP COLUMN synchronization_creation_temp;")
    cr.execute("DELETE FROM ir_model_fields WHERE name='synchronization_creation' AND model='edi.integration';")

    # delete field in_process_type if it exists
    logger.info("Delete field edi.integration.in_process_type")

    cr.execute("""DO $$ BEGIN IF (EXISTS (
            SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'edi_integration' AND column_name='in_process_type'
            ))
            THEN ALTER TABLE edi_integration DROP COLUMN in_process_type;
        END IF; END; $$""")
    cr.execute("DELETE FROM ir_model_fields WHERE name='in_process_type' AND model='edi.integration';")

    # fill-in default value for last execution_date
    logger.info("Set-Up default value for new field edi.integration.last_execution_date")

    create_column(cr, "edi_integration", "last_execution_date", "timestamp")
    cr.execute("UPDATE edi_integration SET last_execution_date = GREATEST(last_success_date, last_failure_date);")

    # fill-in default value for write_content_on_sync
    logger.info("Set-Up default value for new field edi.integration.write_content_on_sync")

    create_column(cr, "edi_integration", "write_content_on_sync", "boolean")
    cr.execute("UPDATE edi_integration SET write_content_on_sync = True;")
