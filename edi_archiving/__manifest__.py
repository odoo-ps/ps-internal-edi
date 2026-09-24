{
    "name": "Odoo PS EDI Archiving",
    "version": "19.0.2.0.0",
    "license": "OEEL-1",
    "summary": "Automatically archive synchronizations",
    "category": "Tools",
    "website": "https://www.odoo.com",
    "author": "Odoo PS",
    "depends": ["edi_base"],
    "data": [
        "views/res_config_settings_views.xml",
        "views/edi_synchronization_views.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "post_init_hook": "post_init_hook",
    "auto_install": True,
    "installable": True,
    "cloc_exclude": ["**/*"],
}
