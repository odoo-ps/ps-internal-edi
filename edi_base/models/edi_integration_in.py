# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging
import xml.dom.minidom

from odoo import fields, models


_logger = logging.getLogger(__name__)


class IntegrationIn(models.Model):
    """Implementation of process in

    _get_in_content             #DEFAULT — do not override
      └── _build_in_payload     #OPTIONAL — query params sent to the API
      └── _api_wrap_response    #OPTIONAL — splits raw response into [{filename, content}, ...]
    _prepare_data_for_sync      batches items into synchronization groups (driven by synchronization_creation)

    for each group (sync):
        try:
            _get_synchronization_name_in  #DEFAULT
            _process_content              #TO IMPLEMENT
            _clean                        #DEFAULT
        except:
            _handle_error                 #DEFAULT
    """

    _inherit = "edi.integration"

    def _create_synchronization_in(self, data):
        """
        :param data: list of dict
        :return: edi.synchronization
        """
        self.ensure_one()

        vals = {
            "integration_id": self.id,
            "name": self._get_synchronization_name_in(data),
            "filename": " ".join([d.get("filename") for d in data]),
            "synchronization_date": fields.Datetime.now(),
        }

        if self.store_received_content:
            vals["received_content"] = "\n\n".join([d.get("content") or "" for d in data])

        sent_parts = [d.get("sent_content") for d in data if d.get("sent_content")]
        if sent_parts and self.store_sent_content:
            vals["sent_content"] = sent_parts[0]

        return self.env["edi.synchronization"].create(vals)

    def _process_in(self, data):
        """Process the given data for in flow (with the current synchronization)

        :param data: list of dict
        """
        self.ensure_one()

        # all operations must be executed in the same savepoint
        # because they should be atomic
        with self.env.cr.savepoint():
            self.env.cr.activity = "Process Content"
            status = self._process_in_data(data)

            # flush before calling _clean, because concurrent updates are revealed with the flush
            # if an update has been applied on a locked record, the flush will wait until the lock is released
            # when it is released, the concurrent update exception is revealed
            # we don't want to call the _clean if a concurrent update happened
            self.env.cr.activity = "Flush Content"
            self.env.flush_all()

            self.env.cr.activity = "Clean Synchro"
            self._clean(data, status)

            # at the exit, the savepoint will still flush (force to reveal concurrent updates)
            # thus, no need of explicit flush

    def _get_in_data(self):
        """Return the data to process for in flow

        :return: list of dict
            the dict should be {
                'filename': FILENAME (str),
                'content': str
                    will be handle by in edi.integration._process_content
                    and will be write on the synchronization
            }
        """
        self.ensure_one()
        return self._get_in_content()

    def _process_in_data(self, data):
        """Process the given data for in flow

        :param data: list of dict
        :return: status use by _clean
        """
        self.ensure_one()
        return self._process_content(data)

    ##################################################
    # Default Behavior: Probably need to reimplement #
    ##################################################

    def _get_synchronization_name_in(self, data):
        """Return the name of the synchronization (in flow)

        To implement in each integration
        ....

        :param data: list of dict
        :return: str
        """
        self.ensure_one()
        return "%s - %s: %s" % (self.name, fields.Datetime.now(), " ".join([d.get("filename") for d in data]))

    def _get_in_content(self):
        """Return the data to process

        Can be overrided if needed

        To implement in each integration
        ....

        :return: list of dict
            the dict should be {
                'filename': FILENAME (str),
                'content': str
                    will be handle by in edi.integration._process_content
                    and will be write on the synchronization
            }
        """
        self.ensure_one()
        if self.api_endpoint_id:
            payload = self._build_in_payload()
            raw_text = self._api_call(payload)
            items = self._api_wrap_response(raw_text)
            if payload:
                payload_str = json.dumps(payload, default=str, indent=2)
                for item in items:
                    item.setdefault("sent_content", payload_str)
            return items
        return self.connection_id._fetch_synchronizations()

    def _build_in_payload(self):
        """Return the query payload to pass to the API call (IN flow).

        Override to add query parameters, filters, pagination, etc.

        :return: dict | str | False
        """
        self.ensure_one()
        return False

    def _api_wrap_response(self, raw_text):
        """Convert raw API response text into the standard IN data format.

        Override to split a single response into multiple items (one per synchronization)
        or to customize filename extraction.

        Default: wraps the entire response as a single item, using the endpoint name
        as the filename. JSON responses are pretty-printed when response_content_type='json'.

        :param raw_text: str — raw response text returned by _api_call
        :return: list of dict with 'filename' and 'content' keys
        """
        self.ensure_one()
        content = raw_text or ""
        if self.response_content_type == "json":
            try:
                content = json.dumps(json.loads(raw_text), indent=2)
            except (json.JSONDecodeError, TypeError):
                pass
        elif self.response_content_type == "xml":
            try:
                content = xml.dom.minidom.parseString(raw_text).toprettyxml(indent="  ")
            except Exception:
                pass
        return [{"filename": self.api_endpoint_id.name, "content": content}]

    def _clean(self, data, status):
        """Called after the processing of each synchronization

        To implement in each integration
        ....

        :param data: list of dict
        :param status: str (status returned by _process_content)
        """
        self.ensure_one()
        self._clean_synchronization(data, status)

    def _clean_in_sync(self, data, status):
        """
        :param data: list of dict
        :param status: str
        """
        self.ensure_one()
        for d in data:
            self.connection_id._clean_synchronization_in(d, status)

    ################################
    # To implement for process in  #
    ################################
    def _process_content(self, data):
        """Allow the integration to redefine the processing of the content

        To implement in each integration
        ....


        :param data: list of dict
            each dict contains key
            - filename
            - content
        :return: status use by _clean

        Can use self._report_error
        """
        self.ensure_one()
        return "done"
