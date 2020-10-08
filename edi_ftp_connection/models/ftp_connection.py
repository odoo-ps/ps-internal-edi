# -*- encoding: utf-8 -*-

import ftplib
import logging
import sys
import os

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import ustr

PY2 = sys.version_info[0] == 2

if PY2:
    from StringIO import StringIO
else:
    from io import BytesIO as StringIO

_logger = logging.getLogger(__name__)


class SynchronizationException(Exception):
    def __init__(self, name, value=None):
        self.name = name
        self.value = value
        self.args = (name, value)


class FTPConnection(models.Model):

    _inherit = 'edi.connection'

    type = fields.Selection(selection_add=[('ftp', 'FTP')])

    def test(self):

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
        """
        """

        self.ensure_one()
        if not self.type == 'ftp':
            return super()._send_synchronization(filename, content, *args, **kwargs)

        self.ftp_server = None

        config = self._read_configuration()
        on_conflict = config['on_conflict']\
            if 'on_conflict' in config else 'raise'
        on_conflict_rename_extension = config['on_conflict_rename_extension']\
            if 'on_conflict_rename_extension' in config else 'old'

        try:

            self.ftp_server = self._connect()

            try:
                self._check_filename(
                    filename,
                    on_conflict=on_conflict,
                    extension=on_conflict_rename_extension
                )
                self._upload_file(filename, content)
            finally:
                if self.ftp_server is not None:
                    self.ftp_server.quit()

        except Exception as e:
            params = (ustr(self.ftp_server), ustr(self.ftp_server.host), e.__class__.__name__, ustr(e))
            msg = _("Sending synchronization failed via FTP server '%s: %s'.\n%s: %s") % params

            _logger.info(msg)

            raise SynchronizationException(_("Failure to send synchronization"), msg)

    def _fetch_synchronizations(self, *args, **kwargs):
        """
        """

        self.ensure_one()
        if not self.type == 'ftp':
            return super()._fetch_synchronizations(*args, **kwargs)

        result = []
        self.ftp_server = None

        try:
            self.ftp_server = self._connect(integration_flow='in')
            existing_filenames = getattr(self.ftp_server, 'mlst', self.ftp_server.nlst)()

            filenames = []
            for fname in existing_filenames:
                if not self._is_valid_filename(fname):
                    continue
                filenames.append(fname)

            for filename in filenames:
                values = {
                    'filename': False,
                    'content': False
                }
                try:
                    content = self._download_file(filename)
                except Exception as e:
                    params = (filename, ustr(self.ftp_server), ustr(self.ftp_server.host), e.__class__.__name__, ustr(e))
                    msg = _("Fetching file '%s' failed via FTP server '%s: %s'.\n%s: %s") % params

                    _logger.info(msg)
                else:
                    values.update({
                        'filename': filename,
                        'content': content
                    })
                    result.append(values)

        except Exception as e:
            msg = _("Fetching synchronizations failed via FTP server '%s: %s'.\n%s: %s") % (
                ustr(self.ftp_server),
                ustr(self.ftp_server.host) if self.ftp_server else False,
                e.__class__.__name__,
                ustr(e)
            )
            _logger.info(msg)
            raise SynchronizationException(_("Failure to fetch synchronization"), msg)

        finally:
            if self.ftp_server is not None:
                self.ftp_server.quit()
        return result

    def _clean_synchronization(self, filename, status, flow_type, **kwargs):
        """
        """

        self.ensure_one()
        if not self.type == 'ftp':
            return super()._clean_synchronization()

        self.ftp_server = self._connect(integration_flow=flow_type)

        config = self._read_configuration()

        if flow_type == 'out' and status == 'error' and filename in self.ftp_server.nlst():
            self.ftp_server.delete("%s/%s" % (config['out_folder'], filename))

        if flow_type == 'in':
            full_path= self.ftp_server.pwd() + '/' + filename
            if status == "done":
                done_path = full_path.replace('/' + config['in_folder'] + '/', '/' + config['in_folder_done'] + '/')
            else:
                done_path = full_path.replace('/' + config['in_folder'] + '/', '/' + config['in_folder_error'] + '/')
            # todo : option to delete done files
            self.ftp_server.rename(full_path, done_path)

    def _get_default_configuration(self):
        """
            Return a dictionnary
            with the template configuration for this type of connection
        """
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

        self.ensure_one()
        if not self.type == 'ftp':
            return super()._connect()

        self.ensure_one()

        config = self._read_configuration()
        server = ftplib.FTP(
            host=config['host'],
            user=config['user'],
            passwd=config['password']
        )

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
    def _check_filename(self, filename, on_conflict='raise', extension='old'):
        if self.type == 'ftp':
            existing_filenames = getattr(self.ftp_server, 'mlst', self.ftp_server.nlst)()
            conflicts = set(existing_filenames) & set([filename])

            if not conflicts:
                return

            if conflicts and on_conflict == 'rename':
                self.ftp_server.rename(filename, filename + '.' + extension)
            elif conflicts and on_conflict == 'replace':
                self.ftp_server.delete(filename)
            else:
                raise UserError(_('File \'%s\' already present if FTP server') % filename)

    @api.model
    def _download_file(self, filename):
        if self.type == 'ftp':
            data = StringIO()
            self.ftp_server.retrbinary('RETR %s' % filename, data.write)
            content = data.getvalue()
            data.close()
            return content.decode()

        return super(FTPConnection, self)._download_file(filename)

    @api.model
    def _upload_file(self, filename, content):
        if self.type == 'ftp':
            self.ftp_server.storbinary('STOR %s' % filename, StringIO(content.encode()))
        else:
            super(FTPConnection, self)._upload_file(filename, content)

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
