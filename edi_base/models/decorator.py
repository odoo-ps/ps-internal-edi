# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import time
import logging

from inspect import signature

from odoo import api, fields

_logger = logging.getLogger(__name__)

def get_integration(integration_obj, name):
    integration = integration_obj.search([('name', '=', name), '|', ('active', '=', False), ('active', '=', True)], limit=1)
    if not integration:
            _logger.info("No integration found, create a default one")
            api_connection = integration_obj.env.ref('edi_base.api_connection')
            integration = integration_obj.create({
                'integration_flow' : 'in',
                'connection_id': api_connection.id,
                'type': 'api',
                'name': name,
                'synchronization_content_type': 'json',
                'active': False,
            })
    return integration

def create_synchronization(integration, pool, args, kwargs, fct):
    data = {
        'name' : '%s @%s' % (integration.name, time.time()),
        'integration_id' : integration.id,
        'synchronization_date': fields.Datetime.now(),
        'content': """
Function
\t%s.%s
Args
\t%s
Kwarg
\t%s
Context
\t%s""" % (pool._name, fct.__name__, args, kwargs, pool._context),
        'user_id': pool.env.user.id,
    }
    return integration.env['edi.synchronization'].create(data)

def integration(name):
    def decorator(fct):
        def wrapper(*args, **kwargs):
            self = args[0]
            with api.Environment.manage():
                new_cr = self.pool.cursor()
                integration_obj = self.env['edi.integration'].sudo().with_env(self.env(cr=new_cr))
                integration = get_integration(integration_obj, name)
                sync = create_synchronization(integration, self, args, kwargs, fct)
                new_cr.commit()
                try:
                    res = fct(*args, **kwargs)
                except Exception as e:
                    sync._report_error(name, e)
                    raise
                else:
                    sync._done()
                    self.flush()
                finally:
                    integration.set_status()
                    new_cr.commit()
                    new_cr.close()
            return res
        sig = signature(fct)
        wrapper.__signature__ = sig
        return wrapper
    return decorator
