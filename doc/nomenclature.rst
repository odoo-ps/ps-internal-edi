============
Nomenclature
============

The terms used in the code, the UI and the documentation of the EDI framework.
Terms marked *(planned)* belong to parts of the 20.0 redesign that are not implemented yet.

Actors
======

Remote system
    The third-party system Odoo exchanges data with (an ERP, a WMS, a marketplace, a bank, ...).

Framework module
    A module of this repository (``edi_base``, ``edi_ftp_connection``, ...). It adds generic
    capabilities: a transport, a format, a trigger, ...

Client module
    A module written for one customer/project that uses the framework to implement its
    integrations. It should mostly contain data (connections, flows, mappings), and code only where
    configuration is not enough.

Connection
==========

Connection (``edi.connection``)
    *Where* the remote system is and *how we log in*. It knows nothing about what is exchanged.
    It combines a transport and an authentication.

Transport (``edi.connection.transport``)
    *How bytes are moved*: ``http``, ``ftp``, ``sftp``, ... Each transport accepts some URL schemes
    and some authentications (see ``edi.connection._get_transports``).

Authentication (``edi.connection.auth_type``)
    *How we prove who we are*: ``none``, ``basic`` (username/password), ``api_key``, ``oauth2``,
    ``ssh_key``. Independent from the transport: any authentication accepted by the transport
    can be used with it. All of them are defined in ``edi_base``.

URL (``edi.connection.url``)
    Base address of the remote system, its scheme must match the transport:
    ``https://api.example.com/v1``, ``ftp://files.example.com``, ``sftp://files.example.com:2222``.

Location
    Where data is read from or written to, *relative to the URL*: an endpoint path for HTTP
    (``orders``), a folder for FTP/SFTP (``/in``). Locations are defined by flows, not connections.

Session
    An open link with the remote system, returned by ``edi.connection._edi_connect``, valid for the
    duration of a ``with`` block (a ``requests.Session``, an ``ftplib.FTP``, ...). Never stored.

Message (``EdiMessage``)
    A unit of raw data received from or answered by the remote system: one file, one HTTP response.
    Its ``content`` is bytes; turning it into records is the job of the format and the mapping.

Acknowledge
    Telling the remote system that a received message has been handled, e.g. moving a file to a
    *done* or *error* folder. A no-op for transports that have nothing to acknowledge (HTTP).

Data exchange *(planned)*
=========================

Flow
    *What* is exchanged with a remote system, in one direction (in or out): the Odoo model, the
    connection, the location, the format, the field mapping, the trigger and the batch size.
    Replaces the 19.0 ``edi.integration``.

Format
    How records are serialized into a message and back: ``json``, ``xml``, ``csv``, ...

Field mapping
    The correspondence between an Odoo field and a key of the external data (JSON path, XPath,
    CSV column), configured in the UI, with an optional transform.

Link
    The match between an Odoo record and its identifier in the remote system (external id ↔ Odoo
    id), used to update instead of duplicate, and to resolve relations.

Trigger
    What starts a flow: *time based* (scheduled), *on change* (an Odoo record is created/updated)
    or *webhook* (the remote system calls Odoo).

Batch
    The records sent or received together: one CSV file, one API call with a list of records.

Queue
    The two-steps buffer between receiving/collecting data and processing/sending it, so that both
    steps can fail and be retried independently. Replaces the 19.0 ``edi.table.record``.

Run
    One execution of a flow step, with its outcome and errors. Replaces the 19.0
    ``edi.synchronization``.

Extension points
================

Hook
    A method designed to be overridden. Every hook is tagged in its docstring, so that it is clear
    *who* may override it (see the `developer guide <developer_guide.rst>`_).

Framework hook (``:edi-hook: framework``)
    Overridden by framework modules only, to plug in a new transport, format, ...

Client hook (``:edi-hook: client``)
    May be overridden by client modules, for what configuration cannot express. Kept to a minimum
    on purpose.
