{
    "name": "Odoo PS EDI Archiving",
    "version": "17.0.1.2.0",
    "license": "OEEL-1",
    "summary": "Automatically archive synchronizations",
    "category": "Tools",
    "website": "https://www.odoo.com",
    "author": "Odoo PS",
    "depends": ["edi_base"],
    "data": ["views/res_config_settings_views.xml", "views/edi_synchronization_views.xml"],
    "auto_install": False,
    "installable": True,
    "cloc_exclude": ["**/*"],
}
