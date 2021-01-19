# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
import os
import tarfile
import tempfile
import time

from datetime import datetime
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


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
                                           domain=[('has_sub_integration', '!=', True),
                                                   '|', ('active', '=', True), ('active', '=', False)])
    record_filter_id = fields.Many2one('ir.filters', string="Record Filter", ondelete='restrict',
                                       help="Filter for default behavior of _get_record_to_send")

    # Status
    synchronization_ids = fields.One2many('edi.synchronization', 'integration_id')
    error_ids = fields.One2many('edi.synchronization.error', 'integration_id')
    last_success_date = fields.Datetime()
    last_failure_date = fields.Datetime()
    last_sync_status = fields.Char()
    color = fields.Integer()

    def set_status(self):
        query = """
            SELECT DISTINCT ON (integration_id, state) 
                integration_id, 
                synchronization_date, 
                state 
            FROM edi_Synchronization 
            WHERE integration_id in %s and synchronization_date is not null
            ORDER BY integration_id, state, synchronization_date desc;
        """
        self.env.cr.execute(query, (tuple(self.ids),))
        fail_sync = {}
        done_sync = {}
        for sync in self.env.cr.fetchall():
            if sync[2] == 'fail':
                fail_sync[sync[0]] = fields.Datetime.to_string(sync[1])
            if sync[2] == 'done':
                done_sync[sync[0]] = fields.Datetime.to_string(sync[1])
        for rec in self:
            rec.last_success_date = done_sync.get(rec.id, False)
            rec.last_failure_date = fail_sync.get(rec.id, False)
            rec.last_sync_status = 'No Sync Yet'
            rec.color = 4
            if (rec.last_success_date or datetime(1970, 1, 1)) > (rec.last_failure_date or datetime(1970, 1, 1)):
                rec.last_sync_status = "Success"
                rec.color = 10
            if (rec.last_success_date or datetime(1970, 1, 1)) < (rec.last_failure_date or datetime(1970, 1, 1)):
                rec.last_sync_status = "Fail"
                rec.color = 1

    # TODO Filter on status

    @api.model_create_multi
    def create(self, values):
        for vals in values:
            vals['model_id'] = self.env.ref('edi_base.model_edi_integration').id
            vals['state'] = "code"
            vals['numbercall'] = -1

        res = super(Integration, self).create(values)
        for rec in res:
            rec.code = "model._process(%i)" % rec.id
        return res

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
        res = self.env['edi.synchronization'].create({
            'integration_id': self.id,
            'name': name,
            'filename': '%s.%s' % (name, self.synchronization_content_type),
            'synchronization_date': fields.Datetime.now(),
        })
        res._report_error(activity, exception)
        return res

    def _report_error(self, activity, exception=None, message=None):
        """ 
            Method to use to report error that should not block the process but needs to be reported
            pass exception if you have catch and exception, otherwise pass a message
            If the error should block the process simply raise an error
        """
        if not self.env.fail_safe.env.sync:
            _logger.error("Cannot log error on sync object, sync object is not created yet")
            return

        sync = self.env.fail_safe.env.sync[-1]
        sync._report_error(activity, exception=exception, message=message)

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

    def _create_synchronization_out(self, records, flow_type):
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
        with api.Environment.manage():
            new_cr = self.pool.cursor()
            self.env.fail_safe = self.with_env(self.env(cr=new_cr))
            self.env.fail_safe.env.sync = []
            self.env.fail_safe.env.activity = "Get Record"
            try:
                if not records:
                    records = self._get_record_to_send()
                if self.synchronization_creation == 'one':
                    for rec in records:
                        self._process_record_out(rec, raise_error=raise_error)
                else:
                    self._process_record_out(records, raise_error=raise_error)
            except Exception as e:
                if not 'no_exception_log' in self._context: #Only for test purpose
                    _logger.exception(str(e))
                if not self.env.fail_safe.env.sync:
                    self.env.fail_safe._create_error_sync(self.env.fail_safe.env.activity, e)
                if raise_error:
                    raise
            finally:
                self.env.fail_safe.set_status()
                new_cr.commit()
                new_cr.close()

    def _process_record_out(self, records, raise_error=False):
        """
            new Self has a cursor that should be called to write the status of the sync
        """
        self.ensure_one()

        sync = self.env.fail_safe._create_synchronization_out(records, flow_type=self.integration_flow)
        self.env.fail_safe.env.cr.commit()
        self.env.fail_safe.env.sync.append(sync)
        try:
            self.env.fail_safe.env.activity = "Get Content"
            content = self._get_content(records)
            sync._write_content(content)
            self.env.fail_safe.env.activity = "Send Synchro"
            res = self._send_content(sync.filename, content)
            self._postprocess(res, sync.filename, content, records)
        except Exception as e:
            sync._report_error(self.env.fail_safe.env.activity, e)
            self.env.fail_safe._handle_error(sync.filename)
            if raise_error:
                raise
        else:
            self.flush()
            sync._done()

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
            domain = json.loads(self.record_filter_id.domain.replace("True", "true").replace("False", "false"))
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
        with api.Environment.manage():
            new_cr = self.pool.cursor()
            if self._context.get('no_exception_log'):
                new_cr._default_log_exceptions = False
            self = self.with_env(self.env(cr=new_cr))
            try:
                self._process_out(records=records, raise_error=raise_error)
            except Exception as e:
                raise
            finally:
                new_cr.commit()
                new_cr.close()


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

    def _create_synchronzation_in(self, filename, content):
        return self.env['edi.synchronization'].create({
            'integration_id': self.id,
            'name': self._get_synchronization_name_in(filename, content),
            'filename': filename,
            'synchronization_date': fields.Datetime.now(),
            'content': str(content),
        })

    def _process_in(self, raise_error=False):
        self.ensure_one()
        with api.Environment.manage():
            new_cr = self.pool.cursor()
            self.env.fail_safe = self.with_env(self.env(cr=new_cr))
            self.env.fail_safe.env.sync = []
            self.env.fail_safe.env.activity = "Fetch Content"

            exceptions = []
            try:
                try:  # We make a second try so the "if exceptions" can be done inside the finally
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        data = self._get_in_content(tmp_dir)
                        for d in data:
                            try:
                                self._process_in_files(d, raise_error=raise_error)
                            except Exception as e:  # To allow to continue processing files event if we have errors
                                exceptions.append(e)
                except Exception as e:  # For exceptions when get the content through the connection
                    exceptions.append(e)

                if exceptions:
                    for e in exceptions:
                        if 'no_exception_log' not in self._context:  # Only for test purpose
                            _logger.exception(str(e))
                        if not self.env.fail_safe.env.sync:
                            self.env.fail_safe._create_error_sync(self.env.fail_safe.env.activity, e)
                    if raise_error:
                        raise ValidationError('\n'.join(map(str, exceptions)))

            finally:
                self.env.fail_safe.set_status()
                new_cr.commit()
                new_cr.close()

    def _process_in_files(self, data, raise_error=False):
        """ Before really process a file, check if the file is an archive
         If yes, we can extract the content the process each file
         If no we just process the downloaded file
         """
        self.ensure_one()
        file = data.get('file')

        # If the file is a tar, then untar to process all files insides
        if tarfile.is_tarfile(file):
            data['archive'] = True
            with tempfile.TemporaryDirectory() as tmp_dir:
                # Extract the tar & pass file names to the process file
                start = time.time()
                tar_file = tarfile.open(file)
                tar_file.extractall(tmp_dir)
                _logger.info("%s - untar %s in %.3fs", self.type, data['filename'],time.time() - start)
                files = [os.path.join(path, name) for path, subdirs, files in os.walk(tmp_dir) for name in files]
                data['files'] = files
                self._process_in_file(data, raise_error=raise_error)
        else:
            data['files'] = [file]
            self._process_in_file(data, raise_error=raise_error)

    def _process_in_file(self, data, raise_error=False):
        filename = data.get('filename')
        files = data.get('files')

        # TODO Check if it's ok to never add anymore the content of the file on a synchronization (which was never displayed anyway...)
        content = False #'%s files' % len(files) if files else data.get('content')  # If many files we just log the file names

        sync = self.env.fail_safe._create_synchronzation_in(filename, content)
        self.env.fail_safe.env.cr.commit()
        self.env.fail_safe.env.sync.append(sync)
        try:
            self.env.fail_safe.env.activity = "Process Content"
            start = time.time()
            sub_start = time.time()
            i = 0
            status = 'done'

            # Parse the files (we can have multiple files if we come from an archive)
            for file in files:
                file_status = self.env.fail_safe._process_in_file_or_content(filename, file, data.get('archive'))
                if file_status != 'done':
                    status = file_status
                    continue  # No need to continue since we will rollback

                # Log for huge archives being processed
                i += 1
                if i % 1000 == 0:
                    _logger.info("%s - process %s : %s file(s) in %.3fs, still working",
                                 self.type, filename, i, time.time() - sub_start)
                    sub_start = time.time()

            # Log some execution info
            _logger.info("%s - process %s : %s file(s) in %.3fs, done",
                         self.type, filename, i,  time.time() - start)
            self.env.fail_safe.env.activity = "Clean Synchro"
            self._clean(filename, status)

            # Commit processed files
            # because when clean is done, it means for ex. delete/move a file on a directory,
            # so the transaction has to be validated after the clean to be consistent
            if status == 'done':
                self.env.fail_safe.env.cr.commit()
            else:
                self.env.fail_safe.env.cr.rollback()
            # TODO Evaluate the usage of the return value 'done' :
            #  It seems better to raise an exception if something goes wrong than to return something else than 'done'.
            #  Here the issue is : if we don't return 'done' then we rollback the transaction, BUT we still consider the
            #  synchronization as Done while the "clean" method of the connection will maybe apply an error treatment
            #  (like move a file to an error directory for FTP case) => not very consistent...
        except Exception as e:
            self.env.fail_safe.env.cr.rollback()
            sync._report_error(self.env.fail_safe.env.activity, e)
            self.env.fail_safe._handle_error(sync.filename)
            if raise_error:
                raise
        else:
            self.flush()
            sync._done()

    def _process_in_file_or_content(self, filename, file, archive=False):

        # Add file info in the context (so we don't change the signature of the method _process_content)
        if archive:
            self = self.with_context(archive=filename,
                                     log_file_name=os.path.join(filename, os.path.basename(file)))
        else:
            self = self.with_context(log_file_name=filename)

        if self.in_process_type == 'file':
            return self._process_content(file, None)
        else:
            with open(file, 'r') as f:
                return self._process_content(file, f.read())

    ##################################################
    # Default Behavior: Probably need to reimplement #
    ##################################################

    def _get_synchronization_name_in(self, filename, content):
        return '%s - %s: %s' % (
            self.name,
            fields.Datetime.now(),
            filename
        )

    def _get_in_content(self, directory):
        """
        Return list of dict
        the dict should be {
            'filename': FILENAME (str),
            'content': str or dict: will be handle by in edi.integration._process_data
        }
        """
        return self.connection_id._fetch_synchronizations(integration_id=self, directory=directory)

    def _clean(self, filename, status):
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

    def _handle_error(self, filename):
        """
            Common to both process in and process out
        """
        self.connection_id._clean_synchronization(filename, 'error', self.integration_flow)
