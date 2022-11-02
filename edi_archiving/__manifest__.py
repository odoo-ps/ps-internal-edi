{
    "name": "Odoo PS EDI Archiving",
    "version": "16.0.1.0.0",
    "license": "OEEL-1",
    "summary": "Automatically archive synchronizations",
    "category": "Tools",
    "website": "https://www.odoo.com",
    "author": "Odoo PS",
    "depends": ["edi_base"],
    "data": [
        "data/cron.xml",
        "views/res_config_settings_views.xml",
    ],
    "auto_install": False,
    "installable": True,
}
