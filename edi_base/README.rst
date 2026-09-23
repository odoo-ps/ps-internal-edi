==================
Framework edi_base
==================

--------------
The 3 concepts
--------------

This module provides a framework for data exchange between Odoo and external systems.
Three objects answer three questions:

-   **What** data to exchange → ``edi.integration``
-   **How** to exchange it → ``edi.connection``
-   **What happened** → ``edi.synchronization``

Connection
==========

Holds credentials and communication logic for a third-party system. The built-in ``type="api"``
connection covers most REST APIs without writing any connection code:

-   ``url``: base URL shared by all integrations, e.g. ``https://api.example.com``
-   ``credential_type``: what credentials are stored — drives which credential fields appear in the UI
-   ``auth_method``: how the credentials are transmitted — drives the HTTP authentication mechanism

Both fields are set in data XML by developers — there is no edition through the backend.
The supported combinations are:

.. list-table::
   :widths: 22 18 60
   :header-rows: 1

   * - ``credential_type``
     - ``auth_method``
     - Fields used
   * - ``none``
     - ``public``
     - — (no credentials)
   * - ``single_key``
     - ``http_bearer``
     - ``key``, ``key_header_name`` (default: ``Authorization``), ``key_format`` (default: ``Bearer {}``)
   * - ``user_pass``
     - ``http_basic``
     - ``username``, ``password``
   * - ``key_secret``
     - ``http_oauth2``
     - ``client_id`` (key), ``client_secret`` (secret), ``scope``, ``token_path`` → *client_credentials* grant
   * - ``user_key_secret``
     - ``http_oauth2``
     - ``username``, ``password``, ``client_id``, ``client_secret``, ``scope``, ``token_path`` → *password* grant

``key_format`` accepts any Python format string where ``{}`` is replaced by the key value:
``"Bearer {}"`` (default), ``"Token {}"`` (DRF-style), or ``"{}"`` alone for headers such as
``X-API-Key`` or ``X-Auth-Token``. ``key_header_name`` controls which header carries the value.

The grant type for OAuth2 is derived automatically from ``credential_type`` — no separate field.
``cached_token`` and ``cached_token_expires`` store the obtained token and are refreshed transparently.

For FTP/SFTP, dedicated modules (``edi_ftp_connection``, ``edi_sftp_connection``) are available.

Integration
===========

Orchestrates the flow. Inherits ``ir.cron`` (scheduling is built-in).
The ``type`` field is a required discriminator — always guard your method overrides with it.

For API connections, two fields configure the resource endpoint:

-   ``path``: relative URL path for this integration's endpoint, e.g. ``/v1/orders``
-   ``method``: HTTP method (GET, POST, …)

When ``connection_type == 'api'`` and ``path`` is set:

- **IN flow**: the framework calls ``connection.url + path`` automatically and passes the
  response to ``_process_content``.
- **OUT flow**: the framework calls the endpoint with the content returned by ``_get_content``.
  The API response is stored in ``received_content`` for traceability.

Without ``path``, the fallback ``_fetch_synchronizations`` / ``_send_synchronization``
path is used (FTP/SFTP, custom connectors). Both paths coexist — no breaking change.

Synchronization
===============

A log record created for each execution. Key fields:

-   ``received_content``: what was received from the external system (IN: data fetched, OUT: API response)
-   ``sent_content``: what was sent to the external system (OUT: payload, IN: query payload if any)
-   ``state``: ``done`` / ``fail``
-   ``error_ids``: detailed error messages

Upgrading to 19.0.2.1.0 on a large database
===========================================

``edi.synchronization.error.synchronization_id`` is indexed from this version on. The foreign key
is ``ON DELETE CASCADE``, and PostgreSQL enforces that cascade with one
``DELETE FROM edi_synchronization_error WHERE synchronization_id = $1`` per deleted parent row —
without the index, each of those scans the whole error table. On the installation that prompted
this change the table had reached 33.9 M rows / 53 GB, and deleting a backlog was impossible.

Odoo creates the index with a plain ``CREATE INDEX`` during the upgrade, which holds an
``ACCESS EXCLUSIVE`` lock on the table for the whole build. On a table of that size that is tens of
minutes during which the table cannot be read or written. **If your error table is large, build the
index by hand before deploying the upgrade**:

.. code-block:: sql

    CREATE INDEX CONCURRENTLY edi_synchronization_error__synchronization_id_index
        ON edi_synchronization_error (synchronization_id);

``CONCURRENTLY`` does not take the blocking lock, so it can run on a live database. The name above
is the one Odoo derives from the table and column, so the ORM finds it and skips creation — the
upgrade then costs nothing. Check ``pg_index.indisvalid`` afterwards: a ``CONCURRENTLY`` build that
fails leaves an **invalid** index behind, which the planner ignores. The 19.0.2.1.0 pre-migration
drops such a leftover so that the ORM rebuilds it rather than skipping it on the strength of its
name alone.

The upgrade will not complete unless the index ends up present and valid: the ORM logs a failed
``CREATE INDEX`` and carries on, so the 19.0.2.1.0 post-migration checks the catalog and raises
rather than let the module be recorded at this version with the cascade still unindexed. If it
raises, look for the reason at ERROR level under ``odoo.schema`` — a full disk or a
``statement_timeout`` while building the index are the usual causes — then build the index with
``CONCURRENTLY`` as above and re-run the upgrade.

------------
How to start
------------

Decide your setup by answering those questions:

1. **REST API?**

   use built-in ``type="api"`` connection, configure ``url`` and auth fields,
   set ``path`` + ``method`` on each integration,
   implement business logic on the integration:

   - IN :
      - Build request : ``_build_in_payload``
      - Parse response : ``_process_content``
   - OUT :
      - Build payload : ``_get_content``

2. **FTP or SFTP?**

   use ``edi_ftp_connection`` or ``edi_sftp_connection``,
   implement business logic on the integration :

   - IN : ``_process_content``
   - OUT : ``_get_content``

3. **New custom protocol?**

   implement ``_fetch_synchronizations`` / ``_send_synchronization``
   on a new connection type. See the reference table at the bottom of this file.

The examples below cover the most common API cases.

--------------------------------
Example 1 — IN flow with API Key
--------------------------------

Scenario: pull a list of orders from a REST API secured with an API key.
The API returns a JSON array; each order is processed as a separate synchronization.

``data/edi.xml``
================

.. code-block:: xml

    <!-- Connection: built-in type="api". API key lives on the connection. -->
    <record id="acme_connection" model="edi.connection">
        <field name="name">ACME API</field>
        <field name="type">api</field>
        <field name="credential_type">single_key</field>
        <field name="url">https://api.acme.com</field>
        <field name="auth_method">http_bearer</field>
        <field name="key">MY_SECRET_KEY</field>
    </record>

    <!-- Integration: path + method define the resource endpoint -->
    <record id="acme_orders_in" model="edi.integration">
        <field name="name">Import ACME Orders</field>
        <field name="type">acme_orders_in</field>
        <field name="integration_flow">in</field>
        <field name="synchronization_content_type">json</field>
        <field name="response_content_type">json</field>  <!-- enables JSON pretty-print in UI -->
        <field name="synchronization_creation">1</field>
        <field name="connection_id" ref="acme_connection"/>
        <field name="path">/v1/orders</field>
        <field name="method">get</field>
        <field name="interval_number">1</field>
        <field name="interval_type">hours</field>
        <field name="active" eval="False"/>
    </record>

``models/acme_integration.py``
==============================

.. code-block:: python

    import json
    from odoo import fields, models


    class AcmeOrdersIn(models.Model):
        _inherit = "edi.integration"

        type = fields.Selection(
            selection_add=[("acme_orders_in", "ACME - Import Orders")],
            ondelete={"acme_orders_in": "cascade"},
        )

        # --- Optional: pass query parameters (pagination, date filter…) ---
        @IntegrationCheck("acme_orders_in")
        def _build_in_payload(self):
            # GET params appended to the URL
            return {"since": str(self.last_success_date or "2000-01-01"), "limit": 100}

        # --- Optional: split a multi-item response into one sync per order ---
        @IntegrationCheck("acme_orders_in")
        def _api_wrap_response(self, raw_text):
            orders = json.loads(raw_text).get("orders", [])
            return [
                {"filename": f"order_{o['id']}", "content": json.dumps(o)}
                for o in orders
            ]

        # --- Required: process the received content ---
        @IntegrationCheck("acme_orders_in")
        def _process_content(self, data):
            for d in data:
                order = json.loads(d["content"])
                self.env["sale.order"].create_or_update_from_acme(order)
            return "done"


--------------------------------
Example 2 — OUT flow with OAuth2
--------------------------------

Scenario: push stock levels to a REST API that requires OAuth2 client credentials.

``data/edi.xml``
================

.. code-block:: xml

    <!-- Connection: OAuth2 auth. Token credentials and resource base URL live here.        -->
    <!-- credential_type="key_secret" → client_credentials grant (no username needed).     -->
    <record id="wms_connection" model="edi.connection">
        <field name="name">WMS API</field>
        <field name="type">api</field>
        <field name="credential_type">key_secret</field>
        <field name="url">https://wms.example.com</field>
        <field name="auth_method">http_oauth2</field>
        <field name="token_path">/oauth/token</field>
        <field name="client_id">MY_CLIENT_ID</field>
        <field name="client_secret">MY_CLIENT_SECRET</field>
    </record>

    <!-- Filter: which products to send -->
    <record id="wms_stock_filter" model="ir.filters">
        <field name="name">WMS - Products to sync</field>
        <field name="model_id">product.product</field>
        <field name="domain">[["type", "=", "product"]]</field>
    </record>

    <!-- Integration: batches of 50 products per synchronization -->
    <record id="wms_stock_out" model="edi.integration">
        <field name="name">Export Stock to WMS</field>
        <field name="type">wms_stock_out</field>
        <field name="integration_flow">out</field>
        <field name="synchronization_content_type">json</field>
        <field name="synchronization_creation">50</field>
        <field name="connection_id" ref="wms_connection"/>
        <field name="path">/api/v2/stock</field>
        <field name="method">post</field>
        <field name="record_filter_id" ref="wms_stock_filter"/>
        <field name="interval_number">1</field>
        <field name="interval_type">hours</field>
        <field name="active" eval="False"/>
    </record>

``models/wms_integration.py``
=============================

.. code-block:: python

    import json
    from odoo import fields, models


    class WmsStockOut(models.Model):
        _inherit = "edi.integration"

        type = fields.Selection(
            selection_add=[("wms_stock_out", "WMS - Export Stock")],
            ondelete={"wms_stock_out": "cascade"},
        )

        # --- Required: serialize records into the API payload ---
        @IntegrationCheck("wms_stock_out")
        def _get_content(self, records):
            return json.dumps([
                {"sku": p.default_code, "qty": p.qty_available}
                for p in records
            ])

        # --- Optional: post-process after successful send ---
        @IntegrationCheck("wms_stock_out")
        def _postprocess(self, response, content, records):
            records.write({"wms_last_sync": fields.Datetime.now()})


------------------------
Reference: key overrides
------------------------

Integration — IN flow
=====================

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Method
     - When to override
   * - ``_process_content(data)``
     - **Required.** Process the received data. ``data`` is a list of ``{filename, content}`` dicts.
       Return ``"done"``.
   * - ``_build_in_payload()``
     - Return a dict of query parameters to pass to the API (e.g. pagination, date filter).
       Default: no parameters.
   * - ``_api_wrap_response(raw_text)``
     - Split a multi-item API response into individual items (one dict per entity).
       The framework then batches those items into synchronizations based on ``synchronization_creation``.
       Default: the whole response as a single item.
   * - ``_get_synchronization_name_in(data)``
     - Override to customize the synchronization record name. Default: integration name + date + filenames.
   * - ``_clean(data, status)``
     - Called after each synchronization. Override for post-IN cleanup.

Integration — OUT flow
======================

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Method
     - When to override
   * - ``_get_content(records)``
     - **Required.** Serialize the recordset into the payload (string or dict).
       This is the only override needed in the vast majority of cases.
   * - ``_build_out_payload(content)``
     - Advanced hook, rarely needed. Override only if the integration must support
       both API and FTP/SFTP and the API requires a different envelope than the raw
       ``_get_content`` output. Default: pass content as-is.
   * - ``_postprocess(response, content, records)``
     - Called after each successful send. Use to update record status, write sync dates, etc.
   * - ``_get_record_to_send()``
     - Override if the default filter-based record selection does not fit.
   * - ``_get_synchronization_name_out(records)``
     - Override to customize the synchronization record name. Default: integration name + date + record IDs.
   * - ``_handle_error(data, exc)``
     - Called when a synchronization fails. Default: clean up and report.

Connection — custom type (legacy / FTP)
========================================

Only needed when **not** using the built-in ``type="api"`` connection:

.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - Method
     - Purpose
   * - ``_fetch_synchronizations()``
     - Return ``[{"filename": …, "content": …}, …]`` — used by IN flows without a path.
   * - ``_send_synchronization(filename, content)``
     - Send content to the remote system — used by OUT flows without a path.
   * - ``_clean_synchronization_in(data, status)``
     - Called after each IN sync (e.g. move processed file to an archive folder).
   * - ``_clean_synchronization_out(filename, status)``
     - Called after each OUT sync (e.g. delete a sent file on error).
   * - ``_get_default_configuration()``
     - Return the JSON template shown in the UI when the connection type is selected.
   * - ``test()``
     - Implement the "Test Connection" button behavior.

Other useful options
====================

-   **type of an integration :**

    The type field is required, and corresponds to a unique name for an integration. As many integrations can define or
    redefine methods with a same name, it allows to be sure that you execute a method only for a specific type.
    So a check on the type is a required security when you define a method on an integration.
    This can be achieved by using the `IntegrationCheck` decorator which will call the super method if the record's type
    is different from the expected type (passed as first argument to the decorator). For example :

    .. code-block:: python

        @IntegrationCheck("get_products_from_xx_software")
        def _process_content(self, data):
              # then I can write my code

    If needed, an exception can be raised if such method is not expected to be used by another integration. For example:

    .. code-block:: python

        @IntegrationCheck("get_products_from_xx_software", raise_if_wrong_integration=True)
        def _process_content(self, data):
              # then I can write my code

    If called by a wrong integration, this will raise an error stating: "This method can only be called in the integration of type get_products_from_xx_software!"


-   **``synchronization_creation``**: controls batching.
    ``1`` = one record/file per sync, ``0`` = all in one sync, ``n`` = batches of n.

-   **``store_received_content`` / ``store_sent_content``**: toggle whether content is
    persisted on the synchronization record (default: both True).

-   **``_process_realtime(data)``**: call this instead of ``process_integration()`` when
    triggering from a button or automation rule. It flushes the current cursor first and
    can accept an explicit recordset (OUT) or data list (IN).

-   **``_on_synchronizations_done(exceptions)``**: hook called once after all
    synchronizations have completed. Use for pagination state, global counters, etc.
