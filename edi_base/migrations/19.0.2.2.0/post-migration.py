import logging

from odoo.upgrade import util


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Bring edi_archiving along on databases that already run edi_base.

    edi_archiving is auto_install from its 19.0.2.0.0, but that alone only covers *new* databases:
    Odoo evaluates auto_install candidates in ir.module.module.button_install(), and only installs
    one when a dependency is in state 'to install'. Upgrading edi_base leaves it 'to upgrade', so an
    existing database would keep a framework that writes a log row per exchange and never removes
    one. Forcing the install here is what actually closes that gap.

    Installing it is not the same as enabling it. Its pre_init_hook adds the two `active` columns
    without rewriting either table, and its post_init_hook refuses to configure any retention on a
    database that already holds synchronizations. Existing installations therefore get the module
    and the settings screen, nothing deleted, and nothing archived, until they decide otherwise.

    This script must stay numbered above 19.0.2.1.0, the version that indexes
    edi_synchronization_error.synchronization_id. Migration scripts only run for
    `installed_version < script_version <= manifest_version` (odoo/modules/migration.py), so a
    database that had already reached 2.1.0 would silently skip a script numbered below it -- and
    the delete stage needs that index to finish on a real backlog anyway.
    """
    _logger.info("Installing edi_archiving alongside edi_base (retention stays disabled)")
    util.force_install_module(cr, "edi_archiving")
