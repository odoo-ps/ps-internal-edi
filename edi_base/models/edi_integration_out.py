# Part of Odoo. See LICENSE file for full copyright and licensing details.
import ast
import logging
from datetime import datetime, timezone

from odoo import fields, models


_logger = logging.getLogger(__name__)


class IntegrationOut(models.Model):
    """Implementation of process out

    _get_record_to_send         #DEFAULT
    _prepare_data_for_sync      (divide recordset into smaller recordsets based on synchronization_creation)

    for each recordset (sync):
        try:
            _get_synchronization_name_out  #DEFAULT
            _get_content                   #TO IMPLEMENT
            _send_content                  #DEFAULT — dispatches to API or FTP, do not override
            _postprocess                   #DEFAULT
        except:
            _handle_error                  #DEFAULT
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
                content = self._process_out_data(records)
                # at the exit, the savepoint will flush (force to reveal concurrent updates)
                # thus, no need of explicit flush
        except Exception:
            raise
        finally:
            if content:
                # force the write of the content on the synchronization
                if self.store_sent_content:
                    self.env.cr.sync._write_sent(content)

    def _get_out_data(self):
        """Return the data to process for out flow

        :return: recordset to synchronize (use to generate the content)
        """
        self.ensure_one()
        return self._get_record_to_send()

    def _get_out_content(self, data):
        """Process the given data for in flow

        :param records: recordset
        :return: str
        """
        self.ensure_one()
        return self._get_content(data)

    def _process_out_data(self, records):
        """Process the given records for out flow

        :param records: recordset
        :return: str
        """
        self.env.cr.activity = "Get Content"
        content = self._get_out_content(records)

        self.env.cr.activity = "Send Synchro"
        res = self._send_content(content, records)

        self.env.cr.activity = "Postprocess"
        self._postprocess(res, content, records)
        return content

    ##################################################
    # Default Behavior: Probably need to reimplement #
    ##################################################

    def _get_synchronization_name_out(self, records):
        """Return the name of the synchronization (out flow)

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

    def _get_synchronization_name_out_record_info(self, records):
        """Optional info to add to generated filenames to represent the current records.

        Purpose is to try to ensure some traceability between a synchronization & a record,
        to be able to find the record back.

        By default, records IDS is provided, but it could also be :
        - record names or any meaningful field
        - or nothing, if not needed
        - or only if synchronization_creation == 1 for ex.
          (especially since we limit the length of the filename, so part of the info could be missing)

        :return: list | str
        """
        return records.ids

    def _get_record_to_send(self):
        """Return the records that should be synchronized

        To implement in each integration
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
        Filename can be accessed by self.env.cr.sync.filename

        To implement in each integration
        ....

        :param content: str
        :param records: recordset
        :return: any (return of self.connection_id._send_synchronization or _call)
        """
        self.ensure_one()

        if self.api_endpoint_id:
            res = self._api_call(self._build_out_payload(content))
            if self.store_received_content:
                self.env.cr.sync._write_received(res)
        else:
            res = self.connection_id._send_synchronization(self.env.cr.sync.filename, content)
        self._clean_synchronization(records, "done")
        return res

    def _clean_out_sync(self, records, status):
        """
        :param records: recordset
        :param status: str
        """
        self.ensure_one()
        self.connection_id._clean_synchronization_out(self.env.cr.sync.filename, status)

    def _postprocess(self, response, content, records):
        """
        Standard behavior can be overwrite if needed
        Called at the end of each synchronization
        By default, do nothing

        Filename can be accessed by self.env.cr.sync.filename

        To implement in each integration
        ....

        :param response: any (value returned by self.connection_id._send_synchronization)
        :param content: str
        :param records: recordset
        """
        self.ensure_one()
        return

    def _build_out_payload(self, content):
        """Transform content before sending to the API (OUT flow).

        Override to wrap the content in a JSON envelope or apply any
        pre-send transformation.

        To implement in each integration
        if not self.type == 'My type':
            return super()._build_out_payload(content)
        ....

        :param content: str — content produced by _get_content
        :return: str | dict | list — payload passed to _api_call
        """
        self.ensure_one()
        return content

    ################################
    # To implement for process out #
    ################################

    def _get_content(self, records):
        """Return the content that should be sent

        To implement in each integration
        ....

        Can use self._report_error

        :param records: recordset
        :return: str
        """
        self.ensure_one()
        return ""
