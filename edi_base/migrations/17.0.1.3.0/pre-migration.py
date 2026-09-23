import logging


_logger = logging.getLogger(__name__)

INDEX_NAME = "edi_synchronization_error__synchronization_id_index"


def migrate(cr, version):
    """Drop a leftover invalid index before the ORM decides whether to create it.

    edi.synchronization.error.synchronization_id becomes index=True in this version. On a large
    table the recommended way to get that index without an upgrade-long ACCESS EXCLUSIVE lock is to
    build it by hand with CREATE INDEX CONCURRENTLY *before* deploying (see the README). A
    CONCURRENTLY build that fails leaves an INVALID index behind, and odoo.tools.sql.index_exists()
    matches on the name only -- the ORM would find that name, skip creation, and the planner would
    ignore the index forever. Dropping it here is cheap (catalog-only) and lets the ORM rebuild it.
    """
    cr.execute(
        """
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE c.relname = %s AND NOT i.indisvalid
        """,
        [INDEX_NAME],
    )
    if cr.rowcount:
        _logger.warning("Dropping invalid index %s, it will be rebuilt by the ORM", INDEX_NAME)
        cr.execute('DROP INDEX "%s"' % INDEX_NAME)
