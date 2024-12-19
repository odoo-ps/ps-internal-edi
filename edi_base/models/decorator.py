# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging
import time
from inspect import signature

from odoo import SUPERUSER_ID, api, fields
from odoo.modules.registry import Registry


_logger = logging.getLogger(__name__)


def integration(name):
    """
    The idea behind that decorator is to allow to mark some RPC'allable methods
    to behave the same way an integration does.

    As for realtime method, inconsistency can happen since the function is executed on
    a different cursor than the integration and synchronization status.
    fct(*args, **kwargs) is using cursor args[0].env.cr != new self.env.cr (used by the integration)
    """

    def decorator(fct):
        def wrapper(*args, **kwargs):

            self = args[0]

            self.env.flush_all()

            new_cr = Registry(self.env.cr.dbname).cursor()
            new_env = api.Environment(new_cr, SUPERUSER_ID, self.env.context)
            self = self.with_env(new_env)

            edi = self.env["edi.integration"].search(
                [("name", "=", name), "|", ("active", "=", False), ("active", "=", True)], limit=1
            )

            if not edi:

                edi = edi.create(
                    {
                        "integration_flow": "in",
                        "connection_id": self.env.ref("edi_base.api_connection").id,
                        "type": "api",
                        "name": name,
                        "synchronization_content_type": "json",
                        "active": False,
                    }
                )

                new_cr.commit()

                _logger.info("No integration found, a default one has been created: '%s' [%s]", name, edi.id)

            # NOTE inspired from _process_synchronization
            # create a default synchronization,
            # commit it, so that the synchronization is created
            # even in case of timeout during the prosess
            sync = edi.env["edi.synchronization"].create(
                {
                    "name": "%s @%s" % (edi.name, time.time()),
                    "integration_id": edi.id,
                    "synchronization_date": fields.Datetime.now(),
                    "content": """
                    Function
                    \t%s.%s
                    Args
                    \t%s
                    Kwarg
                    \t%s
                    Context
                    \t%s
                """
                    % (self._name, fct.__name__, args, kwargs, self._context),
                    "user_id": self.env.user.id,
                }
            )

            new_cr.commit()

            res = None

            try:
                with self.env.cr.savepoint():
                    res = fct(*args, **kwargs)
            except Exception as e:
                sync._report_error(name, e)
                raise
            else:
                sync._done()
            finally:
                edi._set_status()

                new_cr.commit()
                new_cr.close()

            return res

        sig = signature(fct)
        wrapper.__signature__ = sig

        return wrapper

    return decorator
