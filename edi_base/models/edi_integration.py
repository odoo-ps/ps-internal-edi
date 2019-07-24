# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
import traceback

from datetime import datetime as dt

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, safe_eval

_logger = logging.getLogger(__name__)


SYNCHRONIZATION_STATES = ['new', 'fail', 'done', 'cancel']


class Integration(models.Model):

    _name = 'edi.integration'
    _description = 'Integration to process by Odoo instance'
    _inherits = {'ir.cron': 'cron_id'}

    integration_flow = fields.Selection([('in', 'From provider to Odoo'), ('out', 'From Odoo to provider')], required=True, string='Flow of data')
    connection_id = fields.Many2one('edi.connection', required=True, on_delete='restrict', string='Connection')
    type = fields.Selection(selection=[], required=True, string='Type') #Add selection for your integration
    parameter = fields.Text(string="Parameter")

    synchronization_content_type = fields.Selection(selection=[
        ('text', 'Text'),
        ('csv', 'CSV'),
        ('xml', 'XML'),
        ('json', 'JSON'),
        ('pdf', 'PDF')
    ], default='text', required=True, readonly=True, string='Content type')

    #res_model_id = fields.Many2one('ir.model', required=True, string='Resource model')

    #post_message_needed = fields.Boolean(string='Post message')
    #post_message_available = fields.Boolean(compute='_compute_post_message_available', string='Messaging available')
    #message_subject = fields.Char(default='EDI Synchronization', string='Subject')
    #error_message_body = fields.Html(default='<p>Error on synchronization!</p>', string='Error content')
    #success_message_body = fields.Html(default='<p>Success on synchronization!</p>', string='Success content')

    provider_name = fields.Char(string='Provider Name')

    #has_synchronizations = fields.Boolean(compute='_compute_has_synchronizations', string='Has synchronizations?')

    # cron inheritance
    cron_id = fields.Many2one('ir.cron', ondelete='restrict', required=True, string='Cron job')
    integration_name = fields.Char(related='cron_id.name', store=True, string='Name')

    # @api.depends('res_model_id')
    # def _compute_post_message_available(self):
    #     """
    #     """

    #     MailThread = self.pool['mail.thread']

    #     for integration in self:

    #         if not integration.res_model_id:
    #             integration.post_message_available = False
    #         else:
    #             integration.post_message_available = issubclass(
    #                 self.pool.get(integration.res_model_id.model),
    #                 MailThread
    #             )


    def _process(self):
        """
        """

        for integration in self:
            if integration.flow_type == in:
                integration.proccess_in()
            else:
                integration.processs_out()
            integration._process_integration()

        return True #TODO make difference between in and out

    def _compute_has_synchronizations(self):
        for integration in self:
            integration.has_synchronizations = bool(self.env['edi.synchronization'].search_count([
                ('integration_id', '=', integration.id)
            ]))


    @api.model
    def create(self, values):
        """
        """
        values['model_id'] = self.env.ref('edi_base.model_edi_integration').id
        values['state'] = "code"

        res = super(Integration, self).create(values)
        res.code = "model._process(%i)" % res.id
        return res

    @api.model
    def _process_integration(self, integration_id):
        self.browse(integration_id)._process()


    @api.multi
    def test_connection(self):
        for integration in self:
            integration.connection_id.test()

    @api.multi
    def open_synchronizations(self):
        self.ensure_one()

        action_dict = self.env.ref('edi_base.synchronizations_act_window').read([])[0]
        ctx = safe_eval(action_dict.pop('context', '{}'))
        ctx.update({
            'default_integration_id': self.id
        })

        action_dict.update({
            'name': _('%s\'s synchronizations') % self.name,
            'domain': [('integration_id', 'in', self.id)],
            'context': ctx
        })

        return action_dict

    @api.model
    def _get_synchronization_values(self):
        return {
            'synchronization_date': self.now.strftime(DEFAULT_SERVER_DATETIME_FORMAT)
        }

    def _get_synchronizations(self):
        return getattr(self, '_get_%s_synchronizations' % self.integration_type)()

    def _get_synchronization_domain(self):
        return [
            ('integration_id', '=', self.id),
            ('res_model_id', '=', self.res_model_id.id),
            ('synchronization_type', '=', self.integration_type),
            ('content_type', '=', self.synchronization_content_type)
        ]

    def _check_post_message(self):
        """
        """

        if not self.post_message_available or not self.post_message_needed:
            return False

        # NOTE: the second statement on the condition is to avoid spamming the
        #       object with error messages.
        #
        #       The rationale is as follows:
        #           if the synchronization failed once, we don't want to post a
        #           error message, even if the error is different (that can be
        #           checked on the synchronization 'error_ids' field)
        fail_stage = self.synchronization_stage_ids.filtered(lambda s: s.state == 'fail')
        return self.activity == 'done' or (self.activity != 'done' and self.synchronization.stage_id != fail_stage)

    def _process_integration(self):
        """
        """

        self.ensure_one()

        synchronizations = self._get_synchronizations()

        # NOTE: When processing outgoing synchronizations, those generated by the
        #       instance, synchronizations are a RecordSet.
        #
        #       On the other hand, when processing incoming synchronizations,
        #       those obtained from a remote service, synchronizations are a list
        #       of 'objects'.
        for s in synchronizations:

            self.synchronization = s

            self.now = dt.utcnow()
            self.synchronization_values = self._get_synchronization_values()

            try:
                self._synchronize()
                self._finalize()
            except Exception:
                self._handle_exception()

    def _clean_synchronization(self):
        """
        """

        self.synchronization_values['stage_id'] = self.synchronization_stage_ids.filtered(lambda s: s.state == 'done').id
        self.synchronization_values['error_ids'] = [(5, 0)]

    def _synchronize(self):
        getattr(self, '_%s_synchronize' % self.integration_type)()

    def _finalize(self):
        """
        Update synchronization with last values obtained while processing it,
        and create a message on related model's chatter if there is a sensible
        change on its synchronization status.
        """

        resource = False
        if not isinstance(self.synchronization, models.BaseModel):
            self.synchronization = self._get_in_synchronization()

        if self.synchronization:
            self.synchronization.write(self.synchronization_values)
            resource = self.env[self.synchronization.res_model].browse(self.synchronization.res_id)

        if self._check_post_message() and resource:

            msg_body = self.success_message_body if self.activity == 'done' else self.error_message_body

            resource.message_post(
                subject=self.message_subject,
                body=msg_body
            )

    def _handle_exception(self):
        """
        """

        error = '\n'.join(traceback.format_exc().splitlines())

        # TODO: Create ir.logging record
        if not isinstance(self.synchronization, models.BaseModel):
            filename = self.synchronization.get('filename')
        else:
            synchronization_error_id = self.env['edi.synchronization.error'].create({
                'synchronization_id': self.synchronization.id,
                'activity': self.activity,
                'description': '\n'.join(traceback.format_exc().splitlines()),
            })

            self.synchronization_values['error_ids'] = [(4, synchronization_error_id.id)]
            self.synchronization_values['state'] = self.synchronization_stage_ids.filtered(lambda s: s.state == 'fail').id

            self.synchronization.write(self.synchronization_values)

            filename = self.synchronization.name

        _logger.error(_('Error while processing synchronization: %s\nActivity: %s\n%s') % (filename, self.activity, error))

        self.connection_id.clean_synchronization(filename, 'failure')

    #########################
    # Incoming Integrations #
    #########################

    def _get_in_synchronizations(self):
        self.activity = 'Fetch synchronizations'
        return self.connection_id.fetch_synchronizations()

    def _get_synchronization_name(self):
        return '%s_%s_%s_%s_integration_%s_synchronization' % (
            self.res_model_id.model.replace('.', '_'),
            self.synchronization_content_type,
            self.integration_type,
            self.now.strftime('%s.%f'),
            self.id
        )

    def _get_create_synchronzation_values(self):

        result = self._get_synchronization_values()

        result.update({
            'create_date': self.now.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
            'integration_id': self.id,
            'name': self._get_synchronization_name(),
            'filename': self.synchronization['filename'],
            'content': self.synchronization['content']
        })

        return result

    def _get_in_synchronization(self):

        Sync = self.env['edi.synchronization']

        domain = self._get_synchronization_domain()
        domain.extend([
            ('filename', '=', self.synchronization['filename']),
            ('stage_id', 'not in', self.synchronization_stage_ids.filtered(lambda s: s.state in ['done', 'cancel']).ids)
        ])

        sync = Sync.search(domain)
        if not sync:
            sync = Sync.with_context(default_integration_id=self.id).create(self._get_create_synchronzation_values())

        return sync

    def _in_synchronize(self):
        """
        """
        self.activity = 'create'
        sync = self._get_in_synchronization()

        self.activity = 'process'
        sync._process(self.synchronization)

    ########################
    # Outgoing Integration #
    ########################

    def _get_out_synchronizations(self):
        self.activity = 'Get synchronizations'

        domain = self._get_synchronization_domain()
        domain.extend([
            ('stage_id', 'not in', self.synchronization_stage_ids.filtered(lambda s: s.state in ['done', 'cancel']).ids)
        ])

        return self.env['edi.synchronization'].search(domain)

    def _out_synchronize(self):
        self.activity = 'send'
        self._send()

        self.activity = 'done'
        self._clean_synchronization()

    def _get_send_values(self):
        return {}

    def _send(self):
        self.connection_id.send_synchronization(
            self.synchronization,
            **self._get_send_values()
        )
