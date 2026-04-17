import logging

from . import models
from . import tools

from odoo.upgrade import util


_logger = logging.getLogger(__name__)


def pre_init(env):
    if not util.column_exists(env.cr, "edi_synchronization", "updated_edi_table_record_count"):
        _logger.info("Pre-create computed-stored updated_edi_table_record_count on edi_synchronization")
        util.create_column(env.cr, "edi_synchronization", "updated_edi_table_record_count", "integer", default=0)

    if not util.column_exists(env.cr, "edi_synchronization", "processed_edi_table_record_count"):
        _logger.info("Pre-create computed-stored processed_edi_table_record_count on edi_synchronization")
        util.create_column(env.cr, "edi_synchronization", "processed_edi_table_record_count", "integer", default=0)

    if not util.column_exists(env.cr, "edi_synchronization", "edi_table_operation"):
        _logger.info("Pre-create computed-stored edi_table_operation on edi_synchronization")
        util.create_column(env.cr, "edi_synchronization", "edi_table_operation", "varchar")
