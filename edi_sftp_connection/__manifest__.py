{
    "name": "SFTP Connection for Odoo PS EDI Framework",
    "version": "20.0.1.0.0",
    "license": "OEEL-1",
    "summary": "SFTP transport for EDI connections",
    "category": "Tools",
    "website": "https://www.odoo.com",
    "author": "Odoo PS",
    "depends": ["edi_base"],
    "data": ["views/edi_connection_views.xml"],
    "installable": True,
    "external_dependencies": {"python": ["paramiko"]},
    "cloc_exclude": ["**/*"],
}
