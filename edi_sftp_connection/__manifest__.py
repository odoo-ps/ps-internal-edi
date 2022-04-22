{
    'name': 'SFTP Connection for Odoo PS EDI Framework',
    'version': '15.0.1.0.0',
    'license': 'Other proprietary',
    'summary': '',
    'category': 'Tools',
    'description': """
SFTP Connection for Odoo PS EDI Framework
=========================================
    """,
    'depends': ['edi_ftp_connection'],
    'data': [
        'views/edi_connection.xml',
    ],
    'hidden': True,
    'auto_install': False,
    'installable': True,
}
