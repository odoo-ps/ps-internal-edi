==============
SFTP Connector
==============

------------
Particularity
------------

Adds the ``type="sftp"`` connection to ``edi_base``. Same shape as ``edi_ftp_connection``
(credentials & folders live in the ``configuration`` JSON field, no ``path``/``method`` on the
integration, business logic goes through ``_process_content`` / ``_get_content``), with two
SFTP-specific additions in ``configuration``:

-   ``key``: a private RSA key (PEM string) — used instead of ``password`` when set
-   ``host_key``: the server's public host key

--------------------
Example — IN flow
--------------------

Scenario: pick up order files dropped in an SFTP ``in_folder``, authenticating with a private key.

``data/edi.xml``
================

.. code-block:: xml

    <!-- Connection: type="sftp". "key" is used instead of "password" when both are set. -->
    <record id="acme_sftp_connection" model="edi.connection">
        <field name="name">ACME SFTP</field>
        <field name="type">sftp</field>
        <field name="configuration"><![CDATA[
    {
        "host": "sftp.acme.com",
        "user": "acme_user",
        "key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
        "in_folder": "/in",
        "in_folder_done": "/in/done",
        "in_folder_error": "/in/error",
        "out_folder": "/out",
        "on_conflict": "rename"
    }
        ]]></field>
    </record>

    <!-- Integration: no "path"/"method" — files are picked up from in_folder -->
    <record id="acme_orders_in" model="edi.integration">
        <field name="name">Import ACME Orders (SFTP)</field>
        <field name="type">acme_orders_sftp_in</field>
        <field name="integration_flow">in</field>
        <field name="synchronization_content_type">json</field>
        <field name="synchronization_creation">1</field>
        <field name="connection_id" ref="acme_sftp_connection"/>
        <field name="interval_number">1</field>
        <field name="interval_type">hours</field>
        <field name="active" eval="False"/>
    </record>

``models/acme_integration.py``
==============================

.. code-block:: python

    import json
    from odoo import fields, models
    from odoo.addons.edi_base.decorators import IntegrationCheck


    class AcmeOrdersSftpIn(models.Model):
        _inherit = "edi.integration"

        type = fields.Selection(
            selection_add=[("acme_orders_sftp_in", "ACME - Import Orders (SFTP)")],
            ondelete={"acme_orders_sftp_in": "cascade"},
        )

        # --- Required: process each file downloaded from in_folder ---
        @IntegrationCheck("acme_orders_sftp_in")
        def _process_content(self, data):
            for d in data:
                order = json.loads(d["content"])
                self.env["sale.order"].create_or_update_from_acme(order)
            return "done"


---------------------
Example — OUT flow
---------------------

Scenario: drop one JSON file per product batch into the SFTP ``out_folder``.

``data/edi.xml``
================

.. code-block:: xml

    <!-- Same connection as above can be reused, "in" and "out" share it -->

    <!-- Filter: which products to send -->
    <record id="acme_stock_filter" model="ir.filters">
        <field name="name">ACME - Products to sync</field>
        <field name="model_id">product.product</field>
        <field name="domain">[["type", "=", "product"]]</field>
    </record>

    <!-- Integration: batches of 50 products per file -->
    <record id="acme_stock_out" model="edi.integration">
        <field name="name">Export Stock to ACME (SFTP)</field>
        <field name="type">acme_stock_sftp_out</field>
        <field name="integration_flow">out</field>
        <field name="synchronization_content_type">json</field>
        <field name="synchronization_creation">50</field>
        <field name="connection_id" ref="acme_sftp_connection"/>
        <field name="record_filter_id" ref="acme_stock_filter"/>
        <field name="interval_number">1</field>
        <field name="interval_type">hours</field>
        <field name="active" eval="False"/>
    </record>

``models/acme_integration.py``
==============================

.. code-block:: python

    import json
    from odoo import fields, models
    from odoo.addons.edi_base.decorators import IntegrationCheck


    class AcmeStockSftpOut(models.Model):
        _inherit = "edi.integration"

        type = fields.Selection(
            selection_add=[("acme_stock_sftp_out", "ACME - Export Stock (SFTP)")],
            ondelete={"acme_stock_sftp_out": "cascade"},
        )

        # --- Required: serialize records into the file content dropped in out_folder ---
        @IntegrationCheck("acme_stock_sftp_out")
        def _get_content(self, records):
            return json.dumps([
                {"sku": p.default_code, "qty": p.qty_available}
                for p in records
            ])


------------------------------
About the private key encoding
------------------------------

``configuration`` is a JSON field, so ``key`` must be a valid JSON string. A PEM key spans
several lines, and pasting it as-is (real line breaks) into the XML above breaks JSON parsing —
each line break needs to be escaped as ``\n`` first.

Don't escape it by hand: build the ``configuration`` dict in a small Python snippet, let
``json.dumps`` do the escaping, then paste the result into the XML.

Read the key from its file rather than pasting it inline in the script — a triple-quoted string
indented inside a code block would carry that indentation into every line of the key, which can
break PEM parsing.

.. code-block:: python

    import json
    with open("/path/to/id_rsa") as f:
        print(json.dumps({"key": f.read()}, indent=4))

The printed output is valid JSON — with ``key`` on a single line, ``\n`` already in place — and
can be copy-pasted directly inside the ``<![CDATA[ ... ]]>`` block of ``configuration``.
