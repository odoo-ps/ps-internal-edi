from odoo import models, fields


class IntegrationIn(models.Model):
    _inherit = "edi.integration"

    # -------------------------------------------------------------------------
    # Logics IN Flows
    # -------------------------------------------------------------------------

    ###########################################
    #             Generic API                 #
    ###########################################
    # ========================================#

    def _process_in_data(self, data):
        """Process the given data for in flow (with the current synchronization)

        Extended to handle edi.table.record if use_edi_table is True
        Check if we should process:
        - the first step (update edi.table.record)
        or
        - the second step (process edi.table.record)

        :param data: list of dict
        :return: status use by _clean
        """
        self.ensure_one()
        if self.use_edi_table:
            if self._should_process_edi_table():
                self.env.cr.activity = "Process EDI 2-steps queue (step 2)"
                return self._process_in_edi_table(data)

            self.env.cr.activity = "Update EDI 2-steps queue (step 1)"
            self._update_edi_table(data)
            return "done"

        return super()._process_in_data(data)

    def _clean_in_sync(self, data, status):
        """
        :param data: list of dict
        :param status: str
        """
        self.ensure_one()
        if self._should_process_edi_table():
            # if step 2, do not clean sync
            return

        super()._clean_in_sync(data, status)

    #############################################
    # Generic API for creating edi.table.record #
    #############################################
    # ==========================================#

    def _prepare_in_default_edi_table_vals(self, data):
        """Prepare default vals for edi.table.record

        :param data: list of dict
        :return: dict
        """
        self.ensure_one()
        return {
            "filename": " ".join([d.get("filename", "") for d in data]),
            "name": self._get_edi_table_record_name_in(data),
        }

    def _get_edi_table_record_name_in(self, data):
        """Return the name of the table record (in flow)

        To implement in each integration
        ....

        :param data: list of dict
        :return: str
        """
        self.ensure_one()
        return f"{self.name} - {fields.Datetime.now()}: {' '.join([d.get('filename') for d in data])}"

    ###############################################
    # Generic API for processing edi.table.record #
    ###############################################
    # ============================================#

    def _get_in_data(self):
        """Return the data to process for in flow

        Extended to get edi.table.record if should process EDI 2-steps

        :return: list of dict
            the dict should be {
                'filename': FILENAME (str),
                'content': str
                    will be handle by in edi.integration._process_content
                    and will be write on the synchronization
            }
        """
        self.ensure_one()

        if self._should_process_edi_table():
            return self._get_in_content_edi_table()

        return super()._get_in_data()

    def _get_in_content_edi_table(self):
        """Return the data needed by _process_in_edi_table to process edi.table.record (step 2)

        :return: list of dict
            each dict contains key
            - filename: str
            - content: str
            - edi_table_record: record edi.table.record
        """
        self.ensure_one()
        edi_table_records = self._get_edi_table_record_to_process()
        return self._convert_edi_table_records_to_data(edi_table_records)

    def _handle_error_edi_table_in(self, data, exc):
        """Handle error when processing edi.table.record (second step)

        :param data: list of dict
            each dict contains key
            - filename: str
            - content: str
            - edi_table_record: record edi.table.record
        :param exc: exception
        """
        self.ensure_one()
        return

    def _handle_success_edi_table_in(self, data):
        """Handle success for edi.table.record (second step)

        :param data: list of dict
            each dict contains key
            - filename: str
            - content: str
            - edi_table_record: record edi.table.record
        """
        self.ensure_one()
        return

    ################################
    # To implement for process in  #
    ################################

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
        return "done"
