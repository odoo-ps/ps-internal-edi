# Part of Odoo. See LICENSE file for full copyright and licensing details.
import csv
import json
import logging
from io import StringIO

from odoo import fields, models


_logger = logging.getLogger(__name__)


class IntegrationOut(models.Model):
    _inherit = "edi.integration"

    def _get_content(self, records):
        if self.type != "generic":
            return super()._get_content(records)

        data = ""
        if self.type == "generic":
            # Case 1: Tabular Export (CSV/JSON via Export Profile)
            if self.renderer_type == "tabular" and self.export_id:
                field_names = self.export_id.export_fields.mapped("name")
                if not field_names:
                    return ""

                data_result = records.export_data(field_names)
                data_rows = data_result.get("datas", [])

                if self.synchronization_content_type == "json":
                    # Flat JSON (list of objects)
                    return json.dumps(
                        [dict(zip(field_names, row, strict=True)) for row in data_rows], default=str, indent=4
                    )

                elif self.synchronization_content_type in ["csv", "txt"]:
                    output = StringIO()
                    writer = csv.writer(output, delimiter=self.csv_delimiter or ",")
                    writer.writerow(field_names)
                    writer.writerows(data_rows)
                    return output.getvalue()

            # Case 2: QWeb Template (XML/JSON Complex)
            elif self.renderer_type == "qweb" and self.template_id:
                return self.env["ir.qweb"]._render(
                    self.template_id.id,
                    {
                        "records": records,
                        "time": fields.Datetime.now(),
                    },
                )

            # Legacy Fallback (to be removed or kept for backward compatibility)
            conf = self._read_parameter()
            fields_list = conf.get("fields")
            if fields_list:
                exported_data = records.export_data(fields_list)["datas"]
                if self.synchronization_content_type == "json":
                    data_list = [dict(zip(fields_list, row, strict=True)) for row in exported_data]
                    data = json.dumps(data_list, indent=4)
                elif self.synchronization_content_type in ["text", "csv"]:
                    content = StringIO()
                    writer = csv.writer(content, delimiter=conf.get("csv_delimiter", ","))
                    writer.writerow(fields_list)
                    writer.writerows(exported_data)
                    return content.getvalue()

        return data
