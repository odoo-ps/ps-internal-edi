# Part of Odoo. See LICENSE file for full copyright and licensing details.
import base64
import csv
import io

from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError


class IntegrationIn(models.Model):
    _inherit = "edi.integration"

    def _process_content(self, data):
        if self.type != "generic":
            return super()._process_content(data)
        for d in data:
            self._process_base_import(d.get("content"), d.get("filename"))
        return "done"

    def _process_base_import(self, content, filename):
        """Generic import processing using base_import"""
        self.ensure_one()

        if not self.binding_model_id:
            raise UserError(_("Please configure the Binding Model for generic import."))
        if not self.mapping_ids:
            raise UserError(_("Please configure the Import Mapping for generic import."))

        mappings = self.mapping_ids.sorted("sequence")
        identifier_mapping = mappings.filtered("is_identifier")

        # 1. Prepare Data (Flatten if needed)
        if self.synchronization_content_type == "json":
            rows = self.env["edi.import.mapping"]._extract_json_values(content, self.record_path, mappings)
        elif self.synchronization_content_type == "xml":
            rows = self.env["edi.import.mapping"]._extract_xml_values(content, self.record_path, mappings)
        else:  # CSV
            csv_rows = []
            csv_reader = csv.reader(
                io.StringIO(content), delimiter=self.csv_delimiter or ",", quotechar=self.csv_quotechar or '"'
            )
            headers = next(csv_reader)
            for row in csv_reader:
                csv_rows.append(dict(zip(headers, row, strict=False)))
            rows = csv_rows

        # 2. Pre-process rows for advanced lookups
        self._preprocess_rows_for_lookup(rows, mappings)

        # 3. Generate virtual CSV with 'id' column if identifier is set
        csv_content, fields_to_import, columns_in_file = self._generate_csv_for_import(
            rows, mappings, identifier_mapping
        )

        # 4. Create and execute import
        # Note on 'file' encoding: base64.b64encode expects bytes, so we encode the string to utf-8 bytes.
        # The ORM's Binary field in Odoo 19+ strictly expects a BinaryValue object or a base64-encoded string.
        # By decoding the base64 bytes back to a utf-8 string, the ORM handles it natively and safely.
        import_wizard = self.env["base_import.import"].create(
            {
                "res_model": self.binding_model_id.model,
                "file": base64.b64encode(csv_content.encode("utf-8")).decode("utf-8"),
                "file_name": filename,
                "file_type": "text/csv",
            }
        )

        options = {
            "headers": False,
            "separator": self.csv_delimiter or ",",
            "quoting": self.csv_quotechar or '"',
            "date_format": "%Y-%m-%d",
            "datetime_format": "%Y-%m-%d %H:%M:%S",
        }

        result = import_wizard.execute_import(fields=fields_to_import, columns=columns_in_file, options=options)

        if result.get("messages"):
            errors = [msg["message"] for msg in result["messages"] if msg["type"] == "error"]
            if errors:
                raise UserError(_("Import failed:\n%s") % "\n".join(errors))

        return True

    def _preprocess_rows_for_lookup(self, rows, mappings):
        """
        Pre-processes data rows to resolve advanced lookups before passing to base_import.
        It finds records based on a specific field (e.g., code='BE') and replaces
        the value with the corresponding database ID.
        """
        self.ensure_one()

        advanced_lookup_mappings = mappings.filtered(
            lambda m: m.field_ttype in ["many2one", "many2many"] and m.lookup_method == "field" and m.lookup_field_id
        )

        if not advanced_lookup_mappings:
            return

        missing_values_by_field = {}

        for mapping in advanced_lookup_mappings:
            RelatedModel = self.env[mapping.field_id.relation]
            lookup_field = mapping.lookup_field_id.name

            values_to_find = {row.get(mapping.column_name) for row in rows if row.get(mapping.column_name)}

            if not values_to_find:
                continue

            # TODO: Add support for ir.filters to apply additional context-based domains.
            found_records = RelatedModel.search_read([(lookup_field, "in", list(values_to_find))], [lookup_field, "id"])

            value_to_id_map = {
                str(rec[lookup_field]): rec["id"] for rec in found_records if rec[lookup_field] is not False
            }

            for row in rows:
                original_value = row.get(mapping.column_name)
                # On convertit en string pour la comparaison, sauf si c'est None/False
                lookup_key = str(original_value) if original_value else False

                if lookup_key and lookup_key in value_to_id_map:
                    row[mapping.column_name] = value_to_id_map[lookup_key]
                else:
                    if original_value:
                        if mapping.on_lookup_failure == "raise":
                            raise UserError(
                                _("Value %r not found for field %r on model %r")
                                % (original_value, mapping.field_id.field_description, RelatedModel._name)
                            )
                        elif mapping.on_lookup_failure == "warning":
                            missing_values_by_field.setdefault(mapping.field_id.field_description, set()).add(
                                original_value
                            )

                    row[mapping.column_name] = None

        if missing_values_by_field:
            message = "<b>Import Warnings:</b><ul>"
            for field_name, values in missing_values_by_field.items():
                values_str = ", ".join(map(str, values))
                message += (
                    f"<li>Field {field_name!r}: The following values were not found "
                    f"and have been left empty: {values_str}</li>"
                )
            message += "</ul>"

            if self.env.cr.sync:
                self.env.cr.sync.message_post(
                    body=Markup(message), message_type="comment", subtype_xmlid="mail.mt_note"
                )

    def _generate_csv_for_import(self, rows, mappings, identifier_mapping):
        """
        Generates a CSV string from a list of dicts, adding an 'id' column
        if an identifier mapping is provided.
        """
        output = io.StringIO()

        fields_to_import = [m.get_import_field_name() for m in mappings]
        columns_in_file = [m.column_name for m in mappings]

        if identifier_mapping:
            fields_to_import.insert(0, "id")
            columns_in_file.insert(0, "id")

        writer = csv.DictWriter(
            output,
            fieldnames=columns_in_file,
            delimiter=self.csv_delimiter or ",",
            quotechar=self.csv_quotechar or '"',
            quoting=csv.QUOTE_ALL,
        )
        # writer.writeheader()

        for row in rows:
            csv_row = {m.column_name: row.get(m.column_name) for m in mappings}

            if identifier_mapping:
                identifier_value = row.get(identifier_mapping.column_name)
                if identifier_value:
                    module_name = "__import__"
                    xml_id = f"{module_name}.{self.binding_model_id.model.replace('.', '_')}_{identifier_value}"
                    csv_row["id"] = xml_id

            writer.writerow(csv_row)

        return output.getvalue(), fields_to_import, columns_in_file
