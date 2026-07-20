=============
EDI Archiving
=============

------------
Particularity
------------

Adds an ``active`` field on ``edi.synchronization`` (and keeps its errors visible even once
archived) plus an autovacuum job that archives, and optionally deletes, old synchronizations.
Nothing to configure in XML/Python: everything is driven by the settings below.

When a synchronization is archived, ``received_content`` and ``sent_content`` are emptied at
the same time, to keep the database from growing indefinitely — once a synchronization is
archived, its content is considered no longer needed for debugging.

-------------
Configuration
-------------

Settings ▸ Technical ▸ EDI, block "Synchronizations Archiving and Deleting":

-   **Archive after** / **Delete after**: number of days since ``create_date`` before a
    synchronization is archived / deleted (``0`` = never)
-   **States**: which synchronization states (``fail``, ``done``, ``new``, ``cancel``) are
    eligible — a duration without at least one state checked is rejected

Both durations share the same set of states.

------------
How it works
------------

An ``@api.autovacuum`` job (``_process_outdated_synchronizations``) runs deletion first, then
archiving, each time processing at most **10 000** records. If more remain after that
(e.g. first run on an existing backlog), the job re-triggers itself one minute later instead of
processing everything in one go.
