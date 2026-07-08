from functools import wraps

from odoo import SUPERUSER_ID

from .run import audit_run


def _describe_call(record, fct, args, kwargs):
    return (
        f"Function\n\t{record._name}.{fct.__name__}\n"
        f"Args\n\t{args}\n"
        f"Kwarg\n\t{kwargs}\n"
        f"Context\n\t{record.env.context}"
    )


def _run_audited(record, backend, name, fct, args, kwargs, *, metadata=None, uid=None, default_activity=None, on_finalize=None):
    with audit_run(
        record.env,
        backend,
        name,
        metadata=metadata,
        default_activity=default_activity or name,
        uid=uid,
        on_finalize=on_finalize,
    ) as run:
        run.received(_describe_call(record, fct, args, kwargs))
        with record.env.cr.savepoint():
            res = fct(record, *args, **kwargs)
    return res


def audit(backend="python_log", name=None):
    def decorator(fct):
        @wraps(fct)
        def wrapper(self, *args, **kwargs):
            return _run_audited(self, backend, name or fct.__name__, fct, args, kwargs, uid=SUPERUSER_ID)

        return wrapper

    return decorator
