""" Add the file size on the synchronizations """
import logging
import os

from odoo import fields, models

_logger = logging.getLogger(__name__)


class Integration(models.Model):
    _inherit = "edi.integration"

    def _create_synchronization_in(self, data):
        """Override to add the file size on the synchronization

        Assume the file path has been added by the connection
        under the 'file' key.

        If the 'file' key is given, it will try to automatically
        compute the file size and add it on the synchronization
        """
        sync = super()._create_synchronization_in(data)
        size = 0
        one = False
        for d in data:
            filename = d.get("file")
            if filename:
                try:
                    size += os.path.getsize(filename)
                    one = True
                except Exception:
                    # skip if the file does not exist or is inaccessible
                    size = False
        sync.file_size = size if one else False
        return sync


class Synchronization(models.Model):
    _inherit = "edi.synchronization"

    file_size = fields.Integer(readonly=True, help="File size (bytes)")
