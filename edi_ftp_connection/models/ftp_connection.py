import ftplib
import logging
import os
import shutil
import tempfile
from io import BytesIO as StringIO

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import ustr

_logger = logging.getLogger(__name__)


class SynchronizationException(Exception):
    def __init__(self, name, value=None):
        self.name = name
        self.value = value
        self.args = (name, value)


class FTPConnection(models.Model):
    """Integration with an FTP server"""

    _inherit = "edi.connection"

    type = fields.Selection(selection_add=[("ftp", "FTP")], ondelete={"ftp": "cascade"})
    ftp_in_done_let = fields.Boolean(
        'Let in "in_folder"', help="When downloading, let the file on the folder when process is done"
    )
    ftp_load_content = fields.Boolean("Load Content", default=True, help='Load the content of the file (for "in" flow)')

    @api.constrains("ftp_in_done_let", "integration_ids")
    def _check_let_in_folder_and_synchronization_creation(self):
        for rec in self:
            if rec.ftp_in_done_let:
                if any(
                    integration.integration_flow_type == "in" and integration.synchronization_creation != 1
                    for integration in rec.integration_ids
                ):
                    raise ValidationError(_('Let in "in_folder" only works with Synchronization Creation = 1'))

    #####################################################################
    #             Methods overridden from edi_base                      #
    #####################################################################

    def test(self):
        """Try to connect to the server"""
        self.ensure_one()
        if not self.type == "ftp":
            return super().test()

        self._ftp_test_connection()

    def _send_synchronization(self, filename, content, *args, **kwargs):
        """Override to upload the file"""
        self.ensure_one()
        if not self.type == "ftp":
            return super()._send_synchronization(filename, content, *args, **kwargs)

        return self._ftp_send_file(filename, content, *args, **kwargs)

    def _fetch_synchronizations(self, *args, **kwargs):
        """Override to download the file from the FTP server"""
        self.ensure_one()
        if not self.type == "ftp":
            return super()._fetch_synchronizations(*args, **kwargs)

        return self._ftp_fetch_files(*args, **kwargs)

    def _clean_synchronization_in(self, data, status, *args, **kwargs):
        if not self.type == "ftp":
            return super()._clean_synchronization_in(data, status, *args, **kwargs)

        self._clean_local_file(data, *args, **kwargs)
        self._clean(data.get("filename"), status, "in", *args, **kwargs)

    def _clean_synchronization_out(self, filename, status, *args, **kwargs):
        if not self.type == "ftp":
            return super()._clean_synchronization_out(filename, status, *args, **kwargs)

        self._clean(filename, status, "out", *args, **kwargs)

    def _get_default_configuration(self):
        """Provide a configuration template for this type of connection"""
        if self.type != "ftp":
            return super()._get_default_configuration()

        return self._ftp_default_configuration()

    #####################################################################
    #    Specific FTP Methods that should be overridden                 #
    #    by a connection based on FTP                                   #
    #####################################################################

    def connect(self):
        """Open a connection"""
        self.ensure_one()
        if not self.type == "ftp":
            return super().connect()

        config = self._read_configuration()
        server = ftplib.FTP(host=config["host"], user=config["user"], passwd=config["password"])

        if "is_active" in config and config["is_active"] == "True":
            server.set_pasv(False)

        self.ftp_load_config(server, config)
        return server

    @api.model
    def pwd(self, server):
        """Get the current directory"""
        if not self.type == "ftp":
            return super().pwd(server)

        return server.pwd()

    @api.model
    def dir_exists(self, server, path):
        """Check if the directory exists"""
        if not self.type == "ftp":
            return super().dir_exists(server, path)

        try:
            server.cwd(path)
            return True
        except Exception:
            return False

    @api.model
    def file_exists(self, server, path, filename):
        """Check if the file exists"""
        if not self.type == "ftp":
            return super().file_exists(server, path, filename)

        for file in self.list_files(server, path):
            if file == filename:
                return True
        return False

    @api.model
    def delete_file(self, server, path):
        if not self.type == "ftp":
            return super().delete_file(server, path)

        server.delete(path)

    @api.model
    def change_dir(self, server, path):
        if not self.type == "ftp":
            return super().change_dir(server, path)

        server.cwd(path)

    @api.model
    def rename(self, server, old, new):
        if not self.type == "ftp":
            return super().rename(server, old, new)

        server.rename(old, new)

    @api.model
    def list_files(self, server, path=False):
        if not self.type == "ftp":
            return super().list_files(server, path)

        if path:
            self.change_dir(server, path)
        names = getattr(server, "mlst", server.nlst)()
        filenames = []
        for name in names:
            if not self._ftp_is_valid_filename(name):
                continue
            filenames.append(name)
        return filenames

    @api.model
    def _upload_file(self, server, filename, binary_content):
        if not self.type == "ftp":
            return super()._upload_file(server, filename, binary_content)

        server.storbinary("STOR %s" % filename, binary_content)

    @api.model
    def _download_file(self, server, directory, filename):
        if not self.type == "ftp":
            return super()._download_file(server, directory, filename)

        with open(os.path.join(directory, filename), "wb") as file:
            server.retrbinary("RETR %s" % filename, file.write)
            return file.name

    @api.model
    def _ftp_is_valid_filename(self, filename):
        if filename in [".", ".."]:
            return False

        # NOTE: ftplib does not provides a way to differentiate between normal
        #       files and folders, we expect files to have a '.' on its name,
        #       obviously that is a rather random heuristic.
        fname, _, extension = filename.rpartition(".")
        if not fname or extension in ["bak", "old"]:
            return False

        return True

    #####################################################################
    #   Generic methods between FTP connection and other based on FTP   #
    #####################################################################

    @api.model
    def ftp_load_config(self, server, config):
        paths = {}
        current_folder = self.pwd(server)
        if not current_folder.endswith("/"):
            current_folder += "/"

        for folder, fallback_folder in [
            ("out_folder", "/"),
            ("in_folder", "/"),
            ("in_folder_done", False),
            ("in_folder_error", False),
        ]:

            # Handle not defined
            path = config.get(folder, fallback_folder)
            if not path:
                continue

            # Handle relative path
            if not path.startswith("/"):
                path = current_folder + path

            # Handle non existing path
            if not self.dir_exists(server, path):
                raise UserError(_('Folder "%s" : "%s" does not exists') % (folder, path))

            # Handle same path for different folders
            if path in paths:
                raise UserError(
                    _('Try to use path "%s" for folder "%s", but folder "%s" already use this one')
                    % (path, folder, paths[path])
                )
            paths[path] = folder

            # Add attribute to the server
            server.__setattr__(folder, path)

        if self.ftp_in_done_let and hasattr(server, "in_folder_done"):
            raise UserError(_("You shouldn't have an in_folder_done if you want to let the file on the folder"))

    def _ftp_test_connection(self):
        """Just try to connect"""
        try:
            with self.connect():
                pass
        except Exception as e:
            raise UserError(_("Connection Test Failed! Here is what we got instead:\n %s") % ustr(e))
        else:
            raise UserError(_("Connection Test Succeeded! Everything seems properly set up!"))

    def _ftp_send_file(self, filename, content, *args, **kwargs):
        with self.connect() as server:
            try:
                self.change_dir(server, server.out_folder)
                self._manage_conflict(server, server.out_folder, filename)
                self._upload_file(server, filename, StringIO(content.encode()))
            except Exception as e:
                _logger.error(e)
                raise UserError(_("Send synchronization failed for file %s:\n%s") % (filename, ustr(e)))

    def _ftp_fetch_files(self, *args, **kwargs):
        """Download the file into a temporary dictionnary
        Add the complete file path under key "file"
        Load their content under key "content" if ftp_load_content = True

        :return: list of dict
        """
        result = []

        # create a tempdir
        directory = tempfile.mkdtemp(prefix="odoo-ftp-")

        with self.connect() as server:
            filenames = self.list_files(server, server.in_folder)
            filtered_ones = self._filter_files(filenames, kwargs.get("integration_id"))
            for filename in filtered_ones:
                try:
                    result.append(
                        {
                            "filename": filename,
                            "file": self._download_file(server, directory, filename),
                            "directory": directory,
                        }
                    )
                except Exception as e:
                    _logger.error(e)
                    raise UserError(_("Fetch synchronization failed for file %s:\n%s") % (filename, ustr(e)))
        if self.ftp_load_content:
            for res in result:
                with open(res["file"], "r") as f:
                    res["content"] = f.read()
        return result

    def _filter_files(self, filenames, integration_id):
        """Filter files to be processed
        :param filenames:
        :param integration_id:
        :return: list of filenames
        """
        if not filenames or not integration_id:
            return filenames

        filenames = [
            filename
            for filename in filenames
            if filename.lower().endswith(integration_id.synchronization_content_type.lower())
        ]
        if not filenames or not self.ftp_in_done_let:
            return filenames

        # if we have to let the processed files on the server (in folder),
        # then we have to excluded them from the next synchros
        filenames_str = ", ".join(["('%s')" % filename for filename in filenames])
        cr = integration_id.env.cr
        cr.execute(
            """
            SELECT filename FROM (VALUES {filenames}) AS t (filename)
            WHERE NOT EXISTS (
                SELECT 1
                FROM edi_synchronization as sync
                WHERE sync.integration_id = %s
                AND sync.state = 'done'
                AND sync.filename = t.filename LIMIT 1
            );
            """.format(
                filenames=filenames_str
            ),
            [integration_id.id],
        )
        return [filename for (filename,) in cr.fetchall()]

    def _clean_local_file(self, data, *args, **kwargs):
        """Delete the file locally and the directory if empty
        :param data: dict
            return from _fetch_synchronizations
        """
        # Delete the local file
        f = data.get("file")
        directory = data.get("directory")

        if os.path.exists(f):
            # delete the file
            os.remove(f)

        if os.path.exists(directory) and os.path.isdir(directory) and not os.listdir(directory):
            # delete the directory if it is empty
            shutil.rmtree(directory)

    def _clean(self, filename, status, flow_type, *args, **kwargs):
        """After the file transfer:
        - out : delete the file if an error has occurred
        - in :
            - done :
                - if ftp_in_done_let == True => let
                - if in_folder_done is configured ==> move
                - otherwise delete
            - error :
                - if in_folder_error is configured ==> move
                - otherwise let (to allow to retry next time)
        """
        # No need to connect if :
        if (
            flow_type == "in"
            and self.ftp_in_done_let  # the downloaded file must be let on the server
            or flow_type == "out"
            and status == "done"
        ):  # the file could be uploaded without error
            return

        with self.connect() as server:
            # Move the files on the FTP

            if flow_type == "out":
                if self.file_exists(server, server.out_folder, filename):
                    self.delete_file(server, os.path.join(server.out_folder, filename))

            else:
                current_path = os.path.join(server.in_folder, filename)
                if status == "done":
                    if hasattr(server, "in_folder_done"):
                        self.rename(server, current_path, os.path.join(server.in_folder_done, filename))
                    else:
                        self.delete_file(server, current_path)
                elif hasattr(server, "in_folder_error"):
                    self.rename(server, current_path, os.path.join(server.in_folder_error, filename))

    def _manage_conflict(self, server, path, filename):
        """Check if the file already exists, manage the conflict by rename, replace or raise"""
        if not self.file_exists(server, path, filename):
            return

        config = self._read_configuration()
        on_conflict = config.get("on_conflict", "raise")
        file_path = os.path.join(path, filename)
        if on_conflict == "rename":
            self.rename(
                server,
                file_path,
                "%s.%s.%s"
                % (
                    file_path,
                    fields.Datetime.now().strftime("%Y%m%d%H%M%S"),
                    config.get("on_conflict_rename_extension", "old"),
                ),
            )
        elif on_conflict == "replace":
            self.delete_file(server, file_path)
        else:
            raise UserError(_("File '%s' already present on SFTP server") % file_path)

    @api.model
    def _ftp_default_configuration(self):
        return {
            "host": "host",
            "user": "user",
            "password": "password",
            "on_conflict": "choose one from : raise, rename, replace",
            "on_conflict_rename_extension": "old",
            "is_active": "False",
            "in_folder": "<PATH HERE>",
            "in_folder_done": "<PATH HERE>",
            "in_folder_error": "<PATH HERE>",
            "out_folder": "<PATH HERE>",
        }
