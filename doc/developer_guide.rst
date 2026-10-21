===============
Developer guide
===============

How the EDI framework is built and how to extend it.
The terms used here are defined in the `nomenclature <nomenclature.rst>`_.

Principles
==========

-   **Configuration first.** An integration is data (connection, flow, mapping), editable in the
    UI. Code is the exception, for what configuration cannot express.
-   **Few hooks, explicitly tagged.** Only methods tagged as hooks are meant to be overridden;
    everything else is internal and may change without notice.
-   **Odoo native.** Extension goes through ``_inherit``, selection fields with ``selection_add``
    and ``super()`` calls. No registries, decorators or custom dispatch mechanisms.
-   **Independent dimensions.** Transport, authentication, format and trigger are chosen
    independently; adding one does not require touching the others.

Hooks
=====

Every method designed to be overridden says so in its docstring, with one of two tags:

``:edi-hook: framework``
    Extended by **framework modules** of this repository, to add a capability for everyone
    (a transport, a format, ...). Client modules must not override them:
    if a client needs one, the capability belongs in a framework module.

``:edi-hook: client``
    May be overridden by **client modules**, to implement what an integration cannot express by
    configuration. They are kept to a minimum; each one documents what it receives and returns.

A method without tag is not an extension point. To list the hooks with their method::

    grep -rnE "^    def |^        :edi-hook:" --include=*.py . | grep -B1 ":edi-hook:" | grep -v -- "--"

Tag a hook by adding the field to its docstring, before ``:param``/``:return``:

.. code-block:: python

    def _edi_connect(self):
        """Open a session with the remote system.

        :edi-hook: framework
        :return: a transport-specific session, to use as a context manager
        """

Override pattern
----------------

A hook implemented per transport (or per format, ...) handles its own value and delegates the
others to ``super()``:

.. code-block:: python

    def _edi_connect(self):
        if self.transport != "ftp":
            return super()._edi_connect()
        ...

Connection and transports
=========================

``edi.connection`` holds the URL and the credentials of a remote system, and exposes the same
transport API whatever the transport, inspired by ``ir.mail_server.connect`` / ``send_email``:

.. code-block:: python

    with connection._edi_connect() as session:
        for message in connection._edi_receive(session, "/in"):
            ...
            connection._edi_acknowledge(session, message, success=True)
        connection._edi_send(session, "/out", content, name="orders.csv")

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Method (``:edi-hook: framework``)
     - Purpose
   * - ``_get_transports()``
     - Declare each transport: accepted URL ``schemes`` and ``auth_types``
   * - ``_edi_connect()``
     - Open a session (usable as a context manager), authenticated with ``auth_type``
   * - ``_edi_receive(session, location, ...)``
     - Return the available data as a list of ``EdiMessage``
   * - ``_edi_send(session, location, content, ...)``
     - Send bytes, return the answer as an ``EdiMessage`` (or ``None``)
   * - ``_edi_acknowledge(session, message, success, ...)``
     - Mark a received message as handled (move or delete the file). Default: nothing
   * - ``_edi_test(session)``
     - Extra check for the *Test Connection* button, beyond opening the session

Rules for every transport:

-   Work with **bytes** only: decoding and parsing belong to the format layer.
-   Raise ``EdiTransportError`` (with the remote answer in ``content`` when there is one) for any
    network, authentication or protocol error, so that callers handle one exception type.
-   Read credentials with ``sudo()``: they are restricted to administrators, while flows may run
    as another user.
-   Never keep a session on a record or on the cursor: it only lives in the ``with`` block. What
    must survive a run (e.g. an OAuth2 token) is stored in fields of ``edi.connection``.

Authentications
---------------

All the authentications and their credential fields are defined in ``edi_base``, whatever the
transport using them. Transport modules only declare which ones they accept (``_get_transports``)
and use them in ``_edi_connect``.

.. list-table::
   :widths: 22 78
   :header-rows: 1

   * - ``auth_type``
     - Usage
   * - ``none``
     - Public endpoint, anonymous FTP
   * - ``basic``
     - Username / password (HTTP Basic, FTP and SFTP login)
   * - ``api_key``
     - Key sent in a header or a query parameter, with an optional prefix (``Bearer``, ``Token``)
   * - ``oauth2``
     - Grant types ``client_credentials``, ``password`` and ``authorization_code``. The latter
       needs a user to click *Connect* once: the remote system redirects to
       ``/edi/oauth2/callback`` (the *Redirect URI* to register on the remote application), the
       refresh token is then used and rotated automatically. PKCE is used unless disabled.
   * - ``ssh_key``
     - Username + private key (SFTP)

Two options are combinable with any authentication:

-   **Client certificate** (mutual TLS, ``tls_client_auth``), for the transports declaring
    ``"client_certificate": True``: a PEM file (certificate, optionally with its key) or a PKCS#12
    file, with an optional password. It is checked on save and its expiry date is shown. Transports
    get it as temporary files with ``_tls_client_certificate_files()``, removed after use.
-   **Extra headers** (HTTP, ``http_use_headers``): one ``Name: value`` per line, sent with every
    request, e.g. an Azure API Management subscription key or an API version.

The *Credentials* tab of the form shows the fields listed by ``_get_auth_fields()`` for the
selected ``auth_type``, through the computed ``auth_field_names``
(``invisible="'username' not in auth_field_names"``).

Adding an authentication
------------------------

An authentication specific to one remote system (request signing, login call returning a session
cookie, provider specific token, ...) is added by the client module, like any selection value:

.. code-block:: python

    class EdiConnection(models.Model):
        _inherit = "edi.connection"

        auth_type = fields.Selection(selection_add=[("acme_hmac", "ACME Signature")],
                                     ondelete={"acme_hmac": "set default"})
        acme_key = fields.Char(copy=False)

        def _get_transports(self):
            transports = super()._get_transports()
            transports["http"]["auth_types"] += ("acme_hmac",)
            return transports

        def _get_auth_fields(self):
            return {**super()._get_auth_fields(), "acme_hmac": ("acme_key",)}

        def _http_authenticate(self, session):
            if self.auth_type != "acme_hmac":
                return super()._http_authenticate(session)
            session.auth = AcmeHmacAuth(self.sudo().acme_key)  # a requests.auth.AuthBase

and its fields are added to the ``credentials`` group of the *Credentials* tab, with
``invisible="'acme_key' not in auth_field_names"``. ``_http_authenticate`` and
``_get_auth_fields`` are ``:edi-hook: client``. An authentication that no code handles is refused
when the session is opened.

File based transports
---------------------

FTP and SFTP share the same logic, implemented once in ``edi_base`` for every transport declared
``"file_based": True`` in ``_get_transports()``:

-   ``_edi_receive(session, folder, pattern="*.csv", limit=100, modified_after=None)`` lists the
    files of the folder (not the sub-folders), filters them, sorts them, keeps the first ``limit``
    ones and downloads them
-   ``_edi_send(session, folder, content, name="orders.csv")`` uploads under a temporary
    ``<name>.<random>.filepart`` name then renames it, so that the remote system never picks up a
    partial file; the temporary file is deleted (best effort) if the upload fails
-   ``_edi_acknowledge(session, message, success)`` handles the received file according to the
    connection:

    -   processed successfully (``file_processed_action``): moved to ``file_success_folder``
        (default), deleted, or left in place
    -   failed: moved to ``file_failed_folder`` when the processed files are moved and it is set,
        otherwise left in place, to be received again (retried) on the next run

    The folders are relative to the folder of the file (``done``: ``in/done``), or to the folder
    of the URL when they start with ``/``
-   when the target file already exists, ``file_on_conflict`` decides: fail, rename the existing
    file (``<name>-<UTC date>-utc.old``) or replace it (``_file_replace``: atomic when the
    transport supports it, otherwise the existing file is renamed aside and only deleted once
    the new one took its place)

**Order.** When the processed files leave the folder (moved or deleted), the folder only holds
pending files: the most recent ones are received first. When they are left in place (e.g. the
remote system cleans the folder itself), the oldest ones are received first, and the caller passes
the date of the last processed file as ``modified_after`` so that each run continues after it,
instead of receiving the same oldest files again. Without date from the transport, the files are
sorted by name.

**Folders.** All the folders are relative to the folder of the connection URL: with
``sftp://host/acme``, ``in`` is ``/acme/in``; with ``sftp://host/``, it is ``/in``; without path
(``sftp://host``), it is ``in`` in the login directory of the user. A folder or a file name
resolving outside of it (``..``) is refused, and a file name cannot contain ``/``.

A transport module only implements the primitives, all ``:edi-hook: framework``,
raising ``EdiTransportError`` on any error:

.. list-table::
   :widths: 40 60
   :header-rows: 1

   * - Method
     - Purpose
   * - ``_file_list(session, path)``
     - ``[(name, meta), ...]`` of the files of a folder, in any order. ``meta`` should give
       ``size`` and ``modify`` (aware datetime), used to sort them
   * - ``_file_exists(session, path)``
     - Whether a file exists. Default: based on ``_file_list``, override when cheaper
   * - ``_file_read(session, path)``
     - Content of a file, as bytes
   * - ``_file_write(session, path, content)``
     - Write a file, replacing it if it exists
   * - ``_file_rename(session, path, new_path)``
     - Rename or move a file (the target does not exist)
   * - ``_file_replace(session, path, target)``
     - Move a file over an existing one. Default: in two steps, override with an atomic rename
       when available (e.g. SFTP ``posix-rename``)
   * - ``_file_delete(session, path)``
     - Delete a file

Adding a transport
------------------

A framework module (e.g. ``edi_ftp_connection``):

#.  adds the value to the ``transport`` selection (``selection_add``, ``ondelete="cascade"``);
#.  declares it in ``_get_transports()``, with the URL schemes and the authentications it accepts,
    and whether it supports client certificates and is file based;
#.  overrides ``_edi_connect`` and, for a file based transport, the ``_file_*`` primitives (or
    the other ``_edi_*`` methods otherwise), following the override pattern above;
#.  adds its transport-specific fields, if any (e.g. the FTP passive mode), to the form in the
    ``transport`` group, ``invisible`` unless its transport is selected; a field that rarely needs a
    change is also hidden unless ``show_advanced``, and declared with its default value in
    ``_get_advanced_defaults()`` (``:edi-hook: framework``), so that ``show_advanced`` is checked as
    soon as it is changed;
#.  tests the transport by mocking the underlying library (``ftplib``, ...), e.g. with an
    in-memory fake server as in ``edi_ftp_connection``.

Tests
=====

-   Base test classes on ``odoo.addons.base.tests.common.BaseCommon``. In 20.0 each test runs as a
    dedicated ``test_user``: grant it groups with ``_test_user_groups``, e.g.
    ``_test_user_groups = ("base.group_system",)`` for connections.
-   Never reach a real remote system: mock the library at its boundary
    (``requests.Session.request``, ``requests.post``, ...).
-   With odev, pass the path of the repository explicitly, otherwise the main checkout may be used::

        odev test <database> <path/to/ps-internal-edi> -i edi_base -t /edi_base

Upgrade from 19.0
=================

Each module migrates its own 19.0 data in ``migrations/20.0.1.0.0/``. Data still needed by another
module (e.g. the 19.0 JSON ``configuration`` of connections, read by the transport modules and the
flows) is only removed in an ``end-`` script, once every module has been migrated.
