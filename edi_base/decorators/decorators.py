import logging
import warnings
from functools import WRAPPER_ASSIGNMENTS

from odoo.exceptions import UserError
import contextlib


_logger = logging.getLogger(__name__)


class IntegrationCheck:
    def __init__(self, *integration_types: str, raise_if_wrong_integration: bool = False):
        # NOTE: Accept both varargs (@IntegrationCheck("api", "sftp")) and the
        #       legacy single list/tuple form (@IntegrationCheck(["api", "sftp"])).
        if len(integration_types) == 1 and isinstance(integration_types[0], (list, tuple)):
            warnings.warn(
                "Passing integration types as a list/tuple to IntegrationCheck is deprecated "
                "and support will be dropped in 20.0; pass them as positional arguments "
                "instead, e.g. IntegrationCheck('api', 'sftp').",
                DeprecationWarning,
                stacklevel=2,
            )
            integration_types = tuple(integration_types[0])
        if not all(isinstance(i, str) for i in integration_types):
            raise TypeError("integration_types must be strings")
        self.integration_types = integration_types
        self.raise_if_wrong_integration = raise_if_wrong_integration
        self.func = None
        self.defining_class = None

    def __call__(self, func):
        self.func = func
        if not self.integration_types:
            _logger.warning(
                "IntegrationCheck on %s has no integration types; the type check is skipped "
                "(raise_if_wrong_integration has no effect) and the decorated method will "
                "always delegate to super().",
                getattr(func, "__qualname__", func.__name__),
            )
        for attr in WRAPPER_ASSIGNMENTS:
            with contextlib.suppress(AttributeError):
                setattr(self, attr, getattr(func, attr))
        self.__wrapped__ = func
        return self

    def __set_name__(self, owner, name):
        self.defining_class = owner

    def __get__(self, obj, owner):
        if obj is None:
            # obj is None on any class-level attribute access, e.g.:
            #   - getattr(ModelClass, 'test')          ← Odoo's RPC dispatcher (call_kw /
            #                                            get_public_method) does exactly this to
            #                                            retrieve a public method, then calls
            #                                            method(recs, *args, **kwargs)
            #   - getattr(mro_cls, 'test')             ← get_public_method's internal MRO scan
            #                                            checking for _api_private
            #   - FTPConnection.test / Model.test      ← direct class-level access in Python
            #
            # The naive "return self" would hand the IntegrationCheck *instance* (which is
            # callable via __call__) to the caller.  When Odoo then invokes method(recs, ...),
            # it triggers IC.__call__(recs), which is the decorator-application path: it stores
            # the recordset as self.func and returns self — silently corrupting the descriptor
            # and never reaching the actual method body.
            #
            # Fix: return a thin unbound wrapper that re-enters __get__ with the actual record
            # once the caller provides it, correctly routing through the type-check logic.
            ic = self

            def unbound(rec, *args, **kwargs):
                return ic.__get__(rec, type(rec))(*args, **kwargs)

            for attr in WRAPPER_ASSIGNMENTS:
                with contextlib.suppress(AttributeError):
                    setattr(unbound, attr, getattr(self, attr))
            return unbound

        if obj._name not in ("edi.integration", "edi.connection"):
            _logger.warning(
                f"IntegrationCheck decorator can only be used on edi.integration or edi.connection, not on {obj._name}"
            )
            return self

        def bound_method(*args, **kwargs):
            # defining_class is the component class that declared the decorator (e.g. ConnectionApi),
            # which may differ from owner (the composed registry class) — use it for super() to work.
            actual_owner = self.defining_class or owner
            obj_type = obj[:1].type if obj else None

            if obj_type not in self.integration_types:
                if self.raise_if_wrong_integration and self.integration_types:
                    entity = obj.env._("integration") if obj._name == "edi.integration" else obj.env._("edi.connection")
                    raise UserError(
                        obj.env._(
                            "This method can only be called in the %(entity_type)s of type(s) %(expected_types)s!",
                            entity_type=entity,
                            expected_types=",".join(self.integration_types),
                        )
                    )

                super_obj = super(actual_owner, obj)
                parent_method = getattr(super_obj, self.func.__name__, None)
                if parent_method is not None:
                    return parent_method(*args, **kwargs)
                return  # no super method found, exit here

            return self.func(obj, *args, **kwargs)

        return bound_method
