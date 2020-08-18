# -*- encoding: utf-8 -*-

import pysftp
import logging
import sys
import os
import paramiko

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


class SFTPConnection(models.Model):

    _inherit = 'edi.connection'

    type = fields.Selection(selection_add=[('sftp', 'SFTP')])

    def test(self):

        self.ensure_one()
        if not self.type == 'sftp':
            return super().test()

        try:
            conn = self._connect()
            conn.close()
        except Exception as e:
            raise UserError(_('Connection Test Failed! Here is what we got instead:\n %s') % ustr(e))
        else:
            raise UserError(_('Connection Test Succeeded! Everything seems properly set up!'))

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """
        """

        self.ensure_one()
        if not self.type == 'sftp':
            return super()._send_synchronization(filename, content, *args, **kwargs)

        self.sftp_server = None

        config = self._read_configuration()
        on_conflict = config['on_conflict']\
            if 'on_conflict' in config else 'raise'
        on_conflict_rename_extension = config['on_conflict_rename_extension']\
            if 'on_conflict_rename_extension' in config else 'old'

        try:

            self.sftp_server = self._connect()

            try:
                self._check_filename(
                    filename,
                    on_conflict=on_conflict,
                    extension=on_conflict_rename_extension
                )
                self._upload_file(filename, content)
            finally:
                if self.sftp_server is not None:
                    self.sftp_server.close()

        except Exception as e:
            params = (ustr(self.sftp_server), ustr(self.sftp_server.hostkey), e.__class__.__name__, ustr(e))
            msg = _("Sending synchronization failed via FTP server '%s: %s'.\n%s: %s") % params

            _logger.info(msg)

            raise SynchronizationException(_("Failure to send synchronization"), msg)

    def _fetch_synchronizations(self, *args, **kwargs):
        """
        """

        self.ensure_one()
        if not self.type == 'sftp':
            return super()._fetch_synchronizations(*args, **kwargs)

        result = []
        self.sftp_server = None

        try:
            self.sftp_server = self._connect()
            existing_filenames = self.sftp_server.listdir() 

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
                    params = (filename, ustr(self.sftp_server), ustr(self.sftp_server.host), e.__class__.__name__, ustr(e))
                    msg = _("Fetching file '%s' failed via SFTP server '%s: %s'.\n%s: %s") % params

                    _logger.info(msg)
                else:
                    values.update({
                        'filename': filename,
                        'content': content
                    })
                    result.append(values)

        except Exception as e:
            params = (
                ustr(self.sftp_server),
                ustr(self.sftp_server.host),
                e.__class__.__name__,
                ustr(e)
            )
            msg = _("Fetching synchronizations failed via SFTP server '%s: %s'.\n%s: %s") % params

            _logger.info(msg)

        finally:
            if self.sftp_server is not None:
                self.sftp_server.close()
        return result

    def _clean_synchronization(self, filename, status, flow_type, **kwargs):
        """
        """

        self.ensure_one()
        if not self.type == 'sftp':
            return super()._clean_synchronization()

        self.sftp_server = self._connect()
        
        config = self._read_configuration()

        if flow_type == 'out':
            path = "%s/%s" % (config['out_folder'], filename)
            if status == 'error':
                # todo: check if path exists first
                self.sftp_server.delete(path)

        if flow_type == 'in':
            path = "%s/%s" % (config['in_folder'], filename)
            if status == "done":
                done_path = "%s/%s" % (config['in_folder_done'], filename)
            else:
                done_path = "%s/%s" % (config['in_folder_error'], filename)
            # todo : option to delete done files
            # os.rename(path, done_path)

    def _get_default_configuration(self):
        """
            Return a dictionnary
            with the template configuration for this type of connection
        """
        if self.type != 'sftp':
            return super()._get_default_configuration()

        return {
            'host': 'hostname',
            'user': 'user',
            'on_conflict': 'choose one from : raise, rename, replace',
            'on_conflict_rename_extension': 'old',
            'on_clean_integration': 'choose one from : rename, delete',
            'on_clean_integration_rename_extension': 'bak',
            'is_active': 'False',
            'in_folder': 'in_folder',
            'in_folder_done': 'in_folder_done',
            'in_folder_error': 'in_folder_error',
            'out_folder': 'out_folder',
        }

    def _connect(self, integration_flow='in'):

        self.ensure_one()
        if not self.type == 'sftp':
            return super()._connect()

        self.ensure_one()
        privatekeyfile = os.path.expanduser('~/.ssh/id_rsa')

        config = self._read_configuration()
        server = pysftp.Connection(
            host=config['host'],
            username=config['user'],
            private_key=paramiko.RSAKey.from_private_key_file(privatekeyfile)
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
        if self.type == 'sftp':
            existing_filenames = getattr(self.sftp_server, 'mlst', self.sftp_server.nlst)()
            conflicts = set(existing_filenames) & set([filename])

            if not conflicts:
                return

            if conflicts and on_conflict == 'rename':
                self.sftp_server.rename(filename, filename + '.' + extension)
            elif conflicts and on_conflict == 'replace':
                self.sftp_server.delete(filename)
            else:
                raise UserError(_('File \'%s\' already present in SFTP server') % filename)

    @api.model
    def _download_file(self, filename):
        if self.type == 'sftp':
            data = StringIO()
            with self.stfp_server.open(filename, mode='r') as content_file:
                data.write(content_file.read())
            content = data.getvalue()
            data.close()
            return content.decode()

        return super(SFTPConnection, self)._download_file(filename)

    # To update with sftp connection

    @api.model
    def _upload_file(self, filename, content):
        if self.type == 'sftp':
            self.sftp_server.putfo(StringIO(content.encode(), filename))
        else:
            super(SFTPConnection, self)._upload_file(filename, content)

    @api.model
    def _is_valid_filename(self, filename):
        if filename in ['.', '..']:
            return False

        # NOTE: ftplib does not provides a way to differentiate between normal
        #       files and folders, we expect files to have a '.' on its name,
        #       obviously that is a rather random heuristic.
        # fname, _, extension = filename.rpartition('.')
        # if not fname or extension in ['bak', 'old']:
        #     return False

        # return True
        else:
            return self.sftp_server.isfile(filename)
