{
    "name": "Odoo PS EDI Framework",
    "version": "20.0.1.0.0",
    "license": "OEEL-1",
    "summary": "Gateway between odoo and third party components",
    "category": "Tools",
    "website": "https://www.odoo.com",
    "author": "Odoo PS",
    "depends": ["mail"],
    "data": [
        "security/ir.access.csv",
        "views/edi_connection_views.xml",
        "views/edi_menus.xml",
    ],
    "installable": True,
    "cloc_exclude": ["**/*"],
}
