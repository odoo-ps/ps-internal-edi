==================
Framework edi_base
==================

Data exchange between Odoo and remote systems (APIs, file servers, ...), configured in the UI.
The terms used here are defined in the `nomenclature <../doc/nomenclature.rst>`_, the extension
points in the `developer guide <../doc/developer_guide.rst>`_.

.. note::
    Being rebuilt for 20.0: this version contains the connections. The flows (format, field
    mapping, triggers, queue, runs) come next.

-----------
Connections
-----------

Menu **EDI ▸ Configuration ▸ Connections**. A connection says *where* the remote system is and
*how Odoo logs in*. Two independent choices:

-   **Transport**: how the bytes are moved. ``http`` (HTTP or HTTPS) is in this module, ``ftp``
    in ``edi_ftp_connection``, ``sftp`` in ``edi_sftp_connection``.
-   **Authentication**: how Odoo proves who it is. Any authentication accepted by the transport
    can be used with it.

.. list-table::
   :widths: 22 48 30
   :header-rows: 1

   * - Authentication
     - Credentials
     - Transports
   * - None
     - —
     - HTTP, FTP
   * - Username / Password
     - username, password
     - HTTP (Basic), FTP, SFTP
   * - API Key
     - key, sent in a header or a query parameter, with an optional prefix (``Bearer``, ``Token``)
     - HTTP
   * - OAuth2
     - client ID/secret and token URL. Grant types: client credentials, password, or
       authorization code (a user clicks *Connect* once, see below)
     - HTTP
   * - SSH Key
     - username, private key (RSA, ECDSA, Ed25519), passphrase
     - SFTP

The credentials are in the *Credentials* tab. A client module can add an authentication specific to
one remote system, see the developer guide. The **Test Connection** button checks the URL and the
credentials.

Optional settings
=================

In the tabs of the form, depending on the transport. The settings that rarely need a change
(timeout, API key header and prefix, OAuth2 scope and PKCE, conflict policy of the sent files, FTP
passive mode, client certificate, extra headers) are only shown with **Advanced Settings**, which
is checked automatically as soon as one of them is changed.

-   **File Transfer** (FTP, SFTP):

    -   *Received files*, once processed: moved (to the success folder, default ``done``), deleted,
        or left in place. When processing fails: moved to the failed folder (default ``error``) if
        the processed files are moved, otherwise left in place to be retried. The folders are relative to the folder of the file
        (``done`` for ``in/done``), or to the path of the URL if they start with ``/``.
    -   *Sent files*, when a file with the same name exists: fail, rename the existing file
        (``<name>-<UTC date>-utc.old``) or replace it.

-   **Client Certificate** (HTTPS, FTPS): mutual TLS, combinable with any authentication. Upload
    a PEM file (certificate, optionally with its private key) or a PKCS#12 file (``.p12``,
    ``.pfx``), with its password if any. Its expiry date is shown.
-   **HTTP Headers** (HTTP): extra headers sent with every request, one ``Name: value`` per line,
    e.g. an Azure API Management subscription key or an API version.

URL
===

The URL holds the scheme of the transport, the server, its port, and a base path:

-   ``https://api.example.com/v1``: the endpoints of the flows are relative to it
-   ``sftp://files.example.com:2222/acme``: the folders of the flows are relative to ``/acme``
-   ``sftp://files.example.com``: without path, the folders are relative to the login directory

OAuth2 authorization code
=========================

For the APIs where a user gives Odoo access (Microsoft 365, Google, Xero, Exact Online, ...):

#.  in the application of the remote system, register the **Redirect URI** shown on the connection
#.  fill the authorization URL, token URL, client ID/secret and scope (with ``offline_access`` or
    equivalent, to get a refresh token)
#.  click **Connect**, log in on the remote system and accept

The tokens are then renewed automatically. If the access is revoked, the errors ask to click on
*Connect* again.

-------
Example
-------

Connections are usually created in data files of the client module, with ``noupdate`` so that the
credentials set in production are kept::

    <odoo noupdate="1">
        <record id="acme_api" model="edi.connection">
            <field name="name">ACME API</field>
            <field name="transport">http</field>
            <field name="url">https://api.acme.com/v2</field>
            <field name="auth_type">oauth2</field>
            <field name="oauth2_grant_type">client_credentials</field>
            <field name="oauth2_token_url">https://auth.acme.com/oauth/token</field>
            <field name="oauth2_scope">orders stock</field>
            <!-- client ID and secret set in production -->
        </record>
    </odoo>

-----------------
Upgrade from 19.0
-----------------

The connections are migrated: the 19.0 ``type`` becomes the transport, the API credentials and the
FTP/SFTP JSON ``configuration`` (host, port, user, password, key, conflict policy, done and error
folders) become the fields above.
