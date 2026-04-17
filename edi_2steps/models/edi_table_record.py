from collections import defaultdict

import psycopg2

from odoo import _, api, fields, models, Command
from odoo.exceptions import UserError
from odoo.orm.domains import Domain
from odoo.tools import groupby


class EdiTableRecord(models.Model):
    _name = "edi.table.record"
    _description = "EDI 2-steps queue record"
    _order = "create_date desc, id desc"

    name = fields.Char(readonly=True, required=True)
    name_short = fields.Char(compute="_compute_name_short")
    identifier = fields.Char(readonly=True, index=True, help="Identifier of the record (not mandatory)")
    filename = fields.Char(readonly=True)
    filename_short = fields.Char(compute="_compute_filename_short")
    state = fields.Selection(
        [("new", "Started"), ("warning", "Warning"), ("fail", "Fail"), ("done", "Done"), ("cancelled", "Cancelled")],
        required=True,
        default="new",
        index=True,
    )
    integration_id = fields.Many2one("edi.integration", required=True, readonly=True, index=True)
    company_id = fields.Many2one(related="integration_id.company_id", store=True)
    content = fields.Text(readonly=True)
    last_process_date = fields.Datetime(
        compute="_compute_last_sync_data",
        index=True,
        help="Last time when the record has been processed",
        store=True
    )
    updated_by_sync_ids = fields.Many2many(
        comodel_name="edi.synchronization",
        relation="edi_table_record_updated_by_syncs",
        readonly=True,
        copy=False,
        domain="[('integration_id', '=', integration_id)]",
        help="Synchronizations that updated the record (first step)",
        context={"active_test": False},
    )
    processed_by_sync_ids = fields.Many2many(
        comodel_name="edi.synchronization",
        relation="edi_table_record_processed_by_syncs",
        domain="[('integration_id', '=', integration_id)]",
        readonly=True,
        copy=False,
        help="Synchronizations that processed the record (second step)",
        context={"active_test": False},
    )
    updated_by_sync_count = fields.Integer(
        string="Updates",
        help="# of synchronizations that updated the record",
        compute="_compute_updated_by_sync_count",
        store=True,
    )
    processed_by_sync_count = fields.Integer(
        string="Process Attempts",
        help="# of synchronizations that processed the record",
        compute="_compute_processed_by_sync_count",
        store=True,
    )
    can_be_processed = fields.Boolean(
        compute="_compute_can_be_processed",
        search="_search_can_be_processed",
        help="Can be processed by the second step?",
    )

    error_ids = fields.Many2many(
        "edi.synchronization.error",
        compute="_compute_last_sync_data",
        store=True,
    )

    def _compute_name_short(self):
        max_size = 80
        for rec in self:
            if not rec.name or len(rec.name) < max_size:
                rec.name_short = rec.name
            else:
                rec.name_short = f"{rec.name[:max_size]}..."

    def _compute_filename_short(self):
        max_size = 150
        for rec in self:
            if not rec.filename or len(rec.filename) < max_size:
                rec.filename_short = rec.filename
            else:
                rec.filename_short = f"{rec.filename[:max_size]}..."

    @api.depends("integration_id", "state", "integration_id.use_edi_table")
    def _compute_can_be_processed(self):
        """Compute records that can be processed by the second step"""
        self.can_be_processed = False
        for integration, group_records in groupby(self, lambda r: r.integration_id):
            group_records = self.filtered(lambda r: r in group_records)
            if integration.use_edi_table:
                records_to_process = group_records.filtered_domain(integration._edi_table_record_domain())
                records_to_process.can_be_processed = True

    @api.depends("processed_by_sync_ids", "updated_by_sync_ids")
    def _compute_last_sync_data(self):
        for rec in self:
            last_sync = (rec.updated_by_sync_ids | rec.processed_by_sync_ids).sorted("triggered_date", reverse=True)[:1]
            rec.error_ids = [Command.set(last_sync.error_ids.ids)]
            rec.last_process_date = last_sync.triggered_date

    def _search_can_be_processed(self, operator, value):
        """Search records that can be processed by the second step"""
        if operator not in ("=", "!=") or value not in (True, False):
            raise UserError(_("Invalid operator or value"))

        domain = []
        for integration in (
            self.env["edi.integration"].with_context(active_test=False).search([("use_edi_table", "=", True)])
        ):
            domain = Domain.OR([domain, integration._edi_table_record_domain()])
        if not domain:
            domain = [("id", "=", 0)]
        if (operator == "=" and not value) or (operator == "!=" and value):
            domain = ["!"] + domain
        return domain

    @api.depends("updated_by_sync_ids")
    def _compute_updated_by_sync_count(self):
        for rec in self:
            rec.updated_by_sync_count = len(rec.updated_by_sync_ids)

    @api.depends("processed_by_sync_ids")
    def _compute_processed_by_sync_count(self):
        for rec in self:
            rec.processed_by_sync_count = len(rec.processed_by_sync_ids)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def open_synchronizations(self):
        self.ensure_one()

        action_dict = self.env.ref("edi_base.synchronizations_act_window").read([])[0]
        action_dict.update(
            {
                "name": _("Synchronizations"),
                "domain": [("id", "in", (self.updated_by_sync_ids | self.processed_by_sync_ids).ids)],
            }
        )
        return action_dict

    def open_updated_by_syncs(self):
        self.ensure_one()

        action_dict = self.env.ref("edi_base.synchronizations_act_window").read([])[0]
        action_dict.update(
            {"name": _("Updated by synchronizations"), "domain": [("id", "in", self.updated_by_sync_ids.ids)]}
        )
        return action_dict

    def open_processed_by_syncs(self):
        self.ensure_one()

        action_dict = self.env.ref("edi_base.synchronizations_act_window").read([])[0]
        action_dict.update(
            {"name": _("Processed by synchronizations"), "domain": [("id", "in", self.processed_by_sync_ids.ids)]}
        )
        return action_dict

    def action_process(self):
        """Process the second step

        Records are grouped by integration.
        Groups are processed in a special order (according to the priority of integrations).
        """
        data_to_process = defaultdict(list)

        for integration, group_records in groupby(self, lambda r: r.integration_id):
            if not integration.use_edi_table:
                raise UserError(_("Integration %s is not configured to use EDI 2-steps queue", integration.name))

            # filter
            group_records = self.filtered(lambda r: r.id in group_records)
            records_to_process = group_records.filtered("can_be_processed")
            if records_to_process != group_records:
                raise UserError(_("Some records cannot be processed"))

            # order
            records_to_process = records_to_process.sorted(integration._edi_table_record_order(method=True))

            # convert record to data
            records_data = integration._convert_edi_table_to_data(records_to_process)
            data_to_process[integration] = records_data

        # process integrations according to cron priority
        integrations = self.integration_id.sorted(key=lambda integ: integ.id)
        for integration in integrations:
            # execute integration
            integration.with_context(process_edi_table=True, autocommit=False)._process_realtime(
                data=data_to_process[integration]
            )

    def action_cancel(self, raise_error=True):
        can_be_cancelled = self.filtered("can_be_processed")
        if raise_error and can_be_cancelled != self:
            raise UserError(_("Some records cannot be cancelled"))
        can_be_cancelled._cancel()

    def action_clear(self, raise_error=True):
        can_be_cleared = self.filtered(lambda r: not r.can_be_processed)
        if raise_error and can_be_cleared != self:
            raise UserError(_("Some records cannot be cleared"))
        can_be_cleared._clear()

    def action_reset(self):
        self._reset()

    def action_archive(self):
        self._clear_from_action("archive")
        return super().action_archive()

    # -------------------------------------------------------------------------
    # Logics
    # -------------------------------------------------------------------------

    @api.model
    def _end_states(self):
        return ["success", "fail", "cancel"]

    def _get_error_state(self, exc):
        """Get the state to set when an error occurs

        :param exc: Exception that caused the error
        :return: State to set
        """
        self.ensure_one()
        state = "fail"
        if isinstance(exc, psycopg2.errors.SerializationFailure):
            # concurrent update
            state = "warning"
        return state

    def _sync_error(self, exc):
        """Set the record in error state

        :param exc: Exception that caused the error
        """
        for rec in self:
            rec.write({"state": rec._get_error_state(exc)})

    def _sync_success(self):
        """Set the record in success state"""
        self.state = "done"

    def _clear_from_action_filter(self, clear_rule, action):
        if not self.can_be_processed:
            clear_rule = self.integration_id.edi_table_record_clear_content
            if action == "success" and clear_rule in ["on_success", "on_success_or_error"]:
                return True
            elif action == "error" and clear_rule in ["on_error", "on_success_or_error"]:
                return True
        return False

    def _clear_from_action(self, action):
        """Clear the content of the record

        :param action: Action that caused the clear (success, error, archive)
        """
        self.filtered(
            lambda rec: rec._clear_from_action_filter(rec.integration_id.edi_table_record_clear_content, action)
        )._clear()

    def _clear(self):
        """Clear the content of the record"""
        self.content = False

    def _cancel(self):
        """Cancel the record (to not be processed)"""
        self.state = "cancelled"

    def _reset(self, vals=None):
        """Reset the record to a new state (to be reprocessed)"""
        vals = {"state": "new", **(vals or {})}
        self.write(vals)
