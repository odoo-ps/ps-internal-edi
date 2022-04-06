{
    'name': 'Odoo PS EDI Framework',
    'version': '2.0',
    'license': 'Other proprietary',
    'summary': 'Gateway between odoo and third party components',
    'category': 'Tools',
    'description': """
Odoo PS EDI Framework
=====================

Gateway between odoo and third party components
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
