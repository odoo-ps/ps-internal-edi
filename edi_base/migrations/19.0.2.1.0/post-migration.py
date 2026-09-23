import logging


_logger = logging.getLogger(__name__)

TABLE_NAME = "edi_synchronization_error"
INDEX_NAME = "edi_synchronization_error__synchronization_id_index"


def migrate(cr, version):
    """Fail the upgrade if the index the ORM was asked to build is not there and valid.

    Registry.check_indexes() wraps create_index() in a savepoint and swallows psycopg2
    OperationalError into a _schema.error() log line. Building a multi-GB index raises exactly that:
    53100 (disk full) or 57014 (statement/lock timeout). Left alone, the upgrade would then commit,
    edi_base would be recorded at this version, this migration would never run again, and the
    cascade would still be unindexed -- the failure mode this whole version exists to fix, arriving
    silently. Raising here rolls the upgrade back instead, so the operator sees it.

    indisvalid is checked as well as existence: an index pre-created with CREATE INDEX CONCURRENTLY
    that failed halfway is present in pg_indexes, so the ORM skipped creation, but the planner will
    never use it.
    """
    cr.execute(
        """
        SELECT i.indisvalid
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = %s
           AND n.nspname = ANY (current_schemas(FALSE))
        """,
        [INDEX_NAME],
    )
    row = cr.fetchone()
    if row is None:
        # index_exists() reads pg_indexes without filtering the schema, so an index of this name
        # anywhere in the database -- including a schema that is not even on the search_path --
        # makes the ORM skip creation. Name it rather than send the reader to the disk-space hunt.
        cr.execute(
            """
            SELECT n.nspname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relname = %s AND c.relkind = 'i'
            """,
            [INDEX_NAME],
        )
        elsewhere = [schema for (schema,) in cr.fetchall()]
        if elsewhere:
            raise RuntimeError(
                f"{INDEX_NAME} is missing on {TABLE_NAME}, but an index of that name exists in "
                f"schema(s) {', '.join(elsewhere)}. The ORM matches the index name without "
                f"filtering the schema, so it skipped creation. Rename or drop that index, then "
                f"re-run the upgrade."
            )
        raise RuntimeError(
            f"{INDEX_NAME} was not created on {TABLE_NAME}. The ORM logs the reason at ERROR level "
            f"under odoo.schema -- a full disk or a statement_timeout while building it are the "
            f"likely causes. Build it manually with CREATE INDEX CONCURRENTLY (see the edi_base "
            f"README) and re-run the upgrade."
        )
    if not row[0]:
        raise RuntimeError(
            f"{INDEX_NAME} exists on {TABLE_NAME} but is INVALID, so the planner ignores it. Drop "
            f"it and rebuild it with CREATE INDEX CONCURRENTLY (see the edi_base README)."
        )
    _logger.info("%s is present and valid on %s", INDEX_NAME, TABLE_NAME)
