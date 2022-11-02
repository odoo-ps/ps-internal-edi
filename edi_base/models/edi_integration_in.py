# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class IntegrationIn(models.Model):
    """Implementation of process in

    _get_in_content  #DEFAULT
    _prepare_data_for_sync (divide list of data into smaller list of data based on synchronization_creation field)

    for each list of data (sync)
        try:
            _get_synchronization_name_in: #DEFAULT
            _process_content  #TO IMPLEMENT
            _clean   #DEFAULT
        except:
            _handle_error  #DEFAULT
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

        if self.write_content_on_sync:
            vals["content"] = "\n\n".join([d.get("content", "") for d in data])

        return self.env["edi.synchronization"].create(vals)

    def _process_in(self, data):
        """Process the given data for in flow (with the current synchronization)

        :param data: list of dict
        """
        self.ensure_one()

        # all operations must be executed in the same savepoint
        # because they should be atomic
        with self.env.cr.savepoint():
            self.env.activity = "Process Content"
            status = self._process_content(data)

            # flush before calling _clean, because concurrent updates are revealed with the flush
            # if an update has been applied on a locked record, the flush will wait until the lock is released
            # when it is released, the concurrent update exception is revealed
            # we don't want to call the _clean if a concurrent update happened
            self.env.activity = "Flush Content"
            self.env.flush_all()

            self.env.activity = "Clean Synchro"
            self._clean(data, status)

            # at the exit, the savepoint will still flush (force to reveal concurrent updates)
            # thus, no need of explicit flush

    ##################################################
    # Default Behavior: Probably need to reimplement #
    ##################################################

    def _get_synchronization_name_in(self, data):
        """Return the name of the synchronization (in flow)

        To implement in each integration
        if not self.type == 'My type':
            return super()._get_synchronization_name_in(data)
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
        if not self.type == 'My type':
            return super()._get_in_content()
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
        return self.connection_id._fetch_synchronizations()

    def _clean(self, data, status):
        """Called after the processing of each synchronization

        To implement in each integration
        if not self.type == 'My type':
            return super()._clean(data, status)
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
        if not self.type == 'My type':
            return super()._process_content(data)
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

    ##########################################################
    # Common default Behavior: Probably need to reimplement  #
    ##########################################################
    # ========================================================#

    def _handle_error(self, data, exc):
        """Can be use to handle an error at the end of each synchronization

        To implement in each integration
        if not self.type == 'My type':
            return super()._handle_error(data, exc)
        ....

        :param data:
            - in: list of dict
            - out: recordset
        :param: exception
        """
        self.ensure_one()
        self._clean_synchronization(data, "error")
