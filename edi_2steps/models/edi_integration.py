import logging
import uuid
from ast import literal_eval
from collections import defaultdict

from odoo import _, api, fields, models, Command
from odoo.addons.edi_2steps.tools.lock import Locker
from odoo.orm.domains import Domain

_logger = logging.getLogger(__name__)


class Integration(models.Model):
    _inherit = "edi.integration"

    # EDI 2-steps fields
    use_edi_table = fields.Boolean(string="Use EDI 2-steps", help="Use EDI 2-steps to process the integration")
    edi_table_synchronization_creation = fields.Integer(
        help="""Number of data to process by synchronization when processing EDI 2-steps queue (second step).\n"""
             """1: one data by synchronization (one)\n"""
             """0: all data inside the same synchronization (multi)\n"""
             """n: max n data in the same synchronization""",
        required=True,
        default=1,
    )
    edi_table_match_identifier = fields.Boolean(
        string="Match Identifier",
        help="""If ticked, it will try to match an existing record (not processed yet) and update it,"""
        """ otherwise it will create a new one""",
    )
    edi_table_record_clear_content = fields.Selection(
        [
            ("on_success", "On Success"),
            ("on_error", "On Error"),
            ("on_archiving", "On Archiving"),
            ("on_success_or_error", "On Success or Error"),
            ("on_success_or_archiving", "On Success or Archiving"),
            ("on_error_or_archiving", "On Error or Archiving"),
        ],
        default="on_success",
        string="Clear Record Content",
        help="Clear EDI 2-steps queue content",
    )
    edi_table_record_ids = fields.One2many(
        "edi.table.record", "integration_id", string="EDI 2-steps Records", readonly=True, copy=False
    )
    edi_table_records_count = fields.Integer(compute="_compute_edi_table_records_count")
    edi_table_records_to_process_count = fields.Integer(compute="_compute_edi_table_records_to_process_count")

    # EDI 2-steps CRON fields
    edi_table_cron_id = fields.Many2one(
        "ir.cron",
        string="EDI 2-steps Cron",
        readonly=True,
        copy=False,
        help="CRON used to process EDI 2-steps queue records (second step)",
        context={"active_test": False},
    )
    edi_table_interval_number = fields.Integer(
        string="EDI 2-steps Interval Number",
        related="edi_table_cron_id.interval_number",
        store=True,
        readonly=False,
        help="CRON Process EDI 2-steps queue records: Repeat every x. (second step)",
    )
    edi_table_interval_type = fields.Selection(
        string="EDI 2-steps Interval Unit",
        related="edi_table_cron_id.interval_type",
        store=True,
        readonly=False,
        help="CRON Process EDI 2-steps Queue Records: Interval unit. (second step)",
    )
    edi_table_lastcall = fields.Datetime(
        string="EDI 2-steps Last Execution Date",
        related="edi_table_cron_id.lastcall",
        store=True,
        readonly=False,
        help="""CRON Process EDI 2-steps queue records: Previous time the cron ran successfully, """
        """provided to the job through the context on the `lastcall` key.""",
    )
    edi_table_nextcall = fields.Datetime(
        string="EDI 2-steps Next Execution Date",
        related="edi_table_cron_id.nextcall",
        store=True,
        readonly=False,
        help="CRON Process EDI 2-steps queue records: Next planned execution date for this job.",
    )

    # -------------------------------------------------------------------------
    # Compute
    # -------------------------------------------------------------------------

    @api.depends("edi_table_record_ids")
    def _compute_edi_table_records_count(self):
        for integration in self:
            integration.edi_table_records_count = len(integration.edi_table_record_ids)

    @api.depends("use_edi_table")
    def _compute_edi_table_records_to_process_count(self):
        self.edi_table_records_to_process_count = 0
        integrations = self.filtered("use_edi_table")
        domain = Domain([])
        for integration in integrations:
            domain |= integration._edi_table_record_domain()
        if not domain:
            return
        count_mapping = defaultdict(int)
        groups = self.env["edi.table.record"]._read_group(domain, ["id"], ["integration_id:count"])
        for integration, count in groups:
            count_mapping[integration] = count
        for integration in integrations:
            integration.edi_table_records_to_process_count = count_mapping[integration]

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        integrations = super().create(vals_list)
        integrations.check_edi_table_cron_id()
        integrations._check_edi_table_locker()
        return integrations

    def write(self, vals):
        res = super().write(vals)

        # check EDI 2-steps CRON consistency (create it if needed)
        if "use_edi_table" in vals or "active" in vals:
            self.check_edi_table_cron_id()

        if "use_edi_table" in vals:
            self._check_edi_table_locker()

        # propagate some fields to the EDI 2-steps CRON
        for integration in self.filtered(lambda integ: integ.edi_table_cron_id):
            edi_table_cron_vals = integration._vals_to_propagate_on_edi_table_cron()
            edi_table_cron_vals = {
                field: edi_table_cron_vals[field] for field
                in set(edi_table_cron_vals.keys()).intersection(vals.keys())
            }
            if edi_table_cron_vals:
                integration.edi_table_cron_id.write(edi_table_cron_vals)

        return res

    def unlink(self):
        self.edi_table_cron_id.unlink()
        return super().unlink()

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def open_edi_table_records(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("edi_2steps.edi_table_record_act_window")
        ctx = dict(
            literal_eval(action.get("context", "{}")),
            default_integration_id=self.id, search_default_can_be_processed=True
        )
        action.update({
            "name": _("%s's EDI 2-steps queue records", self.name),
            "domain": [("integration_id", "=", self.id)],
            "context": ctx,
        })
        return action

    # -------------------------------------------------------------------------
    # Logics
    # -------------------------------------------------------------------------

    def _update_edi_table_from_vals(self, vals_list):
        """Create or update edi.table.record from vals_list.

        :param vals_list list of dict
        :return recordset of edi.table.record (created and updated)
        """
        self.ensure_one()
        updated_records = self.env["edi.table.record"]
        to_create = []

        if not self.edi_table_match_identifier:
            to_create = vals_list
        else:
            identifiers = [vals["identifier"] for vals in vals_list if vals.get("identifier")]
            identified_records = self._get_edi_table_records_by_identifier(identifiers)

            for vals in vals_list:
                if vals.get("identifier") in identified_records:
                    record = identified_records[vals["identifier"]][-1]
                    record._reset(vals=vals)
                    updated_records |= record
                else:
                    to_create.append(vals)
        if to_create:
            updated_records |= self.env["edi.table.record"].create(to_create)
        return updated_records

    def _vals_to_propagate_on_edi_table_cron(self):
        self.ensure_one()
        return {"priority": self.priority, "user_id": self.user_id.id}

    @api.model
    def create_xmlid(self, record):
        if record.exists():
            self.env["ir.model.data"].create({
                "name": f"{record._table}_{record.id}_{uuid.uuid4().hex[:8]}",
                "module": "__cloc_exclude__",
                "res_id": record.id,
                "model": record._name,
            })

    def check_edi_table_cron_id(self):
        """Check the EDI 2-steps CRON consistency.

        If the integration use the EDI 2-steps (use_edi_table is True):
            - if the CRON is not defined, create it
        Else:
            - if the CRON is defined, archive it
        """
        edi2_steps_integrations = self.filtered("use_edi_table")
        # Archive table cron if 2 steps is not used.
        (self - edi2_steps_integrations).filtered("edi_table_cron_id").edi_table_cron_id.active = False
        crons_to_create = []
        for integration in edi2_steps_integrations.filtered(lambda integ: not integ.edi_table_cron_id):
            # create the EDI 2-steps CRON
            crons_to_create.append({
                **self._default_cron_vals(),
                **integration._vals_to_propagate_on_edi_table_cron(),
                "name": f"Process EDI 2-steps queue for {integration.name}",
                "code": f"model._process_edi_table({integration.id})",
                # forcing to 1 minute, to avoid from being considered as slowing down the complete flow
                # risk is to reach 15min on SH if too many crons have too many records to process
                "interval_type": "minutes",
                "integration_ids": [Command.link(integration.id)],
            })
        self.env["ir.cron"].create(crons_to_create)
        # Add xml_id to the server action does avoid the test.cloc.count_customization() to catch it
        self.create_xmlid(edi2_steps_integrations.edi_table_cron_id.ir_actions_server_id)

        for integration in edi2_steps_integrations:
            integration.edi_table_cron_id.active = integration.active

    def _check_edi_table_locker(self):
        """Check the EDI 2-steps Locker Sequence.

        If the integration use the EDI 2-steps (use_edi_table is True):
            - if the locker sequence is not defined, create it
        Else:
            - if the locker sequence is defined, archive it
        """
        sequence_lockers_names = self.filtered("use_edi_table").mapped(lambda integ: integ._get_sequence_locker_name())
        sequences = self.env["ir.sequence"].with_context(active_test=False).search_read(
            [("name", "in", sequence_lockers_names)], ["id", "name"]
        )
        existing_sequences = {sequence["name"] for sequence in sequences}
        sequences_to_create = []
        for sequence_locker_name in sequence_lockers_names:
            if sequence_locker_name not in existing_sequences:
                sequences_to_create.append({
                    "name": sequence_locker_name,
                    "active": False,
                })
        self.env["ir.sequence"].create(sequences_to_create)

    def _convert_edi_table_records_to_data_in(self, records):
        self.ensure_one()
        return [{
            "filename": rec.filename,
            "content": rec.content,
            "edi_table_record": rec,
        } for rec in records]

    def _convert_edi_table_to_data_out(self, records):
        self.ensure_one()
        return records

    def _convert_edi_table_records_to_data(self, records):
        """Convert edi.table.record to data.

        :param records: recordset edi.table.record
        :return:
            - in: list of dict
                each dict contains key
                - filename: str
                - content: str
                - edi_table_record: record edi.table.record
            - out: recordset edi.table.record
        """
        self.ensure_one()
        return self._exec_method_based_on_flow(
            self._convert_edi_table_records_to_data_in,
            self._convert_edi_table_to_data_out,
            records,
        )

    def _convert_data_to_edi_table_records_in(self, data):
        self.ensure_one()
        return self.env["edi.table.record"].union(*map(lambda d: d["edi_table_record"], data))

    def _convert_data_to_edi_table_records_out(self, data):
        self.ensure_one()
        return data

    def _convert_data_to_edi_table_records(self, data):
        """Convert data to edi.table.record.

        :param data:
            - in: list of dict
                each dict contains key
                - filename: str
                - content: str
                - edi_table_record: record edi.table.record
            - out: recordset edi.table.record
        :return: recordset edi.table.record
        """
        self.ensure_one()
        return self._exec_method_based_on_flow(
            self._convert_data_to_edi_table_records_in,
            self._convert_data_to_edi_table_records_out,
            data,
        )

    def _get_sequence_locker_name(self) -> str:
        self and self.ensure_one()
        return f"edi_2steps_integration_locker_{self.id}"

    def _should_process_edi_table(self) -> bool:
        """Check if the current context should process edi.table.record in EDI 2-steps (second step)."""
        return self.use_edi_table and self.env.context.get("process_edi_table")

    ###########################################
    #             Generic API                 #
    ###########################################
    # ========================================#

    def _process_in_out(self, data=None, raise_error=False):
        """Prevent the 2 CRONS (Step 1 (update) + Step 2 (process)) to be executed at the same time.

        Try to grab a lock before starting the integrations
        """
        if not self.use_edi_table:
            return super()._process_in_out(data=data, raise_error=raise_error)

        self.ensure_one()
        step = "2" if self._should_process_edi_table() else "1"
        _logger.info(f"Starting EDI {self.name} (step {step})")
        with Locker(self.env, self.pool, self._get_sequence_locker_name()):
            # locker ensure step 1 and step 2 cannot be executed simultaneously (avoid concurrency update issues)
            return super()._process_in_out(data=data, raise_error=raise_error)

    @api.model
    def _process_edi_table(self, integration_id):
        """Process edi.table.record for the given integration (second step).

        if integration.use_edi_table is False, execute the standard _process method
        """
        return self.with_context(process_edi_table=True)._process(integration_id)

    def _get_sync_max_chunk_size(self):
        """Get the max chunk size for a synchronization.

        :return integer
        """
        # OVERRIDE
        self.ensure_one()
        if self._should_process_edi_table():
            return self.edi_table_synchronization_creation
        return super()._get_sync_max_chunk_size()

    def _handle_error(self, data, exc):
        """Can be use to handle an error at the end of each synchronization.

        To implement in each integration
        if not self.type == 'My type':
            return super()._handle_error(data, exc)
        ....

        Extended to handle edi.table.record processing errors

        :param data: list of dict
            each dict contains key
            - filename: str
            - content: str
        :param exc: exception
        """
        self.ensure_one()
        if self._should_process_edi_table():
            self._handle_error_edi_table(data, exc)
        return super()._handle_error(data, exc)

    def _handle_success_execute_synchronization(self, data):
        """Handle the success of the synchronization execution.

        Extended to handle edi.table.record processing success

        :param data:
            - in: list of dict
            - out: recordset
        """
        self.ensure_one()
        if self._should_process_edi_table():
            self._handle_success_edi_table(data)
        return super()._handle_success_execute_synchronization(data)

    def _handle_error_edi_table(self, data, exc):
        """Handle error when processing edi.table.record (second step).

        :param data:
            - in: list of dict
                each dict contains key
                - filename: str
                - content: str
                - edi_table_record: record edi.table.record
            - out: recordset edi.table.record
        :param exc: exception
        """
        self.ensure_one()
        edi_table_records = self._convert_data_to_edi_table_records(data)

        # mark the edi.table.record as processed with error
        edi_table_records.processed_by_sync_ids = [Command.link(self.env.cr.sync.id)]
        edi_table_records._sync_error(exc)
        edi_table_records._clear_from_action("error")

        self._exec_method_based_on_flow(self._handle_error_edi_table_in, self._handle_error_edi_table_out, data, exc)

    def _handle_success_edi_table(self, data):
        """Handle success when processing edi.table.record (second step).

        :param data:
            - in: list of dict
                each dict contains key
                - filename: str
                - content: str
                - edi_table_record: record edi.table.record
            - out: recordset edi.table.record
        """
        self.ensure_one()
        edi_table_records = self._convert_data_to_edi_table_records(data)

        # mark the edi.table.record as processed with success
        edi_table_records.processed_by_sync_ids = [Command.link(self.env.cr.sync.id)]
        edi_table_records._sync_success()
        edi_table_records._clear_from_action("success")

        self._exec_method_based_on_flow(self._handle_success_edi_table_in, self._handle_success_edi_table_out, data)

    #############################################
    # Generic API for creating edi.table.record #
    #############################################
    # ==========================================#

    def _update_edi_table(self, data):
        """Create or update edi.table.record from data.

        :param data:
            - in: list of dict
            - out: recordset
        :return: recordset of edi.table.record (created and updated)
        """
        self.ensure_one()
        default_vals = self._prepare_default_edi_table_vals(data)
        edi_table_records_vals_list = self._prepare_edi_table_vals(data)
        edi_table_records_vals_list = [
            dict(default_vals, **edi_table_records_vals) for edi_table_records_vals in edi_table_records_vals_list
        ]
        return self._update_edi_table_from_vals(edi_table_records_vals_list)

    def _prepare_default_edi_table_vals(self, data):
        """Prepare default vals for edi.table.record.

        :param data:
            - in: list of dict
            - out: recordset
        :return: dict
        """
        self.ensure_one()
        return {
            "integration_id": self.id,
            "updated_by_sync_ids": [Command.link(self.env.cr.sync.id)],
            "state": "new",
            "content": False,
            **self._exec_method_based_on_flow(
                self._prepare_in_default_edi_table_vals, self._prepare_out_default_edi_table_vals, data
            ),
        }

    def _prepare_edi_table_vals(self, data):
        """Prepare vals for edi.table.record.

        :param data:
            - in: list of dict
            - out: recordset
        :return: list of dict
        """
        self.ensure_one()
        return self._exec_method_based_on_flow(self._prepare_in_edi_table, self._prepare_out_edi_table, data)

    ###############################################
    # Generic API for processing edi.table.record #
    ###############################################
    # ============================================#

    def _get_edi_table_record_to_process(self):
        """Return the edi.table.record to process.

        :return: recordset of edi.table.record
        """
        self.ensure_one()
        return self._get_edi_table_records()

    def _edi_table_record_domain(self):
        """Return the domain to search edi.table.record to process."""
        self.ensure_one()
        return Domain([
            ("integration_id", "=", self.id),
            ("state", "not in", self.env["edi.table.record"]._end_states())
        ])

    def _edi_table_record_order(self, method=False):
        """Return the order of edi.table.record to process."""
        self.ensure_one()
        return (lambda r: (r.create_date, r.id)) if method else "create_date, id"

    ######################################
    # MISC for managing edi.table.record #
    ######################################
    # ===================================#

    def _get_edi_table_records(self, identifiers = None):
        """Return the edi.table.record linked to this integration (to process).

                :identifiers: list[str]
                    if None, cancel all the edi.table.record linked to this integration
                :return: recordset of edi.table.record
                """
        self.ensure_one()
        domain = self._edi_table_record_domain()
        if identifiers:
            domain &= Domain([("identifier", "in", identifiers)])
        return self.env["edi.table.record"].search(domain, order=self._edi_table_record_order())

    def _get_edi_table_records_by_identifier(self, identifiers):
        """Return the edi.table.record linked to this integration (to process).

        :identifiers: list[str]
            if None, cancel all the edi.table.record linked to this integration
        :return: recordset of edi.table.record
        """
        result = defaultdict(self.env["edi.table.record"])
        for rec in self._get_edi_table_records(identifiers):
            result[rec.identifier] |= rec
        return result
