# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'Odoo PS EDI Monitoring',
    'version': '1.0',
    'summary': '',
    'category': 'Tools',
    'depends': ['edi_base'],
    'data': [
        'security/ir.model.access.csv',
        'data/edi_monitoring.xml',
        'data/send_report_template.xml',
        'views/edi_integration.xml',
        'views/edi_monitoring.xml',
        'views/edi_synchronization.xml',
    ],
    'auto_install': False,
    'installable': True,
}
