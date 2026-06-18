# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging

from odoo import _, api, fields, models, tools
from odoo.api import NewId
from odoo.exceptions import UserError, ValidationError
from odoo.modules.registry import Registry
from odoo.tools.safe_eval import safe_eval

from ..tools.util import _chunks


_logger = logging.getLogger(__name__)

_CONTENT_TYPE_MAP = {
    "json": "application/json",
    "xml": "application/xml",
    "csv": "text/csv",
    "text": "text/plain",
}


class ProcessIntegrationException(Exception):
    def __init__(self, name, value=None):
        self.name = name
        self.value = value
        self.args = (name, value)


class Integration(models.Model):
    """
    Object modeling an integration and playing the role of orchestrator
    between the edi.connection, data to be synchronized and the edi.synchronization

    Common methods for in/out flows manipulate the data to be synchronized
    independently of the integration flow type (in/out):

        Data has a different meaning based on the integration flow type:
        - in: list of dict (required key 'filename' & 'content')
              'content' will be write on the edi.synchronization
        - out: recordset

    Data processing and synchronization status are performed on the same cursor
    and committed at the same time (to be fully consistent)
        By default:
            - outside unittest: a new cursor is created and a commit is performed between each synchronization
            - inside unittest: the current cursor is used (no cursor created) and no commits is performed
    """

    _name = "edi.integration"
    _description = "Integration to process by Odoo instance"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _inherits = {"ir.cron": "cron_id"}
    _order = "sequence"

    # Common for in/out flows
    company_id = fields.Many2one("res.company", tracking=True)
    integration_flow = fields.Selection(
        [
            ("in", "From provider to Odoo"),
            ("out", "From Odoo to provider"),
            ("out_real", "From Odoo to provider (Realtime)"),
        ],
        required=True,
        string="Flow of data",
        tracking=True,
    )
    integration_flow_type = fields.Selection(
        [("in", "In"), ("out", "Out"), ("unknown", "Unknown")],
        compute="_compute_integration_flow_type",
        string="Type of flow of data",
    )
    synchronization_creation = fields.Integer(
        help="""Number of data to process by synchronization.\n"""
        """1: one data by synchronization (one)\n"""
        """0: all data inside the same synchronization (multi)\n"""
        """n: max n data in the same synchronization""",
        required=True,
        default=1,
        tracking=True,
    )
    connection_id = fields.Many2one("edi.connection", required=True, string="Connection", tracking=True)
    api_auth_type = fields.Selection(related="connection_id.api_auth_type")
    connection_type = fields.Selection(related="connection_id.type")
    api_endpoint_id = fields.Many2one(
        "edi.endpoint",
        ondelete="restrict",
        domain="[('connection_id', '=', connection_id), ('role', '=', 'resource')]",
        help="Optional resource endpoint for this integration. Must belong to the selected connection.",
        tracking=True,
    )
    type = fields.Selection(
        # TODO probably add unicity constraint ?
        selection=[("multi", "Call Sub Integration"), ("api", "RPC Api")],
        required=True,
        string="Unique Type",
        tracking=True,
    )  # Add selection for your integration
    parameter = fields.Text(string="Parameter", tracking=True)

    synchronization_content_type = fields.Selection(
        selection=[("text", "Text"), ("csv", "CSV"), ("xml", "XML"), ("json", "JSON"), ("pdf", "PDF")],
        default="text",
        required=True,
        string="Data Format",
        tracking=True,
    )
    store_received_content = fields.Boolean(
        string="Store Received Content",
        default=True,
        help="Store what was received from the external system on each synchronization record "
        "(IN: data received, OUT: API response).",
        tracking=True,
    )
    store_sent_content = fields.Boolean(
        string="Store Sent Content",
        default=True,
        help="Store what was sent to the external system on each synchronization record "
        "(OUT: payload sent, IN: query payload).",
        tracking=True,
    )
    response_content_type = fields.Selection(
        selection=[("text", "Text"), ("csv", "CSV"), ("xml", "XML"), ("json", "JSON")],
        string="Response Content Type",
        tracking=True,
        help="Format of the API response (IN: data received, OUT: acknowledgement). "
        "Used for pretty-printing in synchronization records.",
    )

    # Cron inheritance
    cron_id = fields.Many2one("ir.cron", ondelete="restrict", required=True, string="Cron Job")
    active = fields.Boolean(related="cron_id.active", readonly=False, tracking=True)
    interval_number = fields.Integer(related="cron_id.interval_number", readonly=False, tracking=True)
    interval_type = fields.Selection(related="cron_id.interval_type", readonly=False, tracking=True)
    user_id = fields.Many2one(related="cron_id.user_id", readonly=False, tracking=True)

    # Multiple Integration at once
    has_sub_integration = fields.Boolean(
        string="Has sub Integration",
        default=False,
        help="If you need to run many integration in a specific order in the same CRON execution",
        tracking=True,
    )
    sequence = fields.Integer()
    sub_integration_ids = fields.Many2many(
        "edi.integration",
        "edi_integration_sub_integration_rel",
        "integration_id",
        "sub_integration_id",
        domain=[("has_sub_integration", "!=", True)],
        context={"active_test": False},
    )
    record_filter_id = fields.Many2one(
        "ir.filters",
        string="Record Filter",
        ondelete="restrict",
        help="Filter for default behavior of _get_record_to_send",
        tracking=True,
    )

    # Status
    synchronization_ids = fields.One2many("edi.synchronization", "integration_id")
    error_ids = fields.One2many("edi.synchronization.error", "integration_id")
    synchronization_count = fields.Integer(compute="_compute_synchronization_count")
    execution_count = fields.Integer(compute="_compute_execution_count")
    last_execution_date = fields.Datetime(
        string="Last triggered Date",
        readonly=True,
        copy=False,
        help="Last time the integration has been triggered",
    )
    last_success_date = fields.Datetime(readonly=True, copy=False)
    last_failure_date = fields.Datetime(readonly=True, copy=False)
    last_state = fields.Selection(
        [
            ("no_sync", "No Sync Yet"),
            ("new", "Started"),
            ("done", "Done"),
            ("fail", "Fail"),
            ("cancelled", "Cancelled"),
        ],
        default="no_sync",
        readonly=True,
        copy=False,
    )

    @api.depends("integration_flow")
    def _compute_integration_flow_type(self):
        for integration in self:
            if integration.integration_flow in integration._get_in_flow_type():
                flow_type = "in"
            elif integration.integration_flow in integration._get_out_flow_type():
                flow_type = "out"
            else:
                flow_type = "unknown"
            integration.integration_flow_type = flow_type

    @api.depends("synchronization_ids")
    def _compute_synchronization_count(self):
        for rec in self:
            rec.synchronization_count = len(rec.synchronization_ids)

    @api.depends("synchronization_ids")
    def _compute_execution_count(self):
        creating_ones = self.filtered(lambda x: isinstance(x.id, NewId))
        if creating_ones:
            creating_ones.execution_count = 0

        existing_ones = self - creating_ones
        if existing_ones:
            self.env.cr.execute(
                """
                SELECT i.id, COUNT(DISTINCT s.triggered_date)
                FROM edi_integration AS i
                LEFT JOIN edi_synchronization AS s ON s.integration_id = i.id
                WHERE i.id in %s
                GROUP BY i.id
                """,
                (tuple(existing_ones.ids),),
            )
            for integration_id, count in self.env.cr.fetchall():
                self.browse(integration_id).execution_count = count

    def _api_call(self, payload=None):
        headers = {}
        # Set Content-Type for OUT flows sending raw string content (JSON/dict payloads
        # already get Content-Type: application/json automatically from requests).
        if self.integration_flow_type == "out" and isinstance(payload, str):
            ct = _CONTENT_TYPE_MAP.get(self.synchronization_content_type)
            if ct:
                headers["Content-Type"] = ct
        return self.connection_id._api_call(
            endpoint=self.api_endpoint_id,
            payload=payload,
            headers=headers or None,
        )

    def _exec_method_based_on_flow(self, in_method, out_method, *args, **kwargs):
        """
        Execute the method based on the flow type
        :in_method: method to execute if the flow is in
        :out_method: method to execute if the flow is out
        :return: result of the method executed
        """
        self.ensure_one()

        if self.integration_flow_type == "in":
            return in_method(*args, **kwargs)
        elif self.integration_flow_type == "out":
            return out_method(*args, **kwargs)
        raise ValidationError(
            _(
                "Invalid integration flow type %s." "\nYou have to specify if this flow type is an 'in' or 'out' flow.",
                self.integration_flow,
            )
        )

    @api.constrains("api_endpoint_id", "connection_id")
    def _check_endpoint_connection(self):
        for rec in self:
            if rec.api_endpoint_id and rec.api_endpoint_id.connection_id != rec.connection_id:
                raise ValidationError(
                    rec.env._(
                        "Endpoint '%s' does not belong to connection '%s'.",
                        rec.api_endpoint_id.name,
                        rec.connection_id.name,
                    )
                )

    @api.onchange("connection_id")
    def _onchange_connection_id_endpoint(self):
        if self.api_endpoint_id and self.api_endpoint_id.connection_id != self.connection_id:
            self.api_endpoint_id = False

    @api.model
    def _get_in_flow_type(self):
        """Return integration_flow of type in
        Method used to perform the integration with the correct behavior
        Can be extended to add new integration_flow of type in
        """
        return ["in"]

    @api.model
    def _get_out_flow_type(self):
        """Return integration_flow of type out
        Method used to perform the integration with the correct behavior
        Can be extended to add new integration_flow of type out
        """
        return ["out", "out_real"]

    @api.model
    def _should_commit(self):
        """
        Check if the integration should commit
        By default:
            - if not in unittest: True
            - if in unittest: False

        Can force commit with the 'autocommit' context key
            - if autocommit=True: True
            - if autocommit=False: False

        :return: True if should commit else False
        """
        if "autocommit" in self.env.context:  # Context key as priority to decide
            return bool(self.env.context.get("autocommit"))
        return not tools.config["test_enable"]

    @api.model
    def _safe_commit(self):
        """Commit if should commit else flush"""
        cursor = self.env.cr
        cursor.commit() if self._should_commit() else cursor.flush()

    @api.model
    def _default_cron_vals(self):
        return {"model_id": self.env.ref("edi_base.model_edi_integration").id, "state": "code"}

    def _set_status(self, synchronizations=None):
        """Set the status of the integration based on the last synchronization.
        Consider the status from the related synchronization which comes :
        - either from parameter
          (which is the case after an exception which can group multiple integrations)
        - or on the cursor itself
          (because we stored a reference to the synchronization there to make it available everywhere)
        """
        for integration in self:
            synchronizations = (
                synchronizations
                and synchronizations.filtered(lambda x, i=integration: x.integration_id == i)
                or hasattr(integration.env.cr, "all_syncs")
                and integration.env.cr.all_syncs
            )
            if not synchronizations:
                _logger.warning(
                    _(
                        "No synchronization related to the integration %s, impossible to set the status",
                        integration.type,
                    )
                )
                continue

            last_synchronizations = synchronizations.sorted("synchronization_date", reverse=True)
            last_success = last_synchronizations.filtered(lambda x: x.state == "done")[:1]
            last_failure = last_synchronizations.filtered(lambda x: x.state == "fail")[:1]
            if last_success:
                integration.last_success_date = last_success.synchronization_date
            if last_failure:
                integration.last_failure_date = last_failure.synchronization_date

            if last_failure:  # globally failed if at least one failed
                integration.last_state = last_failure.state
            else:  # could be "new" or "done"
                integration.last_state = last_synchronizations[:1].state

    @api.model_create_multi
    def create(self, values):
        for vals in values:
            vals.update(self._default_cron_vals())
        integrations = super().create(values)
        for integration, vals in zip(integrations, values, strict=True):
            if "code" in vals:
                continue
            integration.code = "model._process(%i)" % integration.id
        return integrations

    def unlink(self):
        """Remove server action & cron task on cascade."""
        crons = self.cron_id
        actions = crons.ir_actions_server_id
        res = super().unlink()
        crons.unlink()
        actions.unlink()
        return res

    def _load_records(self, data_list, update=False):
        result = super()._load_records(data_list, update=update)
        if self._name != "edi.integration":
            return result

        # NOTE: Since https://github.com/odoo/odoo/commit/47ae24081, a new test
        #       checks that no code that should be counted by the `cloc` utility
        #       in Odoo exists after the installation of the modules.
        #       The heuristic used is to check the existence of an IMD record for
        #       server actions.
        #       In our case (integration -> cron -> server action), the created
        #       server action does not get automatically an external identifier,
        #       since the framework only support one level of inheritance.
        #       Thus, we need to manually create the external identifier for the SA to
        #       avoid the test to fail.

        IMD = self.env["ir.model.data"]
        crons = result.mapped("cron_id")
        server_actions = crons.mapped("ir_actions_server_id")
        server_action_imds = IMD.search([("model", "=", "ir.actions.server"), ("res_id", "in", server_actions.ids)])
        if len(server_action_imds) == len(result):
            return result

        server_action_imds_res_ids = server_action_imds.mapped("res_id")
        cron_imds = IMD.search([("model", "=", "ir.cron"), ("res_id", "in", crons.ids)])
        imd_data_list = []
        for server_action in server_actions:
            if server_action.id in server_action_imds_res_ids:
                continue

            cron = crons.filtered(lambda c, s=server_action: c.ir_actions_server_id == s)
            imd = cron_imds.filtered(lambda imd, c=cron: imd.res_id == c.id)
            imd_data_list.append(
                {"xml_id": f"{imd.module}.{imd.name}_ir_actions_server", "record": server_action, "noupdate": True}
            )

        IMD._update_xmlids(imd_data_list, update=update)
        return result

    def _read_parameter(self):
        """
        :return: dict
        """
        self.ensure_one()
        return json.loads(self.parameter) if self.parameter else {}

    def test_connection(self):
        """Should raise a UserError with status 'Success' or 'Fail'"""
        for integration in self:
            integration.connection_id.test()

    def open_synchronizations(self):
        self.ensure_one()

        action_dict = self.env["ir.actions.actions"]._for_xml_id("edi_base.synchronizations_act_window")
        ctx = safe_eval(action_dict.pop("context", "{}"))
        ctx.update({"default_integration_id": self.id})

        action_dict.update(
            {
                "name": _("%s's synchronizations") % self.name,
                "domain": [("integration_id", "in", [self.id] + self.sub_integration_ids.ids)],
                "context": ctx,
            }
        )

        return action_dict

    ###########################################
    #             Generic API                 #
    ###########################################
    # =========================================#

    def _create_synchronization(self, data):
        """
        :param data:
                - in: list of dict
                - out: recordset
        :return: edi.synchronization
        """
        self.ensure_one()

        sync = self._exec_method_based_on_flow(self._create_synchronization_in, self._create_synchronization_out, data)

        # add the synchronization in the postrollback dict (only for realtime)
        data_cursor = self.env.context.get("edi_data_cursor")
        if data_cursor:
            data_cursor.postrollback.data.setdefault("edi.integration.postrollback.synchronization_ids", []).append(
                sync.id
            )

        return sync

    def _create_error_sync(self, exception):
        """
        :param exception: exception
        :return: edi.synchronization
        """
        self.ensure_one()
        name = "%s - %s: %s" % (self.name, fields.Datetime.now(), "No Sync Error")
        synchronization = self.env["edi.synchronization"].create(
            {
                "integration_id": self.id,
                "name": name,
                "filename": "%s.%s" % (name, self.synchronization_content_type),
                "synchronization_date": fields.Datetime.now(),
            }
        )
        synchronization._report_error(self.env.cr.activity, exception=exception)
        return synchronization

    def _report_error(self, exception=None, message=None):
        """
        Method to use to report error that should not block the process but needs to be reported
        pass exception if you have catch an exception, otherwise pass a message
        If the error should block the process simply raise an error

        :param exception: exception
        :param message: str
        """

        if self.env.cr.sync:
            self.env.cr.sync._report_error(self.env.cr.activity, exception=exception, message=message)
            return

        _logger.error(_("Cannot log error on sync object, sync object is not created yet"))

    @api.model
    def _process(self, integration_id):
        """
        Entry point for cron, don't raise error (no context key edi_raise_error)
        """
        return self.browse(integration_id).process_integration()

    def process_integration(self):
        """
        Default context key edi_raise_error=True if call from button for testing purpose
        """
        raise_error = self.env.context.get("edi_raise_error", False)
        for integration in self:

            # Force to execute with the scheduled user (if we execute it from the interface)
            integration = integration.with_user(integration.sudo().user_id)

            if integration.sub_integration_ids:
                integration.sub_integration_ids.process_integration()
            else:
                if integration.integration_flow == "out_real":
                    _logger.warning(
                        _("Do not call process_integration for real_time integration, call _process_realtime")
                    )
                else:
                    integration._process_in_out(raise_error=raise_error)
        return True

    def _process_in_out(self, data=None, raise_error=False):
        """
        :param data:
            - in: list of dict
            - out: recordset
        :param raise_error: boolean, set True to get the traceback and
                            stop the iteration in case of error during the processing

        Add no_exception_log=True as context key if you don't want to get error log at the end of the integration
        """
        self.ensure_one()

        autocommit = self._should_commit()
        if autocommit:
            # new cursor is used for the complete process
            # so that everything is committed simultaneously
            new_cr = Registry(self.env.cr.dbname).cursor()
            new_env = api.Environment(new_cr, self.env.user.id, self.env.context)
            prev_env = self.env
            self = self.with_env(new_env)

        # integration is executed in priority in the context of its company
        # so that company_dependent fields are computed in the right company
        self = self.with_company(self.company_id or self.env.company)
        if data and isinstance(data, models.BaseModel):
            # data is a recordset, enforce to be executed in priority in the context of its company
            data = data.with_company(self.company_id or data.env.company)

        self.last_execution_date = fields.Datetime.now()

        exceptions = []
        self.env.cr.all_syncs = self.env["edi.synchronization"]
        try:
            # processing
            self.env.cr.activity = "Process"
            exceptions.extend(self._process_data(data=data, raise_error=raise_error))
        except Exception as e:
            self.env.cr.sync = self._create_error_sync(e)
            self.env.cr.all_syncs |= self.env.cr.sync
            self._safe_commit()
            exceptions.append(e)
        finally:
            self.env.cr.activity = "Set Status"
            self._set_status()

            self._safe_commit()
            if autocommit:
                new_cr.close()
                self = self.with_env(prev_env)

            # logging + traceback
            if not self.env.context.get("no_exception_log"):
                for e in exceptions:
                    _logger.exception(e)
            self._process_in_out_raise_errors(exceptions, raise_error)

    def _process_in_out_raise_errors(self, exceptions, raise_error):
        """Separated method to allow to define specific behaviour in case of errors (send emails...)"""
        self.ensure_one()
        if exceptions and raise_error:
            raise UserError("\n".join(map(str, exceptions)))

    def _process_data(self, data=None, raise_error=False):
        """Get and Process data (in/out)

        :param data:
            - in: list of dict
            - out: recordset
            if None:
                data will be automatically fetched
        :param raise_error: boolean, set True to get the traceback and
                            stop the iteration in case of error during the processing
        :return: list of exceptions
        """
        self.ensure_one()

        # get data to synchronize
        data = self._get_data(data=data)

        # process all the data to synchronize
        exceptions = self._process_synchronizations(data=data, raise_error=raise_error)
        self._on_synchronizations_done(exceptions)
        return exceptions

    def _on_synchronizations_done(self, exceptions):
        """Hook called after all synchronizations have been processed.

        Override to persist pagination cursors, update state fields, etc.
        The exceptions list contains any errors raised during processing.

        To implement in each integration:
        if self.type != 'my_type':
            return super()._on_synchronizations_done(exceptions)
        ...

        :param exceptions: list of exceptions raised during synchronization processing
        """
        self.ensure_one()

    def _get_data(self, data=None):
        """Get the data to synchronize

        :param data:
            - in: list of dict
            - out: recordset
            if None:
                - in flow: call _get_in_data
                - out flow: call _get_out_data
            if not None:  return param data

        :return:
            - in: list of dict
            - out: recordset
        """
        self.ensure_one()

        if data is None:
            with self.env.cr.savepoint():
                data = self._exec_method_based_on_flow(self._get_in_data, self._get_out_data)

        if not data:
            _logger.info(_("No data found to synchronize for %s [%s]", self.name, self.id))
        return data

    def _process_synchronizations(self, data, raise_error=False):
        """Process all synchronizations
        Each piece of data inside data will be part of its own synchronization

        :param data:
            - in: list of dict
            - out: recordset
        :param raise_error: boolean, set True to get the traceback and
                            stop the iteration in case of error during the processing
        :return: list of exceptions
        """
        self.ensure_one()

        exceptions = []
        data_by_sync = self._prepare_data_for_sync(data)
        for d in data_by_sync:
            try:
                self._process_synchronization(d)
            except Exception as e:
                exceptions.append(e)

                if raise_error:
                    # stop the iterations
                    return exceptions
        return exceptions

    def _prepare_data_for_sync(self, data):
        """Prepare the data to be process
        Data are grouped by synchronization based on the synchronization_creation integer.

        If synchronization_creation = 1 (one): each data will be processed in its own synchronization
        If synchronization_creation = 0 (multi): all data will be processed in the same synchronization
        If synchronization_creation = n: each batch of n data will be processed in its own synchronization

        :param data:
            - in: list of dict
            - out: recordset
        :return:
            - in: list of list of dict
            - out: list of recordset
            (each element will be processed in its own synchronization)
        """
        self.ensure_one()

        if not data:
            return []

        max_chunk_size = self._get_sync_max_chunk_size()
        if max_chunk_size <= 0:
            return [data]

        # chunk type is preserved (list -> list of lists, recordset -> list of recordsets)
        return _chunks(data, max_chunk_size)

    def _get_sync_max_chunk_size(self):
        """Get the max chunk size for a synchronization
        :return: integer
        """
        self.ensure_one()
        return self.synchronization_creation

    def _process_synchronization(self, data):
        """Process one synchronization (the data will be part of one synchronization)

        :param data:
            - in: list of dict
            - out: recordset
        """
        self.ensure_one()

        # create a default synchronization
        # commit it, so that the synchronization is created
        # even in case of timeout during the prosess
        self.env.cr.sync = self._create_synchronization(data)
        self.env.cr.all_syncs |= self.env.cr.sync
        self._safe_commit()

        try:
            self._execute_synchronization(data)
        except Exception as e:
            self._handle_error_execute_synchronization(data, e)
            raise
        else:
            self._handle_success_execute_synchronization(data)
        finally:
            self._safe_commit()

    def _execute_synchronization(self, data):
        """Process the data inside the synchronization
        - in flow: call _process_in
        - out flow: call _process_out

        :param data:
            - in: list of dict
            - out: recordset
        """
        self.ensure_one()
        self._exec_method_based_on_flow(self._process_in, self._process_out, data)

    def _handle_error_execute_synchronization(self, data, exc):
        """Handle the error that occurred during the execution of a synchronization
        :param data:
            - in: list of dict
            - out: recordset
        :param exc: exception that occurred during the execution of the synchronization
        """
        self.ensure_one()

        # report error on the sync
        self.env.cr.sync._report_error(self.env.cr.activity, exc)

        # handle error
        try:
            with self.env.cr.savepoint():
                self.env.cr.activity = "Handle Error"
                self._handle_error(data, exc)
        except Exception as exc_2:
            self.env.cr.sync._report_error(self.env.cr.activity, exc_2)
            raise ProcessIntegrationException(
                _("Fail to handle the exception (%s) due to %s", str(exc), str(exc_2))
            ) from exc_2

    def _handle_success_execute_synchronization(self, data):
        """Handle the success of the synchronization execution
        :param data:
            - in: list of dict
            - out: recordset
        """
        self.ensure_one()
        # mark sync as done
        self.env.cr.sync._done()

    def _clean_synchronization(self, data, status):
        """
        :param data:
            - in: list of dict
            - out: recordset
        :param status: str
        """
        self.ensure_one()
        self._exec_method_based_on_flow(self._clean_in_sync, self._clean_out_sync, data, status)

    ##########################################################
    # Common default Behavior: Probably need to reimplement  #
    ##########################################################
    # ========================================================#

    def _handle_error(self, data, exc):
        """Can be use to handle an error at the end of each synchronization

        To implement in each integration
        ....

        :param data:
            - in: list of dict
            - out: recordset
        :param: exception
        """
        self.ensure_one()
        self._clean_synchronization(data, "error")

    #####################################################################
    #                Implementation of process Realtime             #
    #####################################################################
    # ===================================================================#

    def _process_realtime(self, data=None, raise_error=False):
        """
        Same as process but we assume the trigger does not come from a cron
        but any method in odoo and that method is already aware of the data
        to synchronize (or if no data is provided, the process will automatically
        fetch the needed data to synchronize).

        Pay attention that:
        the integration is performed on a different cursor than the current cursor.
        For out flows, data is a recordset and its cursor is different from the cursor
        of the integration.
        So it can lead to inconsistency if an error occurs after the _process_realtime method
        since the integration will commit the status but the recorset cursor will be rolledback.
        Hence, the information sent could not be the one on the Odoo database.
        This is why an error is logged on the created synchronizations if a rollback occurs on the
        data cursor to alert of a potential data inconsistency.

        It is better to call this method instead _process_in_out because a flush
        is performed on the current cursor before starting the processing.
        So that the processing which is executed on another cursor is aware of your
        current cursor changes.

        Set autocommit=False in the context if you don't want the processing is
        executed on a different cursor (in that case, no commit is performed)

        Set skip_inconsistency_test=True to bypass the inconsistency check when the data
        cursor is rollback

        :param data:
            - in: list of dict
            - out: recordset
            if None: will fetch data to synchronize
            if not None: process the given data
        :param raise_error: boolean, set True if you don't want to have the processing
                            to fail silently
        """
        self.ensure_one()

        # Force to execute with the scheduled user (if we execute it from the interface)
        self = self.with_user(self.sudo().user_id)

        if (
            data
            and isinstance(data, models.BaseModel)
            and self._should_commit()
            and not self.env.context.get("skip_inconsistency_test")
        ):
            # if data is a recordset and autocommit, integration will be executed on a different cursor than data
            # if the data cursor rollback, an error will be logged on the synchronizations to alert of an
            # enventual data inconsistency between the odoo database and the third party system
            self = self.with_context(edi_data_cursor=data.env.cr)

            data.env.cr.postrollback.add(self._post_rollback_handler)
            data.env.cr.postrollback.data.setdefault("edi.integration.postrollback.integration_ids", []).append(self.id)

        self.env.flush_all()
        self._process_in_out(data=data, raise_error=raise_error)

    def _post_rollback_handler(self):
        """Method called after the rollback on the data cursor (only for realtime).
        Log an error on synchronizations to prevent of an eventual data inconsistency
        between what has been synchronized and the odoo database.
        This possible inconsitency can occur when an error is raised after the realtime
        has been executed.
        """
        data_cursor = self.env.context.get("edi_data_cursor")
        if data_cursor:
            with Registry(self.env.cr.dbname).cursor() as cr:
                env = api.Environment(cr, self.env.user.id, self.env.context)
                sync_ids = data_cursor.postrollback.data.pop("edi.integration.postrollback.synchronization_ids", [])
                integration_ids = data_cursor.postrollback.data.pop("edi.integration.postrollback.integration_ids", [])

                # report an error on created synchronizations
                syncs = env["edi.synchronization"].browse(sync_ids).exists()
                error_message = _(
                    "An error occurred after the _process_realtime operation. The validity of the data is not guaranteed."
                )
                syncs._report_error("Post Integration", message=error_message)

                # update status of integrations
                integration_ids = env["edi.integration"].browse(integration_ids).exists()
                integration_ids._set_status(syncs)
