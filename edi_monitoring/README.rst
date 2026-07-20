==============
EDI Monitoring
==============

------------
Particularity
------------

Adds observability on top of ``edi_base``, for developers and functional users alike:

-   ``file_size`` on ``edi.synchronization``: computed from the ``file`` key of IN data, when
    the connection provides a local file path (e.g. FTP/SFTP)
-   ``execution_time`` on ``edi.synchronization``: minutes between the synchronization start
    and end; ``reasonable_execution_time`` on ``edi.integration`` flags synchronizations
    running longer than expected (default 10 min — 2/3 of odoo.sh's real-time limit)
-   ``logging_verbosity`` on ``edi.integration`` + a ``log(msg, level=..., verbosity=...)``
    helper: only messages at or below the configured verbosity are actually logged
-   chatter + tracked fields on ``edi.integration``, to see who changed the configuration and
    when
-   ``edi.monitoring``: a scheduled report that summarizes synchronizations by integration and
    state, and can email itself out

-------------
Configuration
-------------

An ``edi.monitoring`` record (menu **EDI ▸ Monitoring ▸ Configurations**) defines what to
report on:

-   ``filter_id``: an ``ir.filters`` domain on ``edi.synchronization`` — the pre-installed
    "Integrations Monitoring" record covers ``new``/``fail`` synchronizations not yet reported
-   ``email``: recipient for the automatic report; leave empty to only generate reports
    manually, without sending them

The button **"Create and send report by email"** creates a report on demand;
``report_cron`` (inactive by default, daily at 7 AM) calls ``action_create_report()`` on all
``edi.monitoring`` records.

------------
How it works
------------

Creating an ``edi.monitoring.report``:

1.  links every synchronization matching the monitoring's domain (``synchronization_ids``),
    but only generates report lines for the first **1 000** of them
    (``limit_reached`` flags when the cap was hit)
2.  adds one warning line per monitored integration ``type`` that is currently archived —
    a likely sign it was deactivated by odoo.sh after hitting the execution timeout
3.  if ``email`` is set, sends the report grouped by integration then by state, with a count
    per state
