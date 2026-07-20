========================
EDI 2-steps Archiving
========================

------------
Particularity
------------

Same mechanism as ``edi_archiving`` (an ``active`` field + an autovacuum job archiving, then
deleting, old records by age and state), applied this time to ``edi.table.record`` — the
step-1/step-2 queue table of ``edi_2steps`` — instead of ``edi.synchronization``.

Two differences from ``edi_archiving`` follow from that:

-   **Age reference**: a queue record is only a candidate once it can no longer be reprocessed
    (``can_be_processed = False``). Its age is measured from ``last_process_date`` (last time a
    synchronization touched it), not from ``create_date`` — a record can sit untouched for a
    while before step 2 ever runs on it.
-   **Per-integration opt-out**: ``edi_table_auto_archive`` on ``edi.integration`` (default
    ``True``) lets a specific integration be excluded from the archiving cron entirely.
-   **Content clearing** reuses ``edi_2steps``'s own ``edi_table_record_clear_content`` field —
    this module only adds the ``on_archiving`` / ``on_success_or_archiving`` /
    ``on_error_or_archiving`` choices to it, so clearing ``content`` on archive is configured
    once, in the same place as clearing on success/error.

-------------
Configuration
-------------

Settings ▸ Technical ▸ EDI, block "EDI 2-steps Archiving and Deleting":

-   **Archive after** / **Delete after**: days since a record stopped being processable
    (``last_process_date``) before it's archived / deleted (``0`` = never)
-   **States**: ``fail`` (as "Error"), ``done`` (as "Success"), ``new``, ``cancelled`` — a
    duration without at least one state checked is rejected

Per integration, on the integration form: ``EDI 2-steps Auto Archive`` to opt out, and
``edi_table_record_clear_content`` to decide when ``content`` gets emptied.

.. note::
    ``edi.table.record`` also has a ``warning`` state (step-2 failed with a recoverable error,
    e.g. a concurrent DB update — the record stays reprocessable). It is a valid value for
    ``_archive_states()`` and the underlying ``ir.config_parameter``
    (``edi.table.record.archive.state.warning``), but there is currently no checkbox for it in
    the Settings screen above — records in that state can't be auto-archived/deleted from the
    UI as it stands today.

------------
How it works
------------

An ``@api.autovacuum`` job (``_process_outdated_table_records``) runs deletion first, then
archiving, each time processing at most **10 000** records. If more remain after that
(e.g. first run on an existing backlog), the job re-triggers itself one minute later instead of
processing everything in one go — same batching logic as ``edi_archiving``.

This module also exposes ``edi.synchronization``'s own ``active`` field (added by
``edi_archiving``) in the search views, with "Active" / "Inactive" / "Active & Inactive"
filters — no separate duration setting is added for synchronizations here, that's already
covered by ``edi_archiving`` itself.
