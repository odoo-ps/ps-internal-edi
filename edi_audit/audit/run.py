import contextlib

from odoo import api
from odoo.models import BaseModel
from odoo.modules.registry import Registry


_BACKEND_PREFIX = "edi.audit.backend."


class AuditRun:
    """Self-persisting audit handle.

    Owns an isolated cursor/env (only when the backend needs one) so the audit
    trail is committed independently of the caller's transaction and survives a
    rollback of the main flow. Backends are stateless ``edi.audit.backend.*``
    AbstractModels resolved per-database through the ORM registry; ``_audit_start``
    returns an opaque entry that this handle threads back into every later call.
    """

    def __init__(self, env, backend, name, metadata=None, default_activity=None, uid=None):
        self._src_env = env
        self._uid = uid or env.uid
        self._backend_spec = backend
        self.name = name
        self.metadata = metadata or {}
        self.default_activity = default_activity
        self._backend = None
        self._entry = None
        self._cr = None
        self._env = None
        self.finalized = False

    @property
    def env(self):
        return self._env

    @property
    def entry(self):
        return self._entry

    @property
    def record(self):
        return self._entry if isinstance(self._entry, BaseModel) else None

    def _resolve(self, env):
        # NOTE: A string key resolves to an AbstractModel on the given env;
        #       otherwise the spec is a pre-resolved recordset or a duck-typed
        #       test double, used as-is.
        backend = env[_BACKEND_PREFIX + self._backend_spec] if isinstance(self._backend_spec, str) else self._backend_spec
        if backend._audit_needs_cursor:
            self._cr = Registry(self._src_env.cr.dbname).cursor()
            self._env = api.Environment(self._cr, self._uid, self._src_env.context)
            if isinstance(backend, BaseModel):
                backend = backend.with_env(self._env)
        return backend

    def start(self):
        self._backend = self._resolve(self._src_env)
        try:
            self._entry = self._backend._audit_start(self.name, self.metadata)
            self.commit()
        except Exception:
            self.close()
            raise
        return self

    def input(self, content):
        self._backend._audit_input(self._entry, content)
        self.commit()

    def output(self, content):
        self._backend._audit_output(self._entry, content)
        self.commit()

    def error(self, exception=None, message=None, activity=None):
        self._backend._audit_error(
            self._entry, activity or self.default_activity or "error", exception=exception, message=message
        )
        self.commit()

    def done(self):
        self._finalize("done")

    def fail(self):
        self._finalize("fail")

    def cancel(self):
        self._finalize("cancelled")

    def _finalize(self, state):
        self._backend._audit_finalize(self._entry, state)
        self.finalized = True
        self.commit()

    def commit(self):
        if self._cr is not None:
            self._cr.commit()

    def close(self):
        if self._cr is not None:
            self._cr.close()
            self._cr = None
            self._env = None


@contextlib.contextmanager
def audit_run(env, backend, name, metadata=None, default_activity=None, uid=None, on_finalize=None):
    """Own the full audit-run lifecycle around a ``with`` body.

    ``on_finalize(record)`` is an optional teardown hook run after the run is
    finalized (done/fail) but before the audit cursor is closed, and its writes
    are committed on that cursor. It receives the backend record and only fires
    when the backend produced one (a DB-backed backend).
    """
    run = AuditRun(env, backend, name, metadata=metadata, default_activity=default_activity, uid=uid)
    run.start()
    try:
        yield run
    except Exception as exc:
        run.error(exception=exc)
        run.fail()
        raise
    else:
        if not run.finalized:
            run.done()
    finally:
        if on_finalize is not None and run.record is not None:
            on_finalize(run.record)
            run.commit()
        run.close()
