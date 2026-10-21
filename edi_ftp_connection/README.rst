=============
FTP Connector
=============

Adds the ``ftp`` transport to the connections of ``edi_base``, with the Python standard library
(no dependency):

-   ``ftp://host[:port][/path]``: plain FTP
-   ``ftps://host[:port][/path]``: FTP over explicit TLS (``AUTH TLS``), with an optional client
    certificate (tab *Client Certificate*)

Authentication: anonymous (*None*) or *Username / Password*. Passive mode is used by default,
disable *Passive Mode* only if the server requires the active mode.

The received and sent files are handled as described in ``edi_base`` (tab *File Transfer*):
success and failed folders, conflicts, order of the files. Servers without the ``MLSD`` command
are supported (listing with ``NLST``, dates with ``MDTM``).

Example::

    <odoo noupdate="1">
        <record id="acme_ftp" model="edi.connection">
            <field name="name">ACME FTP</field>
            <field name="transport">ftp</field>
            <field name="url">ftps://ftp.acme.com/edi</field>
            <field name="auth_type">basic</field>
            <field name="username">odoo</field>
            <!-- password set in production -->
            <field name="file_processed_action">move</field>
            <field name="file_success_folder">done</field>
            <field name="file_failed_folder">error</field>
        </record>
    </odoo>

Upgrade from 19.0
=================

The JSON ``configuration`` is migrated by ``edi_base`` (credentials, folders, conflicts), and
``"is_active": "True"`` becomes *Passive Mode* disabled. Implicit FTPS (port 990) is not supported.
