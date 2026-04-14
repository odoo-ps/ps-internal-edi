{
    "name": "Odoo PS EDI Generic Mapping",
    "version": "19.3.1.0.0",
    "license": "OEEL-1",
    "summary": "Allow ot define generic intergrations & map the data from interface",
    "category": "Tools",
    "website": "https://www.odoo.com",
    "author": "Odoo PS",
    "depends": ["edi_base"],
    "data": [
        "security/ir.model.access.csv",
        "views/edi_integration.xml",
    ],
    "auto_install": False,
    "installable": True,
    "cloc_exclude": ["**/*"],
}
