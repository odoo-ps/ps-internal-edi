""" Add the execution time on synchronizations """
from odoo import api, fields, models


class Integration(models.Model):
    _inherit = "edi.integration"

    reasonable_execution_time = fields.Float(
        default=10,
        help="""
Delay in minutes to consider that we reach a reasonable limit for a synchronization.
By default 10 minutes, considering that we reach 2/3 of real time limit on odoo.sh""",
    )

    def _execute_synchronization(self, data):
        """Override to evaluate the execution time of the synchronization"""
        try:
            res = super()._execute_synchronization(data)
        except Exception:
            raise
        finally:
            self.env.cr.sync.synchronization_end_date = fields.Datetime.now()
        return res


class Synchronization(models.Model):
    _inherit = "edi.synchronization"

    synchronization_end_date = fields.Datetime(readonly=True, string="Synchronized end on")
    execution_time = fields.Float(compute="_compute_execution_time", store=True)
    reasonable_execution_time = fields.Float(related="integration_id.reasonable_execution_time", store=True)

    @api.depends("synchronization_date", "synchronization_end_date")
    def _compute_execution_time(self):
        for rec in self:
            rec.execution_time = (
                (rec.synchronization_end_date - rec.synchronization_date).total_seconds() / 60
                if rec.synchronization_date and rec.synchronization_end_date
                else False
            )
