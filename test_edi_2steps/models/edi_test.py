import csv
import logging
from ast import literal_eval
from io import StringIO

from odoo import fields, models

_logger = logging.getLogger(__name__)


class TestIntegration(models.Model):
    _inherit = "edi.integration"

    type = fields.Selection(selection_add=[("api_2steps", "API 2 steps")], ondelete={"api_2steps": "cascade"})

    def _prepare_in_edi_table(self, data):
        if self.type != "api_2steps":
            return super()._prepare_in_edi_table(data)

        result = []
        for d in data:
            content = d.get("content")

            if content == "raise":
                result.append({
                    "content": [{"name": False}],
                })
            else:
                csv_file = StringIO(d.get("content"))
                reader = csv.reader(csv_file, delimiter=",")
                header = reader.__next__()
                file_content = []

                for line in reader:
                    if not line[0]:
                        self._report_error("Import Partner",
                                           message=f"No value for field name, name is required \n {line}")
                        continue
                    file_content.append({
                        "name": line[0],
                    })

                result.append({
                    "content": file_content,
                })
        return result

    def _process_in_edi_table(self, data):
        if self.type != "api_2steps":
            return super()._process_in_edi_table(data)

        vals_list = []
        for d in data:
            vals_list.extend(literal_eval(d.get("content", [])))

        return self.env["res.partner"].create(vals_list)

    def _get_record_to_send(self):
        if self.type != "api_2steps":
            return super()._get_record_to_send()

        conf = self._read_parameter()
        if "filter" not in conf:
            return super()._get_record_to_send()
        domain = conf["filter"]
        return self.env["res.partner"].search(domain)

    def _prepare_out_edi_table(self, records):
        if self.type != "api_2steps":
            return super()._prepare_out_edi_table()

        if len(records) == 1 and "error" in records.name:
            self._report_error("Export Partner", message="Cannot export the partner")
            return [{
                "content": [],
            }]
        if len(records) == 1 and "time" in records.name:
            import time
            time.sleep(30)

        if len(records) == 1 and "raise" in records.name:
            # Generate an error that break an sql constraint
            self.env["res.partner"].create({"name": False})

        return [{
            "content": [{
                "id": record.id,
                "name": record.name
            } for record in records]
        }]

    def _process_out_edi_table(self, records):
        if self.type != "api_2steps":
            return super()._process_out_edi_table(records)

        content = StringIO()
        writer = csv.writer(content)

        conf = self._read_parameter()
        fields = conf["fields"]
        writer.writerow(fields)
        rows = []
        for record in records:
            rows.extend([vals.values() for vals in literal_eval(record.content)])
        writer.writerows(rows)
        return content.getvalue()
