# Part of Odoo. See LICENSE file for full copyright and licensing details.
import traceback

from odoo import fields, models


class SynchronizationError(models.Model):
    """
    Object added on the synchronization to alert that an error occurred during the synchronization processing
    """

    _name = "edi.synchronization.error"
    _description = "Synchronization Error"
    _order = "create_date desc"

    integration_id = fields.Many2one(related="synchronization_id.integration_id", store=True)
    synchronization_id = fields.Many2one(comodel_name="edi.synchronization", ondelete="cascade", readonly=True)
    activity = fields.Char(readonly=True)
    description = fields.Text(readonly=True)
    description_short = fields.Text(compute="_compute_short_desc")
    company_id = fields.Many2one(related="synchronization_id.company_id", store=True)

    def _compute_short_desc(self):
        for rec in self:
            if not rec.description or len(rec.description) < 650:
                rec.description_short = rec.description
            else:
                rec.description_short = "%s\n....\n%s" % (rec.description[:150], rec.description[-500:])


class Synchronization(models.Model):
    """
    Object to store the status of the synchronization
    """

    _name = "edi.synchronization"
    _description = "Synchronization"
    _order = "create_date desc"

    name = fields.Char(readonly=True, required=True)
    name_short = fields.Char(compute="_compute_name_short")
    filename = fields.Char(readonly=True)
    filename_short = fields.Char(compute="_compute_filename_short")
    state = fields.Selection(
        [("new", "New"), ("fail", "Fail"), ("done", "Done"), ("cancelled", "Cancelled")], default="new", string="Status"
    )
    integration_id = fields.Many2one("edi.integration", required=True, string="Integration")
    company_id = fields.Many2one(related="integration_id.company_id", store=True)
    synchronization_flow = fields.Selection(
        related="integration_id.integration_flow", store=True, readonly=True, string="Type"
    )
    content_type = fields.Selection(
        related="integration_id.synchronization_content_type", store=True, readonly=True, string="Content type"
    )
    res_id = fields.Integer(string="Resource ID")
    synchronization_date = fields.Datetime(readonly=True, string="Synchronized on")
    content = fields.Text(readonly=True)
    error_ids = fields.One2many("edi.synchronization.error", "synchronization_id", string="synchronization_id")
    user_id = fields.Many2one(
        "res.users", string="Trigger User", help="User that trigger the synchronization or call the API"
    )
    color = fields.Integer(compute="_compute_color")

    def _compute_color(self):
        mapping = {
            "new": 4,
            "fail": 1,
            "done": 10,
            "cancelled": 0,
        }
        for rec in self:
            rec.color = mapping.get(rec.state, 0)

    def _compute_name_short(self):
        max_size = 80
        for rec in self:
            if not rec.name or len(rec.name) < max_size:
                rec.name_short = rec.name
            else:
                rec.name_short = "%s..." % rec.name[:max_size]

    def _compute_filename_short(self):
        max_size = 150
        for rec in self:
            if not rec.filename or len(rec.filename) < max_size:
                rec.filename_short = rec.filename
            else:
                rec.filename_short = "%s..." % rec.filename[:max_size]

    def open_integration(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "name": "Integration",
            "res_model": "edi.integration",
            "res_id": self.integration_id.id,
            "view_mode": "form",
        }

    ##################
    #      API       #
    ##################
    def _report_error(self, activity, exception=None, message=None):
        """Add an error on the synchronization

        :param activity: str
        :param exception: exception
        :param message: str
        """
        description = "Unkown Error"
        if exception:
            tb = traceback.format_exc()
            description = "%s\n\n%s" % (str(exception), str(tb))
        if message:
            description = message

        self.write(
            {
                "state": "fail",
                "error_ids": [
                    (
                        0,
                        0,
                        {
                            "activity": activity,
                            "description": description,
                        },
                    )
                ],
            }
        )
        self.flush(fnames=["state", "error_ids", "content_type"], records=self)

    def _write_content(self, content):
        """
        :param content: str
        """
        self.write({"content": content})
        self.flush(fnames=["content"], records=self)

    def _done(self):
        self.write({"state": "done"})
        self.flush(fnames=["state", "content_type", "synchronization_flow"], records=self)
