# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID

import logging

logger = logging.getLogger(__name__)

def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # rename field in_done_let to ftp_in_done_let
    logger.info('Rename field edi.connection.in_done_let into edi.connection.ftp_in_done_let')
    env.cr.execute("""
        ALTER TABLE edi_connection ADD COLUMN ftp_in_done_let boolean;
        UPDATE edi_connection SET ftp_in_done_let = in_done_let;
        ALTER TABLE edi_connection DROP COLUMN in_done_let;
        DELETE FROM ir_model_fields WHERE name='in_done_let' AND model='edi.connection';
    """)
