import os
import os.path

from odoo import _, models
from odoo.exceptions import UserError


class ConnectionFolder(models.Model):

    _inherit = "edi.connection"

    def _get_default_configuration(self):
        if self.type != "folder":
            return super()._get_default_configuration()

        return {
            "in_folder": "<PATH HERE>",
            "in_folder_done": "<PATH HERE>",
            "in_folder_error": "<PATH HERE>",
            "out_folder": "<PATH HERE>",
        }

    def test(self):
        self.ensure_one()
        if not self.type == "folder":
            return super().test()

        config = self._read_configuration()
        for fname in [config["in_folder"], config["out_folder"], config["in_folder_done"], config["in_folder_error"]]:
            path = "%s/test" % fname
            with open(path, "w") as in_f:
                in_f.write("Test")
            os.remove(path)
        raise UserError(_("Connection Successful"))

    def _send_synchronization(self, filename, content, *args, **kwargs):
        self.ensure_one()
        if not self.type == "folder":
            return super()._send_synchronization(filename, content, *args, **kwargs)

        config = self._read_configuration()
        self._check_folder(config["out_folder"])
        path = "%s/%s" % (config["out_folder"], filename)
        with open(path, "w") as out_file:
            out_file.write(content)

    def _fetch_synchronizations(self, *args, **kwargs):
        self.ensure_one()
        if not self.type == "folder":
            return super()._fetch_synchronizations(*args, **kwargs)

        config = self._read_configuration()
        self._check_folder(config["in_folder"])
        data = []
        for f in os.listdir(config["in_folder"]):
            file_path = "%s/%s" % (config["in_folder"], f)
            if os.path.isfile(file_path):
                with open(file_path, "r") as fd:
                    data.append(
                        {
                            "filename": f,
                            "content": fd.read(),
                        }
                    )
        return data

    def _clean_synchronization_in(self, data, status, *args, **kwargs):
        if self.type != "folder":
            return super()._clean_synchronization_in(data, status, *args, **kwargs)

        return self._clean_synchronization(data.get("filename"), status, "in", *args, **kwargs)

    def _clean_synchronization_out(self, filename, status, *args, **kwargs):
        if self.type != "folder":
            return super()._clean_synchronization_out(filename, status, *args, **kwargs)

        return self._clean_synchronization(filename, status, "out", *args, **kwargs)

    def _clean_synchronization(self, filename, status, flow_type, *args, **kwargs):
        self.ensure_one()
        if not self.type == "folder":
            return super()._clean_synchronization(filename, status, flow_type, *args, **kwargs)

        config = self._read_configuration()
        if flow_type == "out":
            self._check_folder(config["out_folder"])
            path = "%s/%s" % (config["out_folder"], filename)
            if status == "error":
                if os.path.isfile(path):
                    os.remove(path)

        if flow_type == "in":
            path = "%s/%s" % (config["in_folder"], filename)
            if status == "done":
                self._check_folder(config["in_folder_done"])
                done_path = "%s/%s" % (config["in_folder_done"], filename)
            else:
                self._check_folder(config["in_folder_error"])
                done_path = "%s/%s" % (config["in_folder_error"], filename)
            os.rename(path, done_path)

    def _check_folder(self, folder):
        current_cwd = os.getcwd()
        folder_list = folder.split("/")
        os.chdir("/")
        for path in folder_list:
            if not path:
                continue
            if not os.path.isdir(path):
                os.makedirs(path)
            os.chdir(path)
        os.chdir(current_cwd)
