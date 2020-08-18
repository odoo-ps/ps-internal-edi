# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import pysftp
import logging
from io import BytesIO as StringIO
from odoo import fields, models, _
from odoo.exceptions import UserError
from odoo.tools import ustr
_logger = logging.getLogger(__name__)


class SFTPConnection(models.Model):
    """ Integration with an SFTP server """
    _inherit = 'edi.connection'

    type = fields.Selection(selection_add=[('sftp', 'SFTP')], ondelete={'sftp': 'cascade'})

    def _connect(self):
        """ Open a connection on a SFTP server & check for specific flow directories
        Note : in_folder & out_folder fall back on the root directory if they are not defined
        """
        self.ensure_one()
        if not self.type == 'sftp':
            return super()._connect()

        config = self._read_configuration()

        server = False
        try:
            server = pysftp.Connection(
                host=config['host'],
                username=config['user'],
                password=config['password']
                # TODO Allow to connect with RSA key
                # private_key=paramiko.RSAKey.from_private_key_file(privatekeyfile)
            )

            paths = {}
            for folder, default in [('out_folder', '/'),
                                    ('in_folder', '/'), ('in_folder_done', False), ('in_folder_error', False)]:

                # Handle not defined
                path = config.get(folder, default)
                if not path:
                    continue

                # Handle relative path
                if not path.startswith('/'):
                    path = server.pwd + path

                # Handle non existing path
                if not server.exists(path):
                    raise UserError(_('Folder "%s" : "%s" does not exists') % (folder, path))

                # Handle same path for different folders
                if path in paths:
                    raise UserError(_('Try to use path "%s" for folder "%s", but folder "%s" already use this one')
                                    % (path, folder, paths[path]))
                paths[path] = folder

                # Add attribute to the server
                server.__setattr__(folder, path)

            return server
        except Exception as e:
            if server:
                server.close()
            raise e

    def test(self):
        """ Try to connect to the SFTP server """
        self.ensure_one()
        if not self.type == 'sftp':
            return super().test()

        try:
            with self._connect():
                pass
        except Exception as e:
            raise UserError(_('Connection Test Failed! Here is what we got instead:\n %s') % ustr(e))
        else:
            raise UserError(_('Connection Test Succeeded! Everything seems properly set up!'))

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """ Override to upload the file on the SFTP server """
        self.ensure_one()
        if not self.type == 'sftp':
            return super()._send_synchronization(filename, content, *args, **kwargs)

        with self._connect() as server:
            try:
                server.chdir(server.out_folder)
                self._manage_conflict(server, filename)
                server.putfo(StringIO(content.encode()), filename)
            except Exception as e:
                _logger.error(e)
                raise UserError(_('Send synchronization failed for file %s:\n%s') % (filename, ustr(e)))

    def _fetch_synchronizations(self, *args, **kwargs):
        """ Override to download the file from the SFTP server """
        self.ensure_one()
        if not self.type == 'sftp':
            return super()._fetch_synchronizations(*args, **kwargs)

        result = []
        with self._connect() as server:
            server.chdir(server.in_folder)
            for filename in server.listdir():
                if not server.isfile(filename):
                    continue
                try:
                    data = StringIO()
                    with server.open(filename, mode='r') as content_file:
                        data.write(content_file.read())
                    content = data.getvalue()
                    data.closes()
                    result.append({
                        'filename': filename,
                        'content': content.decode()
                    })
                except Exception as e:
                    _logger.error(e)
                    raise UserError(_('Fetch synchronization failed for file %s:\n%s') % (filename, ustr(e)))
        return result

    def _clean_synchronization(self, filename, status, flow_type, **kwargs):
        """ Override to take actions after the file transfer
        - out : delete the file if an error has occurred
        - in :
            - if done : move if done folder is defined, else delete
            - if error : move if error folder is defined, else just let the file
        """
        if not self.type == 'sftp':
            return super()._clean_synchronization()

        self.ensure_one()
        if flow_type == 'in' or \
           flow_type == 'out' and status == 'error':  # Test 1st to avoid a connection if not necessary
            with self._connect() as server:

                # out : delete file if an error occurred
                if flow_type == 'out':
                    path = '%s/%s' % (server.out_folder, filename)
                    if server.exists(path):
                        server.remove(path)

                # in : move or delete when done, move to error folder if defined
                else:
                    old_path = '%s/%s' % (server.in_folder, filename)
                    if status == 'done':
                        if hasattr(server, 'in_folder_done'):
                            server.rename(old_path, '%s/%s' % (server.in_folder_done, filename))
                        else:
                            server.remove(old_path)
                    elif hasattr(server, 'in_folder_error'):
                        server.rename(old_path,  '%s/%s' % (server.in_folder_error, filename))

    def _get_default_configuration(self):
        """ Provide a configuration template for this type of connection """
        if self.type != 'sftp':
            return super()._get_default_configuration()

        return {
            'host': 'host',
            'user': 'user',
            'password': 'password',
            'is_active': 'False',
            'out_folder': '<PATH HERE>, "/" if not defined',
            'in_folder': '<PATH HERE>, "/" if not defined',
            'in_folder_done': '<PATH HERE>, deleted if not defined',
            'in_folder_error': '<PATH HERE>, left in the same place of not defined',
            'on_conflict': 'choose one from (raise, rename, replace), default raise',
            'on_conflict_rename_extension': 'old',
            # 'on_clean_integration': 'choose one from : rename, delete',
            # 'on_clean_integration_rename_extension': 'bak',
        }

    def _manage_conflict(self, server, path):
        """ Check if the file already exists, manage the conflict by rename, replace or raise """
        if not server.exists(path):
            return

        config = self._read_configuration()
        on_conflict = config.get('on_conflict', 'raise')
        if on_conflict == 'rename':
            server.rename(path, '%s.%s.%s' % (path,
                                              fields.Datetime.now().strftime('%Y%m%d%H%M%S'),
                                              config.get('on_conflict_rename_extension', 'old')))
        elif on_conflict == 'replace':
            server.remove(path)
        else:
            raise UserError(_('File \'%s\' already present on SFTP server') % path)
