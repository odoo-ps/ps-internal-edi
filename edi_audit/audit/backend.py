import abc


BACKENDS = {}


def register(key):
    """Register an AuditBackend subclass under a string key."""

    def _register(cls):
        BACKENDS[key] = cls
        return cls

    return _register


def get_backend(key):
    """Instantiate the backend registered under ``key``.

    :raises KeyError: if no backend is registered under ``key``.
    """
    if key not in BACKENDS:
        raise KeyError("Unknown audit backend: %r" % key)
    return BACKENDS[key]()


class AuditBackend(abc.ABC):
    """Contract every audit backend implements.

    ``needs_env`` is True only for backends that persist through the ORM; the
    handle then opens an isolated cursor/env and passes it to ``start``.
    ``record`` exposes the underlying persisted record (or None) for callers
    that need it (e.g. to update integration status).
    """

    needs_env = False
    record = None

    @abc.abstractmethod
    def start(self, env, name, metadata):
        ...

    @abc.abstractmethod
    def received(self, content):
        ...

    @abc.abstractmethod
    def sent(self, content):
        ...

    @abc.abstractmethod
    def error(self, activity, exception=None, message=None):
        ...

    @abc.abstractmethod
    def finalize(self, state):
        ...
