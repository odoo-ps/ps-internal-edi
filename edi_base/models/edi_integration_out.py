# Part of Odoo. See LICENSE file for full copyright and licensing details.
import ast
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class IntegrationOut(models.Model):
    """Implementation of process out
    _get_record to send #DEFAULT
    _prepare_data_for_sync (divide recorset into smaller recordset based on synchronization_creation field)

    for each recordset (sync)
        try:
            _get_synchronization_name_out: #DEFAULT
            _get_content  #TO IMPLEMENT
            _send_content  #DEFAULT
            _postprocess #DEFAULT
        except:
            _handle_error  #DEFAULT
    """

    _inherit = "edi.integration"

    def _create_synchronization_out(self, records):
        """
        :param records: recordset
        :return: edi.synchronization
        """
        self.ensure_one()

        name = self._get_synchronization_name_out(records)
        return self.env["edi.synchronization"].create(
            {
                "integration_id": self.id,
                "name": name,
                "filename": "%s.%s" % (name[:100], self.synchronization_content_type),
                "synchronization_date": fields.Datetime.now(),
            }
        )

    def _process_out(self, records):
        """Process the given records for out flow (with the current synchronization)

        :param records: recordset
        """
        self.ensure_one()

        content = False
        try:
            # all operations must be executed in the same savepoint
            # because they should be atomic
            with self.env.cr.savepoint():
                self.env.activity = "Get Content"
                content = self._get_content(records)
                if self.write_content_on_sync:
                    self.env.sync._write_content(content)

                self.env.activity = "Send Synchro"
                res = self._send_content(content, records)

                self.env.activity = "Postprocess"
                self._postprocess(res, content, records)

                # at the exit, the savepoint will flush (force to reveal concurrent updates)
                # thus, no need of explicit flush

        except Exception:
            if content:
                # force the write of the content on the synchronization
                if self.write_content_on_sync:
                    self.env.sync._write_content(content)

            raise

    ##################################################
    # Default Behavior: Probably need to reimplement #
    ##################################################

    def _get_synchronization_name_out(self, records):
        """Return the name of the synchronization (out flow)

        To implement in each integration
        if not self.type == 'My type':
            return super()._get_synchronization_name_out(records)
        ....

        :param records: recordset
        :return: str
        """
        self.ensure_one()
        return "%s - %s: %s" % (self.name, fields.Datetime.now(), records.ids)

    def _get_record_to_send(self):
        """Return the records that should be synchronized

        To implement in each integration
        if not self.type == 'My type':
            return super()._get_record_to_send()
        ....

        :return: recordset to synchronize (use to generate the content)
        """
        self.ensure_one()

        if self.record_filter_id:
            domain = ast.literal_eval(self.record_filter_id.domain)
            return self.env[self.record_filter_id.model_id].search(domain)
        return self.browse()

    def _send_content(self, content, records):
        """
        Standard behavior can be overwrite if needed

        Can use self._report_error
        Filename can be accessed by self.env.sync.filename

        To implement in each integration
        if not self.type == 'My type':
            return super()._send_content(content, records)
        ....

        :param content: str
        :param records: recordset
        :return: any (return of self.connection_id._send_synchronization)
        """
        self.ensure_one()

        res = self.connection_id._send_synchronization(self.env.sync.filename, content)
        self._clean_synchronization(records, "done")
        return res

    def _clean_out_sync(self, records, status):
        """
        :param records: recordset
        :param status: str
        """
        self.ensure_one()
        self.connection_id._clean_synchronization_out(self.env.sync.filename, status)

    def _postprocess(self, send_result, content, records):
        """
        Standard behavior can be overwrite if needed
        Called at the end of each synchronization
        By default, do nothing

        Filename can be accessed by self.env.sync.filename

        To implement in each integration
        if not self.type == 'My type':
            return super()._postprocess(send_result, content, records)
        ....

        :param send_result: any (value returned by self.connection_id._send_synchronization)
        :param content: str
        :param records: recordset
        """
        self.ensure_one()
        return

    ################################
    # To implement for process out #
    ################################

    def _get_content(self, records):
        """Return the content that should be sent

        To implement in each integration
        if not self.type == 'My type':
            return super()._get_content(records)
        ....

        Can use self._report_error

        :param records: recordset
        :return: str
        """
        self.ensure_one()
        return ""
