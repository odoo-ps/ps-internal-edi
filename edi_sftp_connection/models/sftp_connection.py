# Part of Odoo. See LICENSE file for full copyright and licensing details.
import io
import logging
import os
from base64 import decodebytes

from paramiko import RSAKey
import pysftp

from odoo import api, fields, models


_logger = logging.getLogger(__name__)


class SFTPConnection(models.Model):
    """Integration with an SFTP server"""

    _inherit = "edi.connection"

    type = fields.Selection(selection_add=[("sftp", "SFTP")], ondelete={"sftp": "cascade"})

    ####################################################################
    #             Methods overridden from edi_base                      #
    #####################################################################

    def test(self):
        """Try to connect to the server"""
        self.ensure_one()
        if not self.type == "sftp":
            return super().test()

        self._ftp_test_connection()

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """Override to upload the file"""
        self.ensure_one()
        if not self.type == "sftp":
            return super()._send_synchronization(filename, content, *args, **kwargs)

        return self._ftp_send_file(filename, content, *args, **kwargs)

    def _fetch_synchronizations(self, *args, **kwargs):
        """Override to download the file from the FTP server"""
        self.ensure_one()
        if not self.type == "sftp":
            return super()._fetch_synchronizations(*args, **kwargs)

        return self._ftp_fetch_files(*args, **kwargs)

    def _clean_synchronization_in(self, data, status, *args, **kwargs):
        if not self.type == "sftp":
            return super()._clean_synchronization_in(data, status, *args, **kwargs)

        self._clean_local_file(data, *args, **kwargs)
        self._clean(data.get("filename"), status, "in", *args, **kwargs)

    def _clean_synchronization_out(self, filename, status, *args, **kwargs):
        if not self.type == "sftp":
            return super()._clean_synchronization_out(filename, status, *args, **kwargs)

        self._clean(filename, status, "out", *args, **kwargs)

    def _get_default_configuration(self):
        """Provide a configuration template for this type of connection"""
        if self.type != "sftp":
            return super()._get_default_configuration()

        return self._sftp_default_configuration()

    #####################################################################
    #    Specific SFTP Methods that should be overridden                #
    #    by a connection based on SFTP                                  #
    #####################################################################

    def connect(self):
        """Open a connection"""
        self.ensure_one()
        if not self.type == "sftp":
            return super().connect()

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

    @api.model
    def pwd(self, server):
        """Get the current directory"""
        if not self.type == "sftp":
            return super().pwd(server)

        return server.pwd

    @api.model
    def dir_exists(self, server, path):
        """Check if the directory exists"""
        if not self.type == "sftp":
            return super().dir_exists(server, path)

        try:
            server.cwd(path)
            return True
        except Exception:
            return False

    @api.model
    def file_exists(self, server, path, filename):
        """Check if the file exists"""
        if not self.type == "sftp":
            return super().file_exists(server, path, filename)

        for file in self.list_files(server, path):
            if file == filename:
                return True
        return False

    @api.model
    def delete_file(self, server, path):
        if not self.type == "sftp":
            return super().delete_file(server, path)

        server.remove(path)

    @api.model
    def change_dir(self, server, path):
        if not self.type == "sftp":
            return super().change_dir(server, path)

        server.chdir(path)

    @api.model
    def rename(self, server, old, new):
        if not self.type == "sftp":
            return super().rename(server, old, new)

        server.rename(old, new)

    @api.model
    def list_files(self, server, path=False):
        if not self.type == "sftp":
            return super().list_files(server, path)

        if path:
            self.change_dir(server, path)

        names = server.listdir()
        filenames = []
        for name in names:
            if not server.isfile(name):
                continue
            filenames.append(name)
        return filenames

    @api.model
    def _upload_file(self, server, filename, binary_content):
        if not self.type == "sftp":
            return super()._upload_file(server, filename, binary_content)

        server.putfo(binary_content, filename)

    @api.model
    def _download_file(self, server, directory, filename):
        if not self.type == "sftp":
            return super()._download_file(server, directory, filename)

        server.get(filename, os.path.join(directory, filename))
        return os.path.join(directory, filename)

    @api.model
    def _sftp_default_configuration(self):
        return {
            "host": "host",
            "host_key": "",
            "user": "user",
            "password": "password (ignored if key supplied)",
            "key": "",
            "on_conflict": "choose one from : raise, rename, replace",
            "on_conflict_rename_extension": "old",
            "in_folder": "<PATH HERE>",
            "in_folder_done": "<PATH HERE>",
            "in_folder_error": "<PATH HERE>",
            "out_folder": "<PATH HERE>",
        }
