# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class EdiImportMapping(models.Model):
    _name = "edi.import.mapping"
    _description = "EDI Import Mapping"
    _order = "sequence, id"

    integration_id = fields.Many2one("edi.integration", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)

    # Source Configuration
    column_name = fields.Char(
        string="Source Column / Key",
        required=True,
        help="For CSV/XLSX: The column header name.\n"
        "For JSON: The key path (e.g. 'partner.email').\n"
        "For XML: The XPath (e.g. 'partner/email').",
    )

    # Target Configuration
    field_id = fields.Many2one("ir.model.fields", string="Target Field", required=True, ondelete="cascade")
    model_id = fields.Many2one(related="integration_id.binding_model_id", readonly=True)
    field_ttype = fields.Selection(related="field_id.ttype", readonly=True)
    field_relation = fields.Char(related="field_id.relation", readonly=True)

    # Advanced Lookup for Relations
    lookup_method = fields.Selection(
        [
            ("name", "By Name / Display Name"),
            ("xml_id", "By External ID"),
            ("id", "By Database ID"),
            ("field", "By Other Field (Advanced)"),
        ],
        string="Lookup Method",
        default="name",
        help="How to find the related record for Many2one/Many2many fields.",
    )

    lookup_field_id = fields.Many2one(
        "ir.model.fields",
        string="Lookup Field",
        domain="[('model', '=', field_relation)]",
        help="Specify the field on the related model to search on (e.g., 'code', 'ref').",
    )

    on_lookup_failure = fields.Selection(
        [
            ("ignore", "Ignore (leave empty)"),
            ("warning", "Log a Warning"),
            ("raise", "Block the Import"),
        ],
        string="If Not Found",
        default="warning",
        help="Action to take if the related record is not found using the lookup.",
    )

    is_identifier = fields.Boolean(
        string="Is Identifier?",
        help="If checked, this field will be used as the unique key to find existing records for update.\n"
        "Only one field can be the identifier per mapping.",
    )

    @api.constrains("is_identifier", "integration_id")
    def _check_unique_identifier(self):
        for integration in self.mapped("integration_id"):
            if len(integration.mapping_ids.filtered("is_identifier")) > 1:
                raise ValidationError(_("Only one field can be marked as identifier per integration."))

    @api.onchange("field_id")
    def _onchange_field_id(self):
        if self.field_id and not self.column_name:
            self.column_name = self.field_id.name

    def get_import_field_name(self):
        """
        Returns the field name formatted for Odoo's `base_import` module.

        This method adds specific suffixes to the field name for relational
        fields to tell `base_import` how to look up the related records.

        - For relational fields (Many2one, Many2many):
          - 'name' (default): Returns the plain field name (e.g., 'partner_id').
            `base_import` will automatically try to find the record by its
            `rec_name` or `display_name`. This is the standard Odoo behavior.
          - 'xml_id': Returns 'field_name/id'. `base_import` will look for a
            record with the given External ID (XML ID).
          - 'id': Returns 'field_name/.id'. `base_import` will look for a
            record with the given Database ID (integer).

        - For non-relational fields, it always returns the plain field name.
        """
        self.ensure_one()
        name = self.field_id.name
        if self.field_id.ttype in ["many2one", "many2many", "one2many"]:
            if self.lookup_method == "xml_id":
                return f"{name}/id"
            elif self.lookup_method == "id" or self.lookup_method == "field":
                # For 'field', we pre-process to find the ID, so we tell base_import to use it.
                return f"{name}/.id"
        return name

    @api.model
    def _extract_json_values(self, content, record_path, mappings):
        """
        Flattens a JSON structure into a list of dictionaries based on mappings.
        :param content: JSON string or dict
        :param record_path: Path to the list of records (dot notation)
        :param mappings: Recordset of edi.import.mapping
        :return: List of dicts
        """
        if isinstance(content, str):
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                raise UserError(_("Invalid JSON format: %s") % str(e)) from e
        else:
            data = content

        # 1. Navigate to the list of records
        records = data
        if record_path:
            for key in record_path.split("."):
                if isinstance(records, dict):
                    records = records.get(key, {})
                else:
                    records = []
                    break

        if isinstance(records, dict):
            records = [records]
        elif not isinstance(records, list):
            records = []

        extracted_rows = []
        for record in records:
            row = {}
            for mapping in mappings:
                value = record
                # Navigate to the value
                path_parts = mapping.column_name.split(".")
                try:
                    for part in path_parts:
                        if isinstance(value, dict):
                            value = value.get(part)
                        else:
                            value = None
                            break
                    row[mapping.column_name] = value
                except Exception:
                    row[mapping.column_name] = None
            extracted_rows.append(row)

        return extracted_rows

    @api.model
    def _extract_xml_values(self, content, record_path, mappings):
        """
        Flattens an XML structure into a list of dictionaries based on mappings.
        :param content: XML string
        :param record_path: XPath to the list of records
        :param mappings: Recordset of edi.import.mapping
        :return: List of dicts
        """
        try:
            root = etree.fromstring(content.encode("utf-8"))
        except etree.XMLSyntaxError as e:
            raise UserError(_("Invalid XML format: %s") % str(e)) from e

        # 1. Find records
        records = root.xpath(record_path) if record_path else [root]

        extracted_rows = []
        for record in records:
            row = {}
            for mapping in mappings:
                # 2. Find value for each mapping
                # XPath is relative to the record node
                nodes = record.xpath(mapping.column_name)
                if nodes:
                    # Take the text of the first match
                    if isinstance(nodes[0], etree._Element):
                        row[mapping.column_name] = nodes[0].text
                    else:
                        row[mapping.column_name] = str(nodes[0])
                else:
                    row[mapping.column_name] = None
            extracted_rows.append(row)

        return extracted_rows
