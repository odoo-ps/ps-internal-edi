# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID

import logging

logger = logging.getLogger(__name__)

def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # fill-in default value for last execution_date
    logger.info('Set-Up default value for new field edi.integration.last_execution_date')
    integrations = env['edi.integration'].with_context(active_test=False).search([])
    for integration in integrations:
        dates = [integration.last_success_date, integration.last_failure_date]
        valid_dates = [d for d in dates if d]
        integration.last_execution_date = max(valid_dates) if valid_dates else False

    # fill-in default value for edi.integration.write_content_on_sync
    logger.info('Set-Up default value for new field edi.integration.write_content_on_sync')
    integrations.write_content_on_sync = True
