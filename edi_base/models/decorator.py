# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import time
import logging

from inspect import signature

from odoo import api, fields, registry, SUPERUSER_ID


_logger = logging.getLogger(__name__)


def integration(name):
    """
    The idea behind that decorator is to allow to mark some RPC'allable methods
    to behave the same way an integration does.

    As in the regular flow, the synchronization record is created inside a new
    cursor, but a new one can be used to create the integration for the first time.
    """

    def decorator(fct):

        def wrapper(*args, **kwargs):

            self = args[0]

            edi = self.env['edi.integration'].search([
                ('name', '=', name),
                '|',
                ('active', '=', False),
                ('active', '=', True)
            ], limit=1)

            if not edi:

                with registry(self.env.cr.dbname).cursor() as cr:

                    edi = api.Environment(
                        cr,
                        SUPERUSER_ID,
                        self.env.context
                    )['edi.integration'].create({
                        'integration_flow' : 'in',
                        'connection_id': self.env.ref('edi_base.api_connection').id,
                        'type': 'api',
                        'name': name,
                        'synchronization_content_type': 'json',
                        'active': False,
                    })

                _logger.info("No integration found, a default one has been created: '%s' [%s]", name, edi.id)

            new_cr = registry(self.env.cr.dbname).cursor()
            new_env = api.Environment(new_cr, SUPERUSER_ID, self.env.context)

            # NOTE: We just change the environment of the record to be able to
            #       create the synchronization down below, otherwise the integration
            #       is not available on the `new_env`
            edi = edi.with_env(new_env)
            sync = new_env['edi.synchronization'].create({
                'name' : '%s @%s' % (edi.name, time.time()),
                'integration_id' : edi.id,
                'synchronization_date': fields.Datetime.now(),
                'content': """
                    Function
                    \t%s.%s
                    Args
                    \t%s
                    Kwarg
                    \t%s
                    Context
                    \t%s
                """ % (self._name, fct.__name__, args, kwargs, self._context),
                'user_id': self.env.user.id,
            })

            res = None

            try:
                res = fct(*args, **kwargs)
            except Exception as e:
                sync._report_error(name, e)
                raise
            else:
                sync._done()
            finally:
                # NOTE: Since we are under the same cursor, we need to commit 2
                #       times, one to push the changes on the created synchronization
                #       and finally, after computing the status of the integration.
                new_cr.commit()
                edi._set_status()
                new_cr.commit()
                new_cr.close()

            return res

        sig = signature(fct)
        wrapper.__signature__ = sig

        return wrapper

    return decorator
