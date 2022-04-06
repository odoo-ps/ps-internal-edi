# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID

import logging

logger = logging.getLogger(__name__)

def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # migrate field synchronization_creation (SQL)
    # selection field -> integer field
    # multi -> 0
    # one -> 1
    # force synchronization_creation = 1 for all in flow
    logger.info('Migrate field edi.integration.synchronization_creation from selection to integer')
    env.cr.execute("""
        ALTER TABLE edi_integration ADD COLUMN synchronization_creation_temp varchar;
        UPDATE edi_integration SET synchronization_creation_temp = synchronization_creation;
        UPDATE edi_integration SET synchronization_creation = NULL;
        ALTER TABLE edi_integration ALTER COLUMN synchronization_creation TYPE integer USING (synchronization_creation::integer);
        UPDATE edi_integration SET synchronization_creation = CASE
                WHEN synchronization_creation_temp = 'one' THEN 1
                ELSE 0
            END;
        UPDATE edi_integration SET synchronization_creation = 1 WHERE integration_flow = 'in';
        ALTER TABLE edi_integration DROP COLUMN synchronization_creation_temp;
        DELETE FROM ir_model_fields WHERE name='synchronization_creation' AND model='edi.integration';
    """)

    # delete field in_process_type
    logger.info('Delete field edi.integration.in_process_type')
    env.cr.execute("""
        ALTER TABLE edi_integration DROP COLUMN in_process_type;
        DELETE FROM ir_model_fields WHERE name='in_process_type' AND model='edi.integration';
    """)
