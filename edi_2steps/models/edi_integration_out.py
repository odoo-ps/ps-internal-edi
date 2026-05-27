from datetime import datetime, timezone

from odoo import models


class IntegrationOut(models.Model):
    _inherit = "edi.integration"

    # -------------------------------------------------------------------------
    # Logics OUT Flows
    # -------------------------------------------------------------------------

    ###########################################
    #             Generic API                 #
    ###########################################
    # ========================================#

    def _process_out_data(self, records):
        """Process the given data for out flow

        Extended to handle edi.table.record if use_edi_table is True
        Check if we should process:
        - the first step (update edi.table.record)
        or
        - the second step (process edi.table.record)

        :param data: recordset
        :return: str
        """
        # OVERRIDE
        self.ensure_one()
        if self.use_edi_table:

            if self._should_process_edi_table():
                self.env.cr.activity = "Process EDI 2-steps queue (step 2)"
                content = self._process_out_edi_table(records)

                self.env.cr.activity = "Send Synchro (step 2)"
                res = self._send_content(content, records)

                self.env.cr.activity = "Postprocess (step 2)"
                self._postprocess_edi_table(res, content, records)
                return content

            self.env.cr.activity = "Update EDI 2-steps queue (step 1)"
            edi_table_records = self._update_edi_table(records)
            content = "\n".join(edi_table_records.mapped("content"))

            self.env.cr.activity = "Postprocess (step 1)"
            self._postprocess(False, content, records)
            return content

        return super()._process_out_data(records)

    def _clean_out_sync(self, records, status):
        """
        :param records: recordset
        :param status: str
        """
        # OVERRIDE
        self.ensure_one()
        if self.use_edi_table and not self._should_process_edi_table():
            # if step 1, do not clean sync
            return

        super()._clean_out_sync(records, status)

    #############################################
    # Generic API for creating edi.table.record #
    #############################################
    # ==========================================#

    def _prepare_out_default_edi_table_vals(self, records):
        """Prepare default vals for edi.table.record for out flows

        :param records: recordset
        :return: dict
        """
        self.ensure_one()
        return {"name": self._get_table_record_name_out(records)}

    def _get_table_record_name_out(self, records):
        """Return the name of the table record (out flow)

        To implement in each integration
        ....

        :param records: recordset
        :return: str
        """
        self.ensure_one()
        now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-utc")
        record_info = self._get_synchronization_name_out_record_info(records)
        return self.env["ir.http"]._slugify(
            f"{self.name}-{now}" + (record_info and f"-{record_info}" or ""), max_length=200
        )

    ###############################################
    # Generic API for processing edi.table.record #
    ###############################################
    # ============================================#

    def _get_out_data(self):
        """Return the data to process for out flow

        Extended to get edi.table.record if should process EDI 2-steps

        :return: recordset to synchronize (use to generate the content)
        """
        # OVERRIDE
        self.ensure_one()

        if self._should_process_edi_table():
            return self._get_record_to_send_edi_table()

        return super()._get_out_data()

    def _get_record_to_send_edi_table(self):
        """Return the data needed by _process_out_edi_table to process edi.table.record (step 2)

        :return: recordset edi.table.record
        """
        self.ensure_one()
        return self._get_edi_table_record_to_process()

    def _handle_error_edi_table_out(self, records, exc):
        """Handle error when processing edi.table.record (second step)

        :param records: records edi.table.record
        :param exc: exception
        """
        self.ensure_one()
        return

    def _handle_success_edi_table_out(self, records):
        """Handle success for edi.table.record (second step)

        :param records: recordset edi.table.record
        """
        self.ensure_one()
        records.write({"filename": self.env.cr.sync.filename})

    def _postprocess_edi_table(self, send_result, content, records):
        """
        Standard behavior can be overwrite if needed
        Called at the end of each synchronization
        By default, do nothing

        Filename can be accessed by self.env.cr.sync.filename

        To implement in each integration
        ....

        :param send_result: any (value returned by self.connection_id._send_synchronization)
        :param content: str
        :param records: recordset edi.table.record
        """
        self.ensure_one()
        return

    #################################
    # To implement for process out  #
    #################################

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

    def _process_out_edi_table(self, records):
        """Allow the integration to redefine the second step (processing of a edi.table.record)

        To implement in each integration
        ....

        :param data: recordset edi.table.record
        :return: str

        Can use self._report_error
        """
        return ""
