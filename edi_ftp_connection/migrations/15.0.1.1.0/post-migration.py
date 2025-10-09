import logging

from odoo import SUPERUSER_ID, api


logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # reset synchronization_creation to 1 for all IN FTP integrations with ftp_in_done_let = True
    logger.info("Reset synchronization_creation to 1 for all IN FTP integrations with ftp_in_done_let = True")
    env["edi.integration"].with_context(active_test=False).search(
        [
            ("integration_flow", "in", env["edi.integration"]._get_in_flow_type()),
            ("connection_id.ftp_in_done_let", "=", True),
        ]
    ).write({"synchronization_creation": 1})
