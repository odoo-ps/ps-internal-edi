===========
EDI 2-steps
===========

-------------
Particularity
-------------

Extends ``edi_base`` to split a flow into two independent steps instead of one:

-   **Step 1** converts the source data (IN) or the records to send (OUT) into
    ``edi.table.record`` rows — a lightweight intermediate queue — without touching any
    business model.
-   **Step 2**, run by its own dedicated cron, later picks up pending ``edi.table.record`` rows
    and does the actual business processing (IN) / sending (OUT).

The two steps never run concurrently for the same integration (guarded by a DB-level lock), so
step 2 never processes a row step 1 is still writing. Splitting this way decouples fetching/
capturing data from processing it: capture keeps running even while processing is slow, and a
row that failed processing stays in the queue to be retried on the next step-2 run, without
re-fetching it from the source.

-------------
Configuration
-------------

On the integration form, once **Use EDI 2-steps** (``use_edi_table``) is checked:

-   ``edi_table_synchronization_creation``: how many ``edi.table.record`` rows step 2 processes
    per synchronization — same semantics as ``synchronization_creation`` (``1`` = one by one,
    ``0`` = all together, ``n`` = batches of n)
-   ``edi_table_match_identifier`` ("Match Identifier"): if checked, step 1 looks for an
    existing, not-yet-processed ``edi.table.record`` with the same ``identifier`` and updates
    it instead of creating a new one
-   ``edi_table_record_clear_content`` ("Clear Record Content"): when to empty ``content`` —
    ``on_success``, ``on_error``, ``on_success_or_error`` (``edi_archiving`` adds the
    ``on_archiving`` variants)

A dedicated cron ("Process EDI 2-steps queue for <integration>") is created automatically,
polling every minute by default — adjustable afterwards via ``edi_table_interval_number`` /
``edi_table_interval_type`` on the integration form. It is archived automatically if
**Use EDI 2-steps** is unchecked, and follows the integration's own ``active`` state.

------------
How it works
------------

``edi.table.record.state``:

-   ``new``: created by step 1, waiting for step 2
-   ``warning``: step 2 failed with a recoverable error (e.g. a concurrent DB update) — stays
    eligible for a retry
-   ``fail``: step 2 failed and the row cannot be processed anymore (terminal)
-   ``done``: processed successfully by step 2 (terminal)
-   ``cancelled``: cancelled by a user, cannot be processed anymore (terminal)

Only ``new`` and ``warning`` rows are picked up by step 2 (and matched by
``edi_table_match_identifier``).

--------
Examples
--------

Two methods are required per flow direction; the rest of the integration (connection,
``path``/``method`` or FTP/SFTP folders, ``synchronization_content_type``, …) is configured
exactly as without EDI 2-steps.

``data/edi.xml``
================

.. code-block:: xml

    <record id="acme_orders_in" model="edi.integration">
        <field name="name">Import ACME Orders</field>
        <field name="type">acme_orders_in</field>
        <field name="integration_flow">in</field>
        <field name="connection_id" ref="acme_connection"/>
        <!-- ... path/method or FTP folders as usual ... -->

        <!-- EDI 2-steps -->
        <field name="use_edi_table" eval="True"/>
        <field name="edi_table_match_identifier" eval="True"/>
        <field name="edi_table_synchronization_creation">1</field>
    </record>

``models/acme_integration.py``
==============================

.. code-block:: python

    from odoo import fields, models
    from odoo.addons.edi_base.decorators import IntegrationCheck


    class AcmeOrdersIn(models.Model):
        _inherit = "edi.integration"

        type = fields.Selection(
            selection_add=[("acme_orders_in", "ACME - Import Orders")],
            ondelete={"acme_orders_in": "cascade"},
        )

        # --- Step 1: split the raw IN data into one edi.table.record per order ---
        @IntegrationCheck("acme_orders_in")
        def _prepare_in_edi_table(self, data):
            return [
                {"identifier": order["id"], "content": order["content"], "filename": d["filename"]}
                for d in data
                for order in [d["content"]]
            ]

        # --- Step 2: process one edi.table.record ---
        @IntegrationCheck("acme_orders_in")
        def _process_in_edi_table(self, data):
            for d in data:
                self.env["sale.order"].create_or_update_from_acme(d["content"])
            return "done"

        # --- Step 1: convert products to send into edi.table.record content ---
        @IntegrationCheck("acme_stock_out")
        def _prepare_out_edi_table(self, records):
            return [{"identifier": p.default_code, "content": p.default_code} for p in records]

        # --- Step 2: build what will actually be sent for one edi.table.record ---
        @IntegrationCheck("acme_stock_out")
        def _process_out_edi_table(self, records):
            return "\n".join(records.mapped("content"))

------------------------
Reference: key overrides
------------------------

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Method
     - When to override
   * - ``_prepare_in_edi_table(data)`` / ``_prepare_out_edi_table(records)``
     - **Required.** Step 1: build the ``edi.table.record`` vals (``identifier``, ``content``)
       from the IN data / OUT records.
   * - ``_process_in_edi_table(data)`` / ``_process_out_edi_table(records)``
     - **Required.** Step 2: business logic, same role as ``_process_content`` /
       ``_get_content`` without EDI 2-steps.
   * - ``_get_edi_table_record_name_in(data)`` / ``_get_table_record_name_out(records)``
     - Override to customize the ``edi.table.record`` name. Default: integration name + date +
       filenames.
   * - ``_handle_error_edi_table_in/out`` / ``_handle_success_edi_table_in/out``
     - Called after step 2 processes each row, in addition to the standard
       ``_handle_error`` / ``_postprocess``. Default: no-op (OUT success writes back the
       synchronization ``filename``).
   * - ``_postprocess_edi_table(send_result, content, records)``
     - OUT only. Called after step 2 sends the content. Default: no-op.
