# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import ast
import json
import logging

from datetime import datetime
from odoo import api, fields, models, registry, _
from odoo.tools.safe_eval import safe_eval


_logger = logging.getLogger(__name__)


class ProcessIntegrationException(Exception):

    def __init__(self, name, value=None):
        self.name = name
        self.value = value
        self.args = (name, value)


class Integration(models.Model):

    _name = 'edi.integration'
    _description = 'Integration to process by Odoo instance'
    _inherits = {'ir.cron': 'cron_id'}
    _order = 'sequence'

    integration_flow = fields.Selection([
        ('in', 'From provider to Odoo'), 
        ('out', 'From Odoo to provider'), 
        ('out_real', 'From Odoo to provider (Realtime)')
    ], required=True, string='Flow of data')
    synchronization_creation = fields.Selection([('one', 'One'), ('multi', 'Multi')],
                                                help="Create a synchro for each record (one), or for all record multi",
                                                default="multi")
    connection_id = fields.Many2one('edi.connection', required=True, string='Connection')
    type = fields.Selection(selection=[('multi', 'Call Sub Integration'),('api', 'RPC Api')],
                            required=True,
                            string='Type')  # Add selection for your integration
    parameter = fields.Text(string="Parameter")

    synchronization_content_type = fields.Selection(selection=[
        ('text', 'Text'),
        ('csv', 'CSV'),
        ('xml', 'XML'),
        ('json', 'JSON'),
        ('pdf', 'PDF')
    ], default='text', required=True, string='Content type')
    in_process_type = fields.Selection([('content', 'Content of the file'), ('file', 'File')],
                                       default='content',
                                       help="""
For integrations from provider to Odoo only :

- If "content" then the "_process_content" method will receive the content of the file in text mode
- If "file" then "_process_content" method will receive the file and has to open it and load the content

Interesting if you want to load huge files with a stream parser for example.

Default is content.""")

    # cron inheritance
    cron_id = fields.Many2one('ir.cron', ondelete='restrict', required=True, string='Cron job')
    # Multiple Integration at once
    has_sub_integration = fields.Boolean(string="Has sub Integration", default=False, help="if you need to run many integration in a specific order in the same transaction" )
    sequence = fields.Integer()
    sub_integration_ids = fields.Many2many('edi.integration',
                                           'edi_integration_sub_integration_rel',
                                           'integration_id', 'sub_integration_id',
                                           domain=[('has_sub_integration', '!=', True)],
                                           context={'active_test': False})
    record_filter_id = fields.Many2one('ir.filters', string="Record Filter", ondelete='restrict',
                                       help="Filter for default behavior of _get_record_to_send")

    # Status
    synchronization_ids = fields.One2many('edi.synchronization', 'integration_id')
    error_ids = fields.One2many('edi.synchronization.error', 'integration_id')
    last_success_date = fields.Datetime()
    last_failure_date = fields.Datetime()
    last_sync_status = fields.Char(default='No Sync Yet')
    color = fields.Integer()

    def _set_status(self):

        sync_datas = []

        # NOTE: `edi.synchronization`'s are always created on a different cursor
        #        thus we need to open a new one
        with registry(self.env.cr.dbname).cursor() as new_cr:

            new_cr.execute("""
                SELECT DISTINCT ON (integration_id, state)
                       integration_id,
                       synchronization_date,
                       state
                  FROM edi_synchronization
                 WHERE integration_id in %s
                   AND synchronization_date IS NOT NULL
                 ORDER BY integration_id, state, synchronization_date DESC
            """, (tuple(self.ids),))
            sync_datas = new_cr.dictfetchall()

        done_sync = {}
        fail_sync = {}

        for sync_data in sync_datas:

            state = sync_data['state']

            if state not in ('done', 'fail'):
                continue

            dest = fail_sync if state == 'fail' else done_sync
            opp = done_sync if state == 'fail' else fail_sync

            integration_id = sync_data['integration_id']
            sync_date = fields.Datetime.from_string(sync_data['synchronization_date'])

            # NOTE: Due to the SQL query, `done` state synchronization is always
            #       before a possible failed one, so if the already succeeded
            #       synchronization is older, we empty the dict.
            if (
                state == 'fail' and
                integration_id in opp and
                opp[integration_id] <= sync_date
            ):
                opp[integration_id] = False

            dest[integration_id] = sync_date

        for record in self:

            vals = {
                'last_sync_status': 'No Sync Yet',
                'color': 4
            }

            last_success_date = done_sync.get(record.id, False)
            last_failure_date = fail_sync.get(record.id, False)

            if last_success_date:
                vals['last_success_date'] = last_success_date

            if last_failure_date:
                vals['last_failure_date'] = last_failure_date

            if last_success_date or last_failure_date:

                # NOTE: If the last synchronization is a success, we consider the
                #       integration as succeeded
                if last_success_date and (last_failure_date or datetime(1970, 1, 1)) <= last_success_date:
                    vals.update({
                        'last_sync_status': 'Success',
                        'color': 10
                    })

                # NOTE: If the last synchronization is a failure, we consider the
                #       integration as failed
                elif last_failure_date and (last_success_date or datetime(1970, 1, 1)) <= last_failure_date:
                    vals.update({
                        'last_sync_status': 'Fail',
                        'color': 1
                    })

            # NOTE: Reset values
            else:
                vals.update({
                    'last_success_date': False,
                    'last_failure_date': False
                })

            record.write(vals)

    @api.model_create_multi
    def create(self, values):

        for vals in values:
            vals.update({
                'model_id': self.env.ref('edi_base.model_edi_integration').id,
                'state': 'code',
                'numbercall': -1
            })

        integrations = super().create(values)

        for integration, vals in zip(integrations, values):

            if 'code' in vals:
                continue

            integration.code = 'model._process(%i)' % integration.id

        return integrations

    def _load_records(self, data_list, update=False):

        result = super()._load_records(data_list, update=update)

        if self._name != 'edi.integration':
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

        IMD = self.env['ir.model.data']

        crons = result.mapped('cron_id')
        server_actions = crons.mapped('ir_actions_server_id')

        server_action_imds = IMD.search([
            ('model', '=', 'ir.actions.server'),
            ('res_id', 'in', server_actions.ids)
        ])
        if len(server_action_imds) == len(result):
            return result

        server_action_imds_res_ids = server_action_imds.mapped('res_id')

        cron_imds = IMD.search([
            ('model', '=', 'ir.cron'),
            ('res_id', 'in', crons.ids)
        ])

        imd_data_list = []
        for server_action in server_actions:
            if server_action.id in server_action_imds_res_ids:
                continue

            cron = crons.filtered(lambda c: c.ir_actions_server_id == server_action)
            imd = cron_imds.filtered(lambda imd: imd.res_id == cron.id)
            imd_data_list.append({
                'xml_id': f'{imd.module}.{imd.name}_ir_actions_server',
                'record': server_action,
                'noupdate': True
            })

        IMD._update_xmlids(imd_data_list, update=update)

        return result

    def _read_parameter(self):
        self.ensure_one()
        return json.loads(self.parameter)

    def test_connection(self):
        for integration in self:
            integration.connection_id.test()

    def open_synchronizations(self):
        self.ensure_one()

        action_dict = self.env.ref('edi_base.synchronizations_act_window').read([])[0]
        ctx = safe_eval(action_dict.pop('context', '{}'))
        ctx.update({
            'default_integration_id': self.id
        })

        action_dict.update({
            'name': _('%s\'s synchronizations') % self.name,
            'domain': [('integration_id', 'in', [self.id] + self.sub_integration_ids.ids)],
            'context': ctx
        })

        return action_dict

    ###########################################
    #             Generic API                 #
    ###########################################
    #=========================================#

    def _create_error_sync(self, activity, exception):
        name = '%s - %s: %s' % (self.name, fields.Datetime.now(), "No Sync Error")
        synchronization = self.env['edi.synchronization'].create({
            'integration_id': self.id,
            'name': name,
            'filename': '%s.%s' % (name, self.synchronization_content_type),
            'synchronization_date': fields.Datetime.now(),
        })
        synchronization._report_error(activity, exception=exception)
        return synchronization

    def _report_error(self, activity, exception=None, message=None):
        """ 
            Method to use to report error that should not block the process but needs to be reported
            pass exception if you have catch and exception, otherwise pass a message
            If the error should block the process simply raise an error
        """

        if self.env.synchronizations:
            self.env.synchronizations[-1]._report_error(activity, exception=exception, message=message)
            return

        _logger.error("Cannot log error on sync object, sync object is not created yet")


    @api.model
    def _process(self, integration_id):
        """
            Entry point for cron, don't raise error
        """
        return self.browse(integration_id).process_integration()

    def process_integration(self):
        """
            Default raise_error=True if call from button for testing purpose
        """
        raise_error = self._context.get('raise_error', False)
        for integration in self:

            # Force to execute with the scheduled user (if we execute it from the interface)
            integration = integration.with_user(integration.user_id)

            if integration.sub_integration_ids:
                integration.sub_integration_ids.process_integration()
            else:
                if integration.integration_flow == "in":
                    integration._process_in(raise_error=raise_error)
                elif integration.integration_flow == "out":
                    integration._process_out(raise_error=raise_error)
                else:
                    _logger.warning("Do not call process_integration for real time integration call _process_out_realtime")

        return True

    #####################################################################
    #                   Implementation of process out                   #
    #####################################################################
    #===================================================================#

    """
    FLOW OUT
    ========

    Flow out:
       _get_record to send #DEFAULT
       try:
            if one
                for each record
                    _get_synchronization_name_out: #DEFAULT
                    _get_content  #TO IMPLEMENT
                    _send_content  #DEFAULT
                    _postprocess #DEFAULT
            if multi
                _get_synchronization_name_out: #DEFAULT
                _get_content    #TO IMPLEMENT
                _send_content  #DEFAULT
                _postprocess #DEFAULT
        except:
            _handle_error  #DEFAULT
    """

    def _create_synchronization_out(self, records):
        name = self._get_synchronization_name_out(records)
        return self.env['edi.synchronization'].create({
            'integration_id': self.id,
            'name': name,
            'filename': ('%s.%s' % (name[:100], self.synchronization_content_type)),
            'synchronization_date': fields.Datetime.now(),
        })

    def _process_out(self, records=None, raise_error=False):
        """
            with raise_error=True if you want to get the traceback and stop the iteration
        """

        self.ensure_one()

        self.env.synchronizations = []
        self.env.activity = "Get Record"

        try:
            if not records:
                records = self._get_record_to_send()

            if not records:
                _logger.info('No records found to synchronize for %s [%s]', self.name, self.id)
                return

            if self.synchronization_creation != 'one':
                self._process_record_out(records, raise_error=raise_error)
            else:
                for rec in records:
                    self._process_record_out(rec, raise_error=raise_error)

        except Exception as e:

            if not 'no_exception_log' in self._context: #Only for test purpose
                _logger.exception(str(e))

            if not self.env.synchronizations:

                with registry().cursor() as new_cr:
                    self.with_env(api.Environment(
                        new_cr,
                        self.env.user.id,
                        self.env.context
                    ))._create_error_sync(self.env.activity, e)

            if raise_error:
                raise
        finally:
            self._set_status()

    def _process_record_out(self, records, raise_error=False):
        """
            new Self has a cursor that should be called to write the status of the sync
        """
        self.ensure_one()

        new_cr = registry(self.env.cr.dbname).cursor()
        new_env = api.Environment(
            new_cr,
            self.env.user.id,
            self.env.context
        )

        sync = self.with_env(new_env)._create_synchronization_out(records)
        self.env.synchronizations.append(sync)

        try:

            with self.env.cr.savepoint():

                self.env.activity = "Get Content"
                content = self._get_content(records)
                sync._write_content(content)

                self.env.activity = "Send Synchro"
                res = self._send_content(sync.filename, content)

                self.env.activity = "Postprocess"
                self._postprocess(res, sync.filename, content, records)

        except Exception as e:

            sync._report_error(self.env.activity, e)
            self.with_env(new_env)._handle_error(records, sync, e)

            if raise_error:
                raise

        else:
            self.flush()
            sync._done()

        finally:
            new_cr.commit()
            new_cr.close()

    ##################################################
    # Default Behavior: Probably need to reimplement #
    ##################################################

    def _get_synchronization_name_out(self, records):
        return '%s - %s: %s' % (
            self.name,
            fields.Datetime.now(),
            records.ids
        )

    def _get_record_to_send(self):
        """
            To implement in each integration
            if not self.type == 'My type':
                return super()._get_record_to_send()
            ....
            Return the list of record use to generate the content
        """
        if self.record_filter_id:
            domain = ast.literal_eval(self.record_filter_id.domain)
            return self.env[self.record_filter_id.model_id].search(domain)
        return self.browse()

    def _send_content(self, filename, content):
        """
            Standard behavior can be overwritte if needed
            can use self._report_error
        """
        res = self.connection_id._send_synchronization(filename, content)
        self.connection_id._clean_synchronization(filename, 'done', self.integration_flow)
        return res

    def _postprocess(self, send_result, filename, content, records):
        """
            Standard behavior can be overwritte if needed
            Do nothing
        """
        return

    ################################
    # To implement for process out #
    ################################

    def _get_content(self, records):
        """
            To implement in each integration
            if not self.type == 'My type':
                return super()._get_record_to_send()
            ....
            Return a string or an dict with the content to synchronized will be passed to send
            Can use self._report_error
        """
        return ""

    #####################################################################
    #                Implementation of process out Realtime             #
    #####################################################################
    #===================================================================#

    def _process_out_realtime(self, records, raise_error=False):
        """
            Same as process out but we assume the trigger does not come from a cron
            but any method in odoo and that method is already aware of the records
            to synchronize. 
            use a new cursor to synchronize, so if it fail it does not affect the 
            the rest of the transaction
            set raise_error=True if you don't want to have the synchronization
            to fail silently.
        """
        self.ensure_one()

        self.flush()

        no_exception_log = self._context.get('no_exception_log', False)

        self.env.cr._default_log_exceptions = no_exception_log

        try:
            self._process_out(records=records, raise_error=raise_error)
        except Exception:
            if raise_error:
                raise

        self.env.cr._default_log_exceptions = not no_exception_log

    #####################################################################
    #                   Implementation of process in                    #
    #####################################################################
    #===================================================================#

    """
    FLOW IN
    ========

    Flow in:

    try:
            _get_in_content  #DEFAULT

            for each record
                _get_synchronization_name_in: #DEFAULT
                _process_content  #TO IMPLEMENT
                _clean   #DEFAULT
        except:
            _handle_error  #DEFAULT
    """

    def _create_synchronization_in(self, filename, content):
        return self.env['edi.synchronization'].create({
            'integration_id': self.id,
            'name': self._get_synchronization_name_in(filename, content),
            'filename': filename,
            'synchronization_date': fields.Datetime.now(),
            'content': str(content),
        })

    def _process_in(self, raise_error=False):

        self.ensure_one()

        self.env.synchronizations = []
        self.env.activity = "Fetch Content"

        exceptions = []

        try:
            data = self._get_in_content()
        except Exception as e:
            with registry().cursor() as new_cr:
                self.with_env(api.Environment(
                    new_cr,
                    self.env.user.id,
                    self.env.context
                ))._create_error_sync(self.env.activity, e)
            exceptions.append(e)
        else:
            for d in data:
                try:
                    self._process_in_file(d['filename'], d['content'], raise_error=raise_error)
                except Exception as e:
                    exceptions.append(e)
        finally:
            # NOTE: Update integration's status after processing all files
            self._set_status()

            if 'no_exception_log' not in self._context:  # Only for test purpose
                for e in exceptions:
                    _logger.exception(str(e))

            if exceptions and raise_error:
                raise ProcessIntegrationException('\n'.join(map(str, exceptions)))

    def _process_in_file(self, filename, content, raise_error=False):

        self.ensure_one()

        new_cr = registry(self.env.cr.dbname).cursor()
        new_env = api.Environment(
            new_cr,
            self.env.user.id,
            self.env.context
        )

        sync = self.with_env(new_env)._create_synchronization_in(filename, content)
        self.env.synchronizations.append(sync)

        try:

            with self.env.cr.savepoint():

                self.env.activity = "Process Content"
                status = self._process_content(filename, content)

                self.env.activity = "Clean Synchro"
                self._clean(filename, status, content)

        except Exception as e:

            sync._report_error(self.env.activity, e)
            self.with_env(new_env)._handle_error(None, sync, e)

            if raise_error:
                raise

        else:
            self.flush()
            sync._done()

        finally:
            new_cr.commit()
            new_cr.close()

    ##################################################
    # Default Behavior: Probably need to reimplement #
    ##################################################

    def _get_synchronization_name_in(self, filename, content):
        return '%s - %s: %s' % (
            self.name,
            fields.Datetime.now(),
            filename
        )

    def _get_in_content(self):
        """
        Return list of dict
        the dict should be {
            'filename': FILENAME (str),
            'content': str or dict: will be handle by in edi.integration._process_data
        }
        """
        return self.connection_id._fetch_synchronizations()

    def _clean(self, filename, status, content):
        return self.connection_id._clean_synchronization(filename, status, self.integration_flow)

    ################################
    # To implement for process in  #
    ################################
    def _process_content(self, filename, content):
        """ Allow the integration to redefine the processing of the content

        :param filename:
            the full path of the name
            (which is now always in a temporary directory during the processing)
        :param content:
            the content of the file if in_process_type == 'content'
            if in_process_type == 'file' it means that :
                - the content is not passed to the method (so content = None)
                - we can use the filename to open the file
        :return:
            status use by _clean

        Can use self._report_error

        Note : 2 keys are added in the context just before the call of this method :
            - archive : if we process a file coming from an archive, the name of the archive file, ex. products.tar
            - log_file_name :
                - if archive, the concat of the archive & file processing, ex. products.tar/product-xx.xml
                - else just the base filename product-xx.xml
            Useful for traceability (log, save info in DB...)
        """
        return "done"

    ##########################################################
    # Common default Behavior: Probably need to reimplement  #
    ##########################################################
    #========================================================#

    def _handle_error(self, records, sync, exc):
        """
            Common to both process in and process out
        """
        self.connection_id._clean_synchronization(sync.filename, 'error', self.integration_flow)
