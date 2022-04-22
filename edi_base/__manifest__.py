{
    'name': 'Odoo PS EDI Framework',
    'version': '15.0.1.0.0',
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
        'security/edi_base.xml',
        'security/ir.model.access.csv',

        'data/connection.xml',

        'views/edi_connection.xml',
        'views/edi_integration.xml',
        'views/edi_synchronization.xml',
    ],
    'auto_install': False,
    'installable': False,
}
