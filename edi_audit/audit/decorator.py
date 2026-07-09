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


def _describe_result(result):
    # NOTE: Describing the return value must never fail the audited call: it
    #       runs after the call succeeded, so a raising __repr__ would turn a
    #       completed operation into an error.
    try:
        return f"Result\n\t{result!r}"
    except Exception:  # noqa: BLE001
        return f"Result\n\t<undescribable {type(result).__name__}>"


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
        run.input(_describe_call(record, fct, args, kwargs))
        with record.env.cr.savepoint():
            res = fct(record, *args, **kwargs)
        run.output(_describe_result(res))
    return res


def audit(backend="python_log", name=None):
    def decorator(fct):
        @wraps(fct)
        def wrapper(self, *args, **kwargs):
            return _run_audited(self, backend, name or fct.__name__, fct, args, kwargs, uid=SUPERUSER_ID)

        return wrapper

    return decorator
