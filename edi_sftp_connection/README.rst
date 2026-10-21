==============
SFTP Connector
==============

Adds the ``sftp`` transport to the connections of ``edi_base``, with paramiko
(``external_dependencies``: ``paramiko``, pinned in ``requirements.txt``).

-   URL: ``sftp://host[:port][/path]``; without path, the folders are relative to the login
    directory
-   Authentication: *Username / Password*, or *SSH Key* (RSA, ECDSA or Ed25519, OpenSSH or PEM
    format, with its passphrase if encrypted)

Only the credentials of the connection are used: no SSH agent, no key of ``~/.ssh``, no
``known_hosts`` file of the server running Odoo.

Host key
========

The **Host Key** identifies the server, as in a ``known_hosts`` file (``ssh-ed25519 AAAA...``):

-   when it is empty, the key presented by the server on the first connection attempt (*Test
    Connection* or a run) is saved, and its fingerprint is posted in the chatter: compare it with
    the fingerprint given by the remote system
-   once saved, the server must always present this key; otherwise the connection is refused,
    as it may be an attack. If the server key changed on purpose, clear the field and connect
    again.

The received and sent files are handled as described in ``edi_base`` (tab *File Transfer*):
success and failed folders, conflicts, order of the files. A file is replaced atomically when the
server supports it (``posix-rename``, OpenSSH).

Example::

    <odoo noupdate="1">
        <record id="acme_sftp" model="edi.connection">
            <field name="name">ACME SFTP</field>
            <field name="transport">sftp</field>
            <field name="url">sftp://sftp.acme.com/edi</field>
            <field name="auth_type">ssh_key</field>
            <field name="username">odoo</field>
            <!-- private key set in production, host key saved on the first connection -->
        </record>
    </odoo>

Upgrade from 19.0
=================

pysftp is not used anymore. The JSON ``configuration`` is migrated by ``edi_base`` (credentials,
private key, folders, conflicts), and the 19.0 RSA ``host_key`` becomes ``ssh-rsa <key>``.
Connections without host key keep working: their key is saved on their first connection.
