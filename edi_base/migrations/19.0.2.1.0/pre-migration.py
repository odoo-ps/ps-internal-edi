import logging

from odoo.tools.sql import drop_index


_logger = logging.getLogger(__name__)

TABLE_NAME = "edi_synchronization_error"
INDEX_NAME = "edi_synchronization_error__synchronization_id_index"


def migrate(cr, version):
    """Drop a leftover invalid index before the ORM decides whether to create it.

    edi.synchronization.error.synchronization_id becomes index=True in this version. On a large
    table the recommended way to get that index without an upgrade-long ACCESS EXCLUSIVE lock is to
    build it by hand with CREATE INDEX CONCURRENTLY *before* deploying (see the README). A
    CONCURRENTLY build that fails leaves an INVALID index behind, and odoo.tools.sql.index_exists()
    matches on the name only -- the ORM would find that name, skip creation, and the planner would
    ignore the index forever. Dropping it here lets the ORM rebuild it.

    The drop is catalog work plus unlinking whatever relation files the failed build left on disk,
    and it briefly takes an ACCESS EXCLUSIVE lock on the parent table. post-migration.py then
    asserts the ORM really did create the index.
    """
    cr.execute(
        """
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = %s
           AND NOT i.indisvalid
           AND n.nspname = ANY (current_schemas(FALSE))
        """,
        [INDEX_NAME],
    )
    if cr.rowcount:
        _logger.warning("Dropping invalid index %s, it will be rebuilt by the ORM", INDEX_NAME)
        drop_index(cr, INDEX_NAME, TABLE_NAME)
