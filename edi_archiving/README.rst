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
archived, its content is considered no longer needed for debugging. Optionally, its errors are
deleted too — see "Delete errors" below.

This module is ``auto_install``: it is installed as soon as ``edi_base`` is, because a framework
that writes a log row per exchange needs a retention policy from the start rather than as an
afterthought.

-------------
Configuration
-------------

Settings ▸ Technical ▸ EDI, block "Synchronizations Archiving and Deleting":

-   **Archive after** / **Delete after**: number of days since ``create_date`` before a
    synchronization is archived / deleted (``0`` = never)
-   **States**: which synchronization states (``fail``, ``done``, ``new``, ``cancelled``) are
    eligible — a duration without at least one state checked is rejected
-   **Delete errors when archiving**: also delete the errors attached to an archived
    synchronization. This is usually where the volume is: a description holds a full traceback,
    and errors outnumber synchronizations. Once the content is released an error has nothing left
    to diagnose, so it goes with it; the synchronization keeps its state and dates. Its Failures
    tab is then empty, as are the last issue shown by ``edi_monitoring`` and the Failures page of
    an ``edi_2steps`` table record whose last synchronization is archived. Unarchiving does not
    bring them back.

Both durations share the same set of states.

Defaults
========

A **new** installation is configured to archive after **90 days** and delete after **365 days**,
for the ``done`` and ``cancelled`` states, with errors deleted on archive.

``fail`` and ``new`` are deliberately left out. A failure is often still needed weeks after the
fact, and a synchronization stuck in ``new`` is a symptom — deleting those automatically would
hide a problem rather than solve one. Enable them, with a duration that suits, if you want them.

These defaults are applied by the ``post_init_hook``, and **only to a database that holds no
synchronization yet**. An installation that already has EDI history — including one where this
module arrives through ``auto_install`` on an existing database — gets every duration at ``0``,
which means the job does nothing at all, exactly as before. Retention there is an explicit
decision, taken from the settings screen.

The defaults are not shipped as data records on purpose: a record carrying ``noupdate="1"`` whose
external id does not exist yet is still *created* when the module is upgraded, so shipping them
that way would switch retention on at every existing installation of this shared module.

Upgrading from 1.x
==================

Before 2.0 the **Cancelled** box was stored as ``edi.archive.state.cancel`` and selected nothing,
since the state is ``cancelled``. The 2.0 pre-migration renames the parameter, so a box that was
ticked stays ticked — and from then on it acts: cancelled synchronizations past the configured
duration are archived, and deleted if a delete duration is set.

A database that had uninstalled this module and gets it back through ``auto_install`` runs no
migration: an ``edi.archive.state.cancel`` left behind there is ignored, and the box has to be
ticked again.

------------
How it works
------------

An ``@api.autovacuum`` job (``_process_outdated_synchronizations``) runs deletion first, then
archiving, each time processing at most **10 000** records. If more remain after that
(e.g. first run on an existing backlog), the job re-triggers itself one minute later instead of
processing everything in one go.

Deletion relies on the ``ON DELETE CASCADE`` from ``edi.synchronization.error``, which needs
``edi_synchronization_error.synchronization_id`` to be indexed — it is, from ``edi_base``
17.0.1.2.2 on. Without that index PostgreSQL scans the whole error table once per deleted
synchronization, and the delete stage never finishes on a real backlog.

Installing on a large existing database
=======================================

The ``pre_init_hook`` creates the two ``active`` columns itself, with a PostgreSQL column default
so that no row is rewritten, and drops the default afterwards. Left to the ORM, those two columns
would each cost a full-table ``UPDATE`` — on the installation that prompted this change, roughly
78 GB of row rewrites on a database that had just run out of storage. With the hook, installing is
a catalog change: about a second on a 3 M row table, and no growth.
