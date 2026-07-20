=============
FTP Connector
=============

------------
Particularity
------------

Adds the ``type="ftp"`` connection to ``edi_base``. Unlike the built-in ``type="api"``
connection, credentials and paths are **not** exposed as dedicated fields: they live in the
free-form ``configuration`` JSON field (``host``, ``user``, ``password``, ``in_folder``,
``out_folder``, ``in_folder_done``, ``in_folder_error``, ``on_conflict``, …).

There is no ``path`` / ``method`` on the integration either: the connection always uses the
legacy ``_fetch_synchronizations`` (IN) / ``_send_synchronization`` (OUT) hooks, moving files
in/out of ``in_folder`` / ``out_folder``. The only business logic left to implement on the
integration is ``_process_content`` (IN) and ``_get_content`` (OUT) — exactly like any other
connection type.

--------------------
Example — IN flow
--------------------

Scenario: pick up order files dropped in an FTP ``in_folder`` and process them one by one.

``data/edi.xml``
================

.. code-block:: xml

    <!-- Connection: type="ftp". Credentials & folders live in the "configuration" JSON field. -->
    <record id="acme_ftp_connection" model="edi.connection">
        <field name="name">ACME FTP</field>
        <field name="type">ftp</field>
        <field name="configuration"><![CDATA[
    {
        "host": "ftp.acme.com",
        "user": "acme_user",
        "password": "MY_SECRET_PASSWORD",
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
        <field name="name">Import ACME Orders (FTP)</field>
        <field name="type">acme_orders_ftp_in</field>
        <field name="integration_flow">in</field>
        <field name="synchronization_content_type">json</field>
        <field name="synchronization_creation">1</field>
        <field name="connection_id" ref="acme_ftp_connection"/>
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


    class AcmeOrdersFtpIn(models.Model):
        _inherit = "edi.integration"

        type = fields.Selection(
            selection_add=[("acme_orders_ftp_in", "ACME - Import Orders (FTP)")],
            ondelete={"acme_orders_ftp_in": "cascade"},
        )

        # --- Required: process each file downloaded from in_folder ---
        @IntegrationCheck("acme_orders_ftp_in")
        def _process_content(self, data):
            for d in data:
                order = json.loads(d["content"])
                self.env["sale.order"].create_or_update_from_acme(order)
            return "done"


---------------------
Example — OUT flow
---------------------

Scenario: drop one JSON file per product batch into the FTP ``out_folder``.

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
        <field name="name">Export Stock to ACME (FTP)</field>
        <field name="type">acme_stock_ftp_out</field>
        <field name="integration_flow">out</field>
        <field name="synchronization_content_type">json</field>
        <field name="synchronization_creation">50</field>
        <field name="connection_id" ref="acme_ftp_connection"/>
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


    class AcmeStockFtpOut(models.Model):
        _inherit = "edi.integration"

        type = fields.Selection(
            selection_add=[("acme_stock_ftp_out", "ACME - Export Stock (FTP)")],
            ondelete={"acme_stock_ftp_out": "cascade"},
        )

        # --- Required: serialize records into the file content dropped in out_folder ---
        @IntegrationCheck("acme_stock_ftp_out")
        def _get_content(self, records):
            return json.dumps([
                {"sku": p.default_code, "qty": p.qty_available}
                for p in records
            ])
