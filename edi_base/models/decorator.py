# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging
import time
from functools import wraps

from odoo import SUPERUSER_ID, api, fields
from odoo.modules.registry import Registry

from odoo.addons.edi_audit.audit import AuditRun


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
        @wraps(fct)
        def wrapper(self, *args, **kwargs):
            self.env.flush_all()

            # The integration must be committed on its own cursor so the audit
            # transaction (a separate cursor) can FK-reference it.
            edi_id = _get_or_create_api_integration(self.env, name)

            sync_name = "%s @%s" % (name, time.time())
            metadata = {
                "name": sync_name,
                "integration_id": edi_id,
                "synchronization_date": fields.Datetime.now(),
                "received_content": """
                    Function
                    \t%s.%s
                    Args
                    \t%s
                    Kwarg
                    \t%s
                    Context
                    \t%s
                """
                % (self._name, fct.__name__, args, kwargs, self.env.context),
                "user_id": self.env.user.id,
            }

            # SUPERUSER: creating edi.synchronization requires base.group_system
            # (see edi_base/security/ir.model.access.csv); audit must not be
            # blocked by the caller's rights (matches prior decorator behavior).
            run = AuditRun(
                self.env,
                "edi_synchronization",
                name=sync_name,
                metadata=metadata,
                default_activity=name,
                uid=SUPERUSER_ID,
            )
            run.start()
            res = None
            try:
                with self.env.cr.savepoint():
                    res = fct(self, *args, **kwargs)
            except Exception as exc:
                run.error(exception=exc)
                run.fail()
                raise
            else:
                run.done()
            finally:
                if run.record:
                    run.record.integration_id._set_status(run.record)
                    run.commit()
                run.close()
            return res

        return wrapper

    return decorator


def _get_or_create_api_integration(env, name):
    """Return the id of the (committed) api integration named ``name``.

    Created on a dedicated cursor and committed so a separate audit
    transaction can reference it.
    """
    new_cr = Registry(env.cr.dbname).cursor()
    new_env = api.Environment(new_cr, SUPERUSER_ID, env.context)
    try:
        edi = new_env["edi.integration"].search(
            [("name", "=", name), "|", ("active", "=", False), ("active", "=", True)], limit=1
        )
        if not edi:
            edi = new_env["edi.integration"].create({
                "integration_flow": "in",
                "connection_id": new_env.ref("edi_base.api_connection").id,
                "type": "api",
                "name": name,
                "synchronization_content_type": "json",
                "active": False,
            })
            _logger.info("No integration found, a default one has been created: '%s' [%s]", name, edi.id)
        new_cr.commit()
        return edi.id
    finally:
        new_cr.close()
