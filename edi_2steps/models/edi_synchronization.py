from odoo import api, fields, models


class Synchronization(models.Model):
    _inherit = "edi.synchronization"

    updated_edi_table_record_ids = fields.Many2many(
        comodel_name="edi.table.record",
        relation="edi_table_record_updated_by_syncs",
        domain="[('integration_id', '=', integration_id)]",
        readonly=True,
        copy=False,
        string="Updated EDI 2-steps queue records",
        help="EDI 2-steps queue records updated by this synchronization",
        context={"active_test": False},
    )
    processed_edi_table_record_ids = fields.Many2many(
        comodel_name="edi.table.record",
        relation="edi_table_record_processed_by_syncs",
        domain="[('integration_id', '=', integration_id)]",
        readonly=True,
        copy=False,
        string="Processed EDI 2-steps queue records",
        help="EDI 2-steps queue records processed by this synchronization",
        context={"active_test": False},
    )
    updated_edi_table_record_count = fields.Integer(compute="_compute_updated_edi_table_record_count", store=True)
    processed_edi_table_record_count = fields.Integer(compute="_compute_processed_edi_table_record_count", store=True)
    edi_table_operation = fields.Selection(
        [("update", "Update"), ("process", "Process"), ("update_and_process", "Update & Process")],
        string="EDI 2-steps Operation",
        compute="_compute_edi_table_operation",
        store=True,
        help="Operation performed by the synchronization on EDI 2-steps queue records",
    )

    # -------------------------------------------------------------------------
    # Compute
    # -------------------------------------------------------------------------

    @api.depends("updated_edi_table_record_ids")
    def _compute_updated_edi_table_record_count(self):
        for rec in self:
            rec.updated_edi_table_record_count = len(rec.updated_edi_table_record_ids)

    @api.depends("processed_edi_table_record_ids")
    def _compute_processed_edi_table_record_count(self):
        for rec in self:
            rec.processed_edi_table_record_count = len(rec.processed_edi_table_record_ids)

    @api.depends("updated_edi_table_record_ids", "processed_edi_table_record_ids")
    def _compute_edi_table_operation(self):
        updated_table_records = self.env["edi.synchronization"]
        processed_table_records = self.env["edi.synchronization"]

        updated_edi_table_records = self.env["edi.table.record"]._read_group(
            [("updated_by_sync_ids", "in", self.ids)], ["updated_by_sync_ids"]
        )
        for rec in updated_edi_table_records:
            updated_table_records |= rec[0]

        processed_edi_table_records = self.env["edi.table.record"]._read_group(
            [("processed_by_sync_ids", "in", self.ids)], ["processed_by_sync_ids"]
        )
        for rec in processed_edi_table_records:
            processed_table_records |= rec[0]

        (updated_table_records & processed_table_records).edi_table_operation = "update_and_process"
        (updated_table_records - processed_table_records).edi_table_operation = "update"
        (processed_table_records - updated_table_records).edi_table_operation = "process"
        (self - (updated_table_records | processed_table_records)).edi_table_operation = False

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def open_updated_edi_table_records(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("edi_2steps.edi_table_record_act_window")
        action.update({
            "name": self.env._("EDI 2-steps queue records updated by %s", self.name),
            "domain": [("id", "in", self.updated_edi_table_record_ids.ids)],
        })
        return action

    def open_processed_edi_table_records(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("edi_2steps.edi_table_record_act_window")
        action.update({
            "name": self.env._("EDI 2-steps queue records processed by %s", self.name),
            "domain": [("id", "in", self.processed_edi_table_record_ids.ids)],
        })
        return action
