# -*- encoding: utf-8 -*-
import ftplib
import logging
from io import BytesIO as StringIO
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import ustr
_logger = logging.getLogger(__name__)


class SynchronizationException(Exception):
    def __init__(self, name, value=None):
        self.name = name
        self.value = value
        self.args = (name, value)


class FTPConnection(models.Model):
    """ Integration with an FTP server """
    _inherit = 'edi.connection'

    type = fields.Selection(selection_add=[('ftp', 'FTP')], ondelete={'ftp': 'cascade'})

    def test(self):
        """ Try to connect to the FTP server """
        self.ensure_one()
        if not self.type == 'ftp':
            return super().test()

        try:
            conn = self._connect()
            conn.quit()
        except Exception as e:
            raise UserError(_('Connection Test Failed! Here is what we got instead:\n %s') % ustr(e))
        else:
            raise UserError(_('Connection Test Succeeded! Everything seems properly set up!'))

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """ Override to upload the file on the FTP server """
        self.ensure_one()
        if not self.type == 'ftp':
            return super()._send_synchronization(filename, content, *args, **kwargs)

        ftp_server = None
        try:
            ftp_server = self._connect()
            try:
                self._check_filename(ftp_server, filename)
                ftp_server.storbinary('STOR %s' % filename, StringIO(content.encode()))
            finally:
                if ftp_server is not None:
                    ftp_server.quit()

        except Exception as e:
            msg = _("Sending synchronization failed via FTP server '%s: %s'.\n%s: %s") % (
                ustr(ftp_server),
                ustr(ftp_server.host) if ftp_server else False,
                e.__class__.__name__,
                ustr(e)
            )
            _logger.info(msg)
            raise SynchronizationException(_("Failure to send synchronization"), msg)

    def _fetch_synchronizations(self, *args, **kwargs):
        """ Override to download the file from the FTP server """
        self.ensure_one()
        if not self.type == 'ftp':
            return super()._fetch_synchronizations(*args, **kwargs)

        result = []
        ftp_server = False
        try:
            ftp_server = self._connect(integration_flow='in')
            existing_filenames = getattr(ftp_server, 'mlst', ftp_server.nlst)()

            filenames = []
            for fname in existing_filenames:
                if not self._is_valid_filename(fname):
                    continue
                filenames.append(fname)

            for filename in filenames:
                try:
                    data = StringIO()
                    ftp_server.retrbinary('RETR %s' % filename, data.write)
                    content = data.getvalue()
                    data.close()
                    result.append({
                        'filename': filename,
                        'content': content.decode()
                    })
                except Exception as e:
                    _logger.info(_("Fetching file '%s' failed via FTP server '%s: %s'.\n%s: %s") % (
                        filename, ustr(ftp_server), ustr(ftp_server.host), e.__class__.__name__, ustr(e)))

        except Exception as e:
            msg = _("Fetching synchronizations failed via FTP server '%s: %s'.\n%s: %s") % (
                ustr(ftp_server),
                ustr(ftp_server.host) if ftp_server else False,
                e.__class__.__name__,
                ustr(e)
            )
            _logger.info(msg)
            raise SynchronizationException(_("Failure to fetch synchronization"), msg)

        finally:
            if ftp_server:
                ftp_server.quit()
        return result

    def _clean_synchronization(self, filename, status, flow_type, **kwargs):
        """ Override to take actions after the file transfer
        - out : delete the file if an error has occurred
        - in : move in in_done or in_error
        """
        self.ensure_one()
        if not self.type == 'ftp':
            return super()._clean_synchronization()

        ftp_server = self._connect(integration_flow=flow_type)
        config = self._read_configuration()

        if flow_type == 'out' and status == 'error' and filename in ftp_server.nlst():
            ftp_server.delete("%s/%s" % (config['out_folder'], filename))

        if flow_type == 'in':
            full_path = ftp_server.pwd() + '/' + filename
            if status == "done":
                done_path = full_path.replace('/' + config['in_folder'] + '/', '/' + config['in_folder_done'] + '/')
            else:
                done_path = full_path.replace('/' + config['in_folder'] + '/', '/' + config['in_folder_error'] + '/')
            # todo : option to delete done files
            ftp_server.rename(full_path, done_path)

    def _get_default_configuration(self):
        """ Provide a configuration template for this type of connection """
        if self.type != 'ftp':
            return super()._get_default_configuration()

        return {
            'host': 'host',
            'user': 'user',
            'password': 'password',
            'on_conflict': 'choose one from : raise, rename, replace',
            'on_conflict_rename_extension': 'old',
            # 'on_clean_integration': 'choose one from : rename, delete',
            # 'on_clean_integration_rename_extension': 'bak',
            'is_active': 'False',
            'in_folder': '<PATH HERE>',
            'in_folder_done': '<PATH HERE>',
            'in_folder_error': '<PATH HERE>',
            'out_folder': '<PATH HERE>',
        }

    def _connect(self, integration_flow='out'):
        """ Open a connection on a FTP server """
        self.ensure_one()
        if not self.type == 'ftp':
            return super()._connect()

        config = self._read_configuration()
        server = ftplib.FTP(host=config['host'],
                            user=config['user'],
                            passwd=config['password'])

        if 'is_active' in config and config['is_active'] == 'True':
            server.set_pasv(False)

        if integration_flow == 'in':
            if 'in_folder' in config:
                server.cwd(config['in_folder'])
        else:
            if 'out_folder' in config:
                server.cwd(config['out_folder'])

        return server

    @api.model
    def _check_filename(self, ftp_server, filename):
        """ Check if the file already exists, and rename or replace if it's the case """
        if self.type == 'ftp':
            config = self._read_configuration()
            on_conflict = config.get('on_conflict', 'raise')
            extension = config.get('on_conflict_rename_extension', 'old')

            existing_filenames = getattr(ftp_server, 'mlst', ftp_server.nlst)()
            conflicts = set(existing_filenames) & set([filename])

            if not conflicts:
                return

            if conflicts and on_conflict == 'rename':
                ftp_server.rename(filename, filename + '.' + extension)
            elif conflicts and on_conflict == 'replace':
                ftp_server.delete(filename)
            else:
                raise UserError(_('File \'%s\' already present if FTP server') % filename)

    @api.model
    def _is_valid_filename(self, filename):
        if filename in ['.', '..']:
            return False

        # NOTE: ftplib does not provides a way to differentiate between normal
        #       files and folders, we expect files to have a '.' on its name,
        #       obviously that is a rather random heuristic.
        fname, _, extension = filename.rpartition('.')
        if not fname or extension in ['bak', 'old']:
            return False

        return True
