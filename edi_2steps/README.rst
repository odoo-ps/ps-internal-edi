===================
EDI Table (2 steps)
===================

**Extend Framework edi_base**

Allow to configure integrations to use an intermediate table (EDI Table) to synchronize your data.

This new feature can be activated via a new boolean **"Use EDI Table"** on the integration configuration.

Once the boolean **"Use EDI Table"** is checked, the integration is decomposed into 2 steps.

--------------
In Flows Steps
--------------

- Step 1 (Update EDI Table): Prepare the input to create/update EDI Table records.
- Step 2 (Process EDI Table): Process the EDI Table records to create/update/delete records in your Odoo database.

---------------
Out Flows Steps
---------------

- Step 1: Prepare the input to create/update EDI Table records.
- Step 2: Process the EDI Table records to synchronize with the provider.

EDI Table
=========

The original integration CRON will be used to execute the first step (update).

A new CRON is automatically created to execute the second step (process).

Both CRONs cannot be executed at the same time to avoid concurrent updates.

The EDI Table is modeled by a new model **edi.table.record**:

- identifier: unique identifier (optional)

- content: content to process in the second step

- state:

    - new: the record is created by the first step

    - warning: an error occurred during the second step (processing) but the record can still be processed (e.g: concurrent update)

    - fail: an error occurred during the second step (processing) and the record cannot be processed anymore (e.g: missing mandatory field)

    - done: the record is processed successfully by the second step

    - cancel: the record is cancelled by the user and cannot be processed anymore


Those records are created by step 1 and processed by step 2.

When created, the state is set to "new".

When processed, the state is set to "warning", "fail" or "done" depending on the result.


When boolean **"Match Identifier"** is checked, the integration step 1 will try to match an existing EDI Table record with the same identifier.

If a match is found, the content of the EDI Table record is updated with the new content.

If no match is found, a new EDI Table record is created.

When boolean "Match Identifier" is not checked, a new EDI Table record is always created.


Requirements
============

This module requires new functions to be defined on the model edi.integration in order to work properly with the EDI Table.

The following functions are the minimum ones to be defined (other functions can be defined to handle success or error cases):

----------------
In Flows Methods
----------------

**Step 1 (Update EDI Table):**

::

    def _prepare_in_edi_table(self, data):
        """Allow the integration to redefine the first step (conversion of data into edi.table.record)

        To implement in each integration
        ....

        :param data: list of dict
            each dict contains key
            - filename: str
            - content: str
        :return: list of dict (vals to create or update edi.table.record)
        e.g. [{'identifier': 'an identifier', 'content': 'a content', 'filename': 'a filename'}, ...]

        Can use self._report_error
        """
        return []

**Step 2 (Process EDI Table):**

::

    def _process_in_edi_table(self, data):
        """Allow the integration to redefine the second step (process edi.table.record)

        To implement in each integration
        ....

        :param data: list of dict
            each dict contains key
            - filename: str
            - content: str
            - edi_table_record: record edi.table.record
        :return: status use by _clean

        Can use self._report_error
        """
        return 'done'

-----------------
Out Flows Methods
-----------------

**Step 1 (Update EDI Table):**

::

    def _prepare_out_edi_table(self, records):
        """Allow the integration to redefine the first step (conversion of records into edi.table.record)

        To implement in each integration
        ....

        :param records: recordset
        :return: list of dict (vals to create or update edi.table.record)
        e.g. [{'identifier': 'an identifier', 'content': 'a content'}, ...]

        Can use self._report_error
        """
        return []

**Step 2 (Process EDI Table):**

::

    def _process_out_edi_table(self, records):
        """Allow the integration to redefine the second step (processing of a edi.table.record)

        To implement in each integration
        ....

        :param data: recordset edi.table.record
        :return: str

        Can use self._report_error
        """
        return ''
