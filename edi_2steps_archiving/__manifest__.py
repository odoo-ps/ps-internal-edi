{
    "name": "Odoo PS EDI Archiving",
    "version": "19.0.1.0.0",
    "license": "OEEL-1",
    "summary": "Automatically archive 2-Steps EDI table records",
    "category": "Tools",
    "website": "https://www.odoo.com",
    "author": "Odoo PS",
    "depends": ["edi_2steps", "edi_archiving"],
    "data": [
        "views/edi_integration_views.xml",
        "views/edi_synchronization_views.xml",
        "views/edi_table_record_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "auto_install": False,
    "installable": True,
    "cloc_exclude": ["**/*"],
}
