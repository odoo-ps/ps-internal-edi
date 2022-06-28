import ast
import operator

from odoo import api, fields, models, tools

MSG_NEW = "Either report was created during the execution, "\
          "or has been killed by odoo.sh because of too 15' execution timeout"
MAX_SYNCHROS_BY_REPORT = 1000


class Monitoring(models.Model):
    _name = 'edi.monitoring'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'EDI monitoring'
    _order = 'name'

    active = fields.Boolean(default=True)
    name = fields.Char(required=True)
    email = fields.Char(tracking=True)
    filter_id = fields.Many2one('ir.filters', string="Filter", ondelete='restrict')
    domain = fields.Char(compute='_compute_filter_info')
    model_id = fields.Char(compute='_compute_filter_info')

    report_ids = fields.One2many('edi.monitoring.report', 'monitoring_id')

    @api.depends('filter_id.model_id', 'filter_id.domain')
    def _compute_filter_info(self):
        for rec in self:
            rec.domain = rec.filter_id.domain
            rec.model_id = rec.filter_id.model_id

    def action_create_report(self):
        if not self:  # from cron task
            self = self.search([])
        report_ids = self.env['edi.monitoring.report']
        for rec in self:
            report_ids |= rec.report_ids.create({'monitoring_id': rec.id})

        # force a new search for line_ids at next usage, otherwise we will get them from cache
        # => the order will the created order (id asc) and not the order defined in _order
        report_ids.invalidate_cache(['line_ids'], report_ids.ids)

        report_ids.action_send_report()
        return True

    def add_integration_to_domain(self, integration_ids):
        """ add active and inactive integrations to the domain of the monitoring """
        for rec in self:
            for integration_id in integration_ids:
                domain = ast.literal_eval(rec.filter_id.domain)
                if ['integration_id.type', '=', integration_id.type] in domain:
                    continue
                first_type = next((x for x in domain if type(x) is list and x[0] == 'integration_id.type'), None)
                if not first_type:
                    domain.append(['integration_id.type', '=', integration_id.type])
                else:
                    index_first_type = domain.index(first_type)
                    domain.insert(index_first_type, ['integration_id.type', '=', integration_id.type])
                    domain.insert(index_first_type, '|')
                rec.filter_id.domain = domain
        return True


class MonitoringReport(models.Model):
    _name = 'edi.monitoring.report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'EDI monitoring report'
    _rec_name = 'create_date'
    _order = 'create_date desc'

    monitoring_id = fields.Many2one('edi.monitoring', string='Monitoring', required=True, ondelete='cascade')
    email = fields.Char(related='monitoring_id.email')
    sent = fields.Datetime()
    line_ids = fields.One2many('edi.monitoring.report.line', 'parent_id', string='Lines')
    lines_count = fields.Integer(compute='_compute_count_lines')
    archived_items_count = fields.Integer(default=0)

    synchronization_ids = fields.One2many('edi.synchronization', 'monitoring_report_id', string='Synchronizations')
    related_lines_count = fields.Integer(compute='_compute_related_lines_count')
    limit_reached = fields.Boolean(compute='_compute_limit_reached')

    @api.depends('line_ids')
    def _compute_count_lines(self):
        for rec in self:
            rec.lines_count = len(rec.line_ids)

    @api.depends('synchronization_ids')
    def _compute_related_lines_count(self):
        for rec in self:
            rec.related_lines_count = len(rec.synchronization_ids)

    @api.depends('related_lines_count', 'lines_count', 'archived_items_count')
    def _compute_limit_reached(self):
        """ Deduce if we had to limit the nr of lines """
        for rec in self:
            rec.limit_reached = rec.related_lines_count != rec.lines_count - rec.archived_items_count

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        res._create_synchronizations()
        res._add_archived_integrations()
        return res

    def _create_synchronizations(self):
        """ Create synchronization based on the domain & the integrations on related monitoring
        note : if no integration is defined then the synchronizations of all integrations are considered

        For each synchronization, a report line will be generated
        """
        for rec in self:
            if rec.monitoring_id.model_id == 'edi.synchronization':
                domain = ast.literal_eval(rec.monitoring_id.domain)

                # limit the nb of synchros to display on a report, but link all the synchros anyway
                rec.synchronization_ids = rec.synchronization_ids.search(domain)  # link all synchros to a report
                synchronization_ids = (rec.synchronization_ids[:1000]
                                       if len(rec.synchronization_ids) > MAX_SYNCHROS_BY_REPORT
                                       else rec.synchronization_ids)

                vals_lines = []
                for synchronization_id in synchronization_ids:
                    vals_lines.append({
                        'source': synchronization_id.integration_id.name,
                        'name': synchronization_id.filename,
                        'date': synchronization_id.create_date,
                        'detail': MSG_NEW if synchronization_id.state == 'new' else synchronization_id.last_issue,
                        'state': synchronization_id.state,
                        'parent_id': rec.id,
                        'synchronization_id': synchronization_id.id
                    })
                if vals_lines:
                    rec.line_ids.create(vals_lines)

    def _add_archived_integrations(self):
        """ create report lines for archived integrations that are monitored """
        for rec in self:
            domain = ast.literal_eval(rec.monitoring_id.domain)
            types = [x[2] for x in domain if type(x) is list and x[0] == 'integration_id.type']
            if not type:
                continue
            archived_integration_ids = self.env['edi.integration'].with_context(active_test=False).search([
                ('type', 'in', types),
                ('active', '=', False)])

            vals_lines = []
            for integration_id in archived_integration_ids:
                vals_lines.append({
                    'source': integration_id.name,
                    'detail': "Integration is deactivated (maybe by odoo.sh because of 15' execution timeout ?)",
                    'state': 'warning',
                    'parent_id': rec.id,
                })
            if vals_lines:
                rec.line_ids.create(vals_lines)
                rec.archived_items_count = len(vals_lines)

    def action_send_report(self):
        """ send reports by email """
        template = self.env.ref('edi_monitoring.report_mail')
        sendable_ids = self.filtered(lambda x: x.email and x.line_ids)
        for rec in sendable_ids:
            """extra_values = {
                'sources': [
                    {
                        'name': 'tests',
                        'flow': 'out',
                        'states': [
                            {
                                'name': 'fail',
                                'number': 5,
                            }
                        ]
                    }
                ]
            }"""

            template.with_context(monitoring=rec._prepare_email()).send_mail(
                rec.id,
                notif_layout='mail.mail_notification_light')
        sendable_ids.write({'sent': fields.Datetime.now()})
        return True

    def _prepare_email(self):
        self.ensure_one()
        extra_values = {
            'sources': [],
        }

        for source, source_lines in tools.groupby(self.line_ids, operator.itemgetter('source')):
            source_lines = self.env['edi.monitoring.report.line'].concat(*source_lines)
            source = {
                'name': source,
                'states': [],
            }

            for state, state_lines in tools.groupby(source_lines, operator.itemgetter('state')):
                state_lines = self.env['edi.monitoring.report.line'].concat(*state_lines)
                state = {
                    'name': state,
                    'name_display': state_lines[0].state_val,
                    'number': len(state_lines)
                }
                source['states'].append(state)

            extra_values['sources'].append(source)

        return extra_values


class MonitoringReportLine(models.Model):
    _name = 'edi.monitoring.report.line'
    _description = 'EDI monitoring report line'
    _order = 'source, date desc, state'

    source = fields.Char(required=True)
    name = fields.Char()
    date = fields.Datetime()
    detail = fields.Char()
    state = fields.Selection([('new', 'Started'),
                              ('fail', 'Fail'),
                              ('done', 'Done'),
                              ('cancelled', 'Cancelled'),
                              ('warning', 'Warning')])
    state_val = fields.Char(compute='_compute_state_val')
    parent_id = fields.Many2one('edi.monitoring.report', string='Monitoring Report', required=True, ondelete='cascade')
    synchronization_id = fields.Many2one('edi.synchronization')

    @api.depends('state')
    def _compute_state_val(self):
        for rec in self:
            rec.state_val = [x[1] for x in rec._fields.get('state').selection if x[0] == rec.state][0]

    def name_get(self):
        res = []
        for rec in self:
            res.append((rec.id, '%s%s%s%s' % (rec.source,
                                              ' - ' if rec.date else '',
                                              rec.date,
                                              ' : ' if rec.name else '')))
        return res
