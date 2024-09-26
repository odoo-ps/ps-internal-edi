# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class Connection(models.Model):
    """
    Object used by the integration performing the gateway between odoo and the third party component
    """

    _name = "edi.connection"
    _description = "EDI Connection"

    name = fields.Char(required=True)
    type = fields.Selection(selection=[], required=True, string="EDI Type")
    configuration = fields.Text()
    company_id = fields.Many2one("res.company")
    integration_ids = fields.One2many("edi.integration", "connection_id", readonly=True, context={"active_test": False})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)

        for rec in records:
            # set-default config if not given
            rec._set_default_configuration()

        return records

    def reset_config(self):
        self.ensure_one()
        self.configuration = json.dumps(self._get_default_configuration(), indent=4, sort_keys=True)

    def test(self):
        """
        Test the connection is successful with the third party component

        Should raise an exception with Success or Failed message

        To implement in each connection
        if not self.type == 'My type':
            return super().test()
        ....
        """
        raise NotImplementedError("No test method implemented for this type of connection")

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """
        Send the content to the third party component (out flows)

        To implement in each connection
        if not self.type == 'My type':
            return super()._send_synchronization(filename, content, *args, **kwargs)
        ....

        :param filename: str
        :param content: str
        """
        raise NotImplementedError("No send_synchronization method implemented for this type of connection")

    def _fetch_synchronizations(self, *args, **kwargs):
        """Fetch the content to process (in flows)

        To implement in each connection
        if not self.type == 'My type':
            return super()._fetch_synchronizations(*args, **kwargs)
        ....

        :return: list of dict
            the dict should be {
                'filename': FILENAME (str),
                'content': str or dict: will be handled by in edi.integration._process_content & will be written
                in edi.synchronization.content field
            }
        """
        raise NotImplementedError("No fetch_synchronizations method implemented for this type of connection")

    def _clean_synchronization_in(self, data, status, *args, **kwargs):
        """Clean the synchronization (in flows)

        To implement in each connection
        if not self.type == 'My type':
            return super()._clean_synchronization_in(data, status, *args, **kwargs)
        ....

        :param data: dict (returned from _fetch_synchronizations)
        :param status: str
            - done if everything went well
            - error if there is something that went wrong

        Default behavior: Do Nothing
        """
        return

    def _clean_synchronization_out(self, filename, status, *args, **kwargs):
        """Clean the synchronization (out flows)

        To implement in each connection
        if not self.type == 'My type':
            return super()._clean_synchronization_out(filename, status, *args, **kwargs)
        ....

        :param filename: str
        :param status: str
            - done if everything went well
            - error if there is something that went wrong

        Default behavior: Do Nothing
        """
        return

    def _get_default_configuration(self):
        """
        Return a dictionnary with the template configuration for
        this type of connection

        To implement in each connection
        self.ensure_one()
        if not self.type == 'My type':
            return super()._get_default_configuration()
        ....

        :return: dict
        """
        self.ensure_one()
        return {}

    ###################################
    #    End of abstract interface    #
    #  don't override these methods   #
    ###################################

    @api.onchange("type")
    def _set_default_configuration(self):
        self.ensure_one()
        if not self.configuration or self.configuration == "{}":
            self.configuration = json.dumps(self._get_default_configuration(), indent=4, sort_keys=True)

    def _read_configuration(self):
        """
        :return: dict
        """
        self.ensure_one()
        return json.loads(self.configuration)


class ConnectionApi(models.Model):

    _inherit = "edi.connection"
    _description = "EDI Connection"

    type = fields.Selection(selection_add=[("api", "Rpc Api")], ondelete={"api": "cascade"})

    def test(self):
        self.ensure_one()
        if not self.type == "api":
            return super().test()

        raise UserError(_("Not applicable for this type of connection"))
