{
    'name': 'Odoo PS EDI Framework',
    'version': '1.0',
    'license': 'Other proprietary',
    'summary': '',
    'category': 'Tools',
    'description': """
Odoo PS EDI Framework
=====================
    """,
    'depends': ['mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/edi_connection.xml',
        'views/edi_integration.xml',
        'views/edi_synchronization.xml',
        'data/connection.xml',
    ],
    'auto_install': False,
    'installable': False,
}
