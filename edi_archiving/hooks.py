import logging

from odoo.tools.sql import column_exists


_logger = logging.getLogger(__name__)

# Retention applied to a brand new installation. Deliberately not shipped as data records: a
# noupdate="1" record whose xml_id does not exist yet is still created when the module is
# upgraded (see odoo/orm/models.py, _load_records), which would switch retention on at every
# existing installation of this shared module. A post_init_hook only ever runs on install.
#
# The values are strings on purpose: ir.config_parameter stores strings, and these are exactly what
# the settings screen writes. "Off" is the *absence* of the key, never the string "False" -- that is
# what a Boolean field with a config_parameter does when unticked, and it has to stay that way,
# since get_param would otherwise hand back a non-empty string and every read here is a truth test.
NEW_INSTALL_PARAMETERS = {
    "edi.archive.duration": "90",
    "edi.archive.duration.delete": "365",
    "edi.archive.state.done": "True",
    "edi.archive.state.cancelled": "True",
    "edi.archive.clear_error_description": "True",
}


def pre_init_hook(env):
    """Add the two `active` columns ourselves, so that installing stays cheap on a large database.

    Both are new stored columns on the two biggest tables this framework produces. Left to the ORM:

    - `edi_synchronization.active` gets a plain ADD COLUMN followed by _init_column(), which is
      "UPDATE edi_synchronization SET active = true WHERE active IS NULL" over the whole table;
    - `edi_synchronization_error.active` is a stored related over a stored many2one, so it takes the
      shortcut at odoo/orm/fields.py (update_db_related): a single "UPDATE ... FROM" over the whole
      table. Fast in ORM terms, but still a rewrite of every row.

    Both rewrites are gated on the column not already existing, so creating it here skips them. We
    add it with DEFAULT TRUE instead, which PostgreSQL stores in the catalog and materialises
    lazily (no rewrite at all), then drop the default so the schema matches what the ORM expects --
    dropping it afterwards keeps the value already recorded for the existing rows. Every
    synchronization is by definition unarchived at this point, so TRUE is the correct value for all
    of them, and an error's `active` follows its synchronization's.

    Measured on a 3 M row / 4.7 GB table: 1.0 s and no size change, against a full-table UPDATE
    that rewrites every row and doubles the table until it is vacuumed.
    """
    for table in ("edi_synchronization", "edi_synchronization_error"):
        if column_exists(env.cr, table, "active"):
            continue
        _logger.info("Adding %s.active without rewriting the table", table)
        env.cr.execute('ALTER TABLE "%s" ADD COLUMN "active" boolean DEFAULT TRUE' % table)
        env.cr.execute('ALTER TABLE "%s" ALTER COLUMN "active" DROP DEFAULT' % table)


def post_init_hook(env):
    """Configure a retention policy, but only for an installation that has no history to lose.

    This module is auto-installed alongside edi_base, so it also lands on databases that have been
    running EDI for years. Switching deletion on there would destroy an audit trail nobody agreed
    to give up, so the defaults are only applied when the database holds no synchronization at all.
    An existing installation gets the module inert, exactly as before, and opts in from
    Settings > Technical > EDI.
    """
    if env["edi.synchronization"].with_context(active_test=False).search([], limit=1):
        _logger.info(
            "Existing synchronizations found: leaving EDI retention disabled. "
            "Configure it in Settings > Technical > EDI."
        )
        return

    config = env["ir.config_parameter"].sudo()
    for key, value in NEW_INSTALL_PARAMETERS.items():
        config.set_param(key, value)
    _logger.info("New installation: EDI synchronizations archived after 90 days, deleted after 365")
