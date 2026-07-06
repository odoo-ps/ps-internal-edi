import contextlib

from odoo import api
from odoo.modules.registry import Registry

from .backend import AuditBackend, get_backend


def _resolve_backend(backend):
    if isinstance(backend, AuditBackend):
        return backend
    return get_backend(backend)


class AuditRun:
    """Self-persisting audit handle.

    Owns an isolated cursor/env (only when the backend needs one) so the audit
    trail is committed independently of the caller's transaction and survives a
    rollback of the main flow. Replaces the old ``cr.sync`` cursor threading:
    the run is passed around explicitly instead.
    """

    def __init__(self, env, backend, name, metadata=None, default_activity=None, uid=None):
        self._src_env = env
        self._uid = uid or env.uid
        self.backend = _resolve_backend(backend)
        self.name = name
        self.metadata = metadata or {}
        self.default_activity = default_activity
        self._cr = None
        self._env = None
        self.finalized = False

    @property
    def env(self):
        return self._env

    @property
    def record(self):
        return getattr(self.backend, "record", None)

    def start(self):
        try:
            if self.backend.needs_env:
                self._cr = Registry(self._src_env.cr.dbname).cursor()
                self._env = api.Environment(self._cr, self._uid, self._src_env.context)
            self.backend.start(self._env, self.name, self.metadata)
            self.commit()
        except Exception:
            self.close()
            raise
        return self

    def received(self, content):
        self.backend.received(content)
        self.commit()

    def sent(self, content):
        self.backend.sent(content)
        self.commit()

    def error(self, exception=None, message=None, activity=None):
        self.backend.error(activity or self.default_activity or "error", exception=exception, message=message)
        self.commit()

    def done(self):
        self._finalize("done")

    def fail(self):
        self._finalize("fail")

    def cancel(self):
        self._finalize("cancelled")

    def _finalize(self, state):
        self.backend.finalize(state)
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
