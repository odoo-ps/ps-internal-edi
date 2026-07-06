# Part of Odoo. See LICENSE file for full copyright and licensing details.
import io
import logging
import os
import stat
from base64 import decodebytes

import pysftp
from paramiko import RSAKey

from odoo import api, fields, models
from odoo.addons.edi_base.decorators import IntegrationCheck

_logger = logging.getLogger(__name__)


class SFTPConnection(models.Model):
    """Integration with an SFTP server"""

    _inherit = "edi.connection"

    type = fields.Selection(selection_add=[("sftp", "SFTP")], ondelete={"sftp": "cascade"})

    ####################################################################
    #             Methods overridden from edi_base                      #
    #####################################################################

    @IntegrationCheck("sftp")
    def test(self):
        """Try to connect to the server"""
        self.ensure_one()
        self._ftp_test_connection()

    @IntegrationCheck("sftp")
    def _send_synchronization(self, filename, content, *args, **kwargs):
        """Override to upload the file"""
        self.ensure_one()
        return self._ftp_send_file(filename, content, *args, **kwargs)

    @IntegrationCheck("sftp")
    def _fetch_synchronizations(self, *args, **kwargs):
        """Override to download the file from the FTP server"""
        self.ensure_one()
        return self._ftp_fetch_files(*args, **kwargs)

    @IntegrationCheck("sftp")
    def _clean_synchronization_in(self, data, status, *args, **kwargs):
        self._clean_local_file(data, *args, **kwargs)
        self._clean(data.get("filename"), status, "in", *args, **kwargs)

    @IntegrationCheck("sftp")
    def _clean_synchronization_out(self, filename, status, *args, **kwargs):
        self._clean(filename, status, "out", *args, **kwargs)

    @IntegrationCheck("sftp")
    def _get_default_configuration(self):
        """Provide a configuration template for this type of connection"""
        self.ensure_one()
        return {
            "host": "host",
            "host_key": "",
            "user": "user",
            "password": "password (ignored if key supplied)",
            "key": "",
            "on_conflict": "choose one from : raise, rename, replace",
            "on_conflict_rename_extension": "old",
            "is_active": "False",
            "in_folder": "<PATH HERE>",
            "in_folder_done": "<PATH HERE>",
            "in_folder_error": "<PATH HERE>",
            "out_folder": "<PATH HERE>",
        }

    #####################################################################
    #    Specific SFTP Methods that should be overridden                #
    #    by a connection based on SFTP                                  #
    #####################################################################

    @IntegrationCheck("sftp")
    def connect(self):
        """Open a connection"""
        self.ensure_one()

        config = self._read_configuration()

        cnopts = pysftp.CnOpts()

        host_key_str = config.get("host_key")
        if host_key_str:
            host_key = RSAKey(data=decodebytes(host_key_str.encode()))
            cnopts.hostkeys.add(config.get("host"), "ssh-rsa", host_key)
        else:
            cnopts.hostkeys = None

        key_str = config.get("key", None)
        key = RSAKey.from_private_key(io.StringIO(key_str)) if key_str else None

        server = pysftp.Connection(
            host=config.get("host"),
            username=config.get("user"),
            password=None if key else config.get("password"),
            private_key=key,
            cnopts=cnopts,
            port=config.get("port", 22),
        )

        self.ftp_load_config(server, config)
        return server

    @IntegrationCheck("sftp")
    @api.model
    def pwd(self, server):
        """Get the current directory"""
        return server.pwd

    @IntegrationCheck("sftp")
    @api.model
    def dir_exists(self, server, path):
        """Check if the directory exists"""
        try:
            server.cwd(path)
            return True
        except Exception:
            return False

    @IntegrationCheck("sftp")
    @api.model
    def file_exists(self, server, path, filename):
        """Check if the file exists"""
        return server.exists(os.path.join(path, filename))

    @IntegrationCheck("sftp")
    @api.model
    def delete_file(self, server, path):
        server.remove(path)

    @IntegrationCheck("sftp")
    @api.model
    def change_dir(self, server, path):
        server.chdir(path)

    @IntegrationCheck("sftp")
    @api.model
    def rename(self, server, old, new):
        server.rename(old, new)

    @IntegrationCheck("sftp")
    @api.model
    def list_files(self, server, path=False, filename=False):
        if path:
            self.change_dir(server, path)

        filenames = []
        if filename:  # Fallback to single file check for SFTP if a filename is provided
            if server.exists(filename):
                # Retrieve the attributes of the single file to verify it's a file
                try:
                    attr = server.stat(filename)
                    if stat.S_ISREG(attr.st_mode):
                        filenames.append(filename)
                except Exception:
                    pass
        else:
            for attr in server.listdir_attr():
                if stat.S_ISREG(attr.st_mode):
                    filenames.append(attr.filename)
        return filenames

    @IntegrationCheck("sftp")
    @api.model
    def _upload_file(self, server, filename, binary_content):
        server.putfo(binary_content, filename)

    @IntegrationCheck("sftp")
    @api.model
    def _download_file(self, server, directory, filename):
        server.get(filename, os.path.join(directory, filename))
        return os.path.join(directory, filename)
