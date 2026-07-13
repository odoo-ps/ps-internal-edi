from odoo.upgrade import util


def migrate(cr, version):
    views_to_rename = [
        ("integration_form_view_edi_table", "integration_form_view"),
        ("synchronization_form_view_edi_table", "synchronization_form_view"),
    ]
    for old_xmlid, new_xmlid in views_to_rename:
        util.rename_xmlid(cr, f"edi_2steps.{old_xmlid}", f"edi_2steps.{new_xmlid}")

    views_to_remove = [
        "res_config_settings_view_form_edi_table",
    ]
    for view in views_to_remove:
        util.remove_view(cr, f"edi_2steps.{view}")

    cr.execute("""
        UPDATE edi_table_record SET state =
        CASE 
            WHEN state = 'error' THEN 'fail'
            WHEN state = 'cancel' THEN 'cancelled'
            ELSE state
        END
        WHERE state IN ('error', 'cancel')
    """)
