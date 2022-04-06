import csv
import logging

from io import StringIO

from odoo import api, models

from odoo.addons.edi_base.models.decorator import integration


_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model
    @integration("Create Partner")
    def create_partner(self, data):
        """
            import odoolib
            odoolib.get_connection(database='edi_test', login='admin', password='admin', hostname="localhost").get_model("res.partner").create_partner({'name': 'Hello', 'time': 10})
        """
        if "time" in data:
            import time
            time.sleep(data.pop('time'))

        return self.create([data]).id

class TestIntegration(models.Model):

    _inherit = 'edi.integration'

    def _get_record_to_send(self):
        if self.type != 'partner_folder_out':
            return super()._get_record_to_send()

        conf = self._read_parameter()
        if 'filter' not in conf:
            return super()._get_record_to_send()
        domain = conf['filter']
        return self.env['res.partner'].search(domain)

    def _get_content(self, records):
        if self.type != 'partner_folder_out':
            return super()._get_content(records)

        if len(records) == 1 and 'error' in records.name:
            self._report_error("Export Partner", message="Cannot export the partner")
            return "Error: wrong partner"

        if len(records) == 1 and 'time' in records.name:
            import time
            time.sleep(30)

        if len(records) == 1 and 'raise' in records.name:
            # Generate an error that break an sql constraint
            self.env['res.partner'].create({'name': False})

        content = StringIO()
        writer = csv.writer(content)

        conf = self._read_parameter()
        fields = conf['fields']
        writer.writerow(fields)
        rows = records.export_data(fields)['datas']
        writer.writerows(rows)
        return content.getvalue()

    def _process_content(self, data):
        if self.type != 'partner_folder_in':
            return super()._process_content(data)

        for d in data:

            content = d.get('content')
            # Code to test when something go wrong
            if content == 'raise':
                self.env['res.partner'].create({'name': False})

            if content.strip() == 'time':
                import time
                time.sleep(30)

            csv_file = StringIO(content)
            reader = csv.reader(csv_file, delimiter=',')
            header = reader.__next__()

            vals_list = []
            for line in reader:
                if not line[0]:
                    self._report_error(
                        "Import Partner",
                        message=f"No value for field name, name is required \n {line}"
                    )
                    continue

                vals_list.append(line)

            self.env['res.partner'].load(header, vals_list)

        return "done"
