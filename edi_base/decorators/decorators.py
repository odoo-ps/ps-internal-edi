import logging
from functools import WRAPPER_ASSIGNMENTS
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class IntegrationCheck:
    def __init__(self, integration_types: list[str], raise_if_wrong_integration:bool=False):
        if not (
            isinstance(integration_types, list) and
            len(integration_types) > 0 and
            all(isinstance(i, str) for i in integration_types)
        ):
            raise TypeError("integration_types must be a non-empty list of strings")
        self.integration_types = integration_types
        self.raise_if_wrong_integration = raise_if_wrong_integration
        self.func = None
        self.defining_class = None

    def __call__(self, func):
        self.func = func
        for attr in WRAPPER_ASSIGNMENTS:
            try:
                setattr(self, attr, getattr(func, attr))
            except AttributeError:
                pass
        self.__wrapped__ = func
        return self

    def __set_name__(self, owner, name):
        self.defining_class = owner

    def __get__(self, obj, owner):
        if obj is None:
            return self

        if obj._name not in ("edi.integration", "edi.connection"):
            _logger.warning(f"IntegrationCheck decorator can only be used on edi.integration or edi.connection, not on {obj._name}")
            return self

        def bound_method(*args, **kwargs):
            # The decorator's owner is most often the highest level of the class (the base defining class) but we need the actual class.
            actual_owner = self.defining_class or owner
            obj_type = obj[:1].type if obj else None

            if obj_type not in self.integration_types:
                if self.raise_if_wrong_integration:
                    entity = obj.env._("integration") if obj._name == "edi.integration" else obj.env._("edi.connection")
                    raise UserError(
                        obj.env._(
                            "This method can only be called in the %(entity_type)s of type(s) %(expected_types)s!",
                            entity_type=entity, expected_types=",".join(self.integration_types)
                        )
                    )

                super_obj = super(actual_owner, obj)
                parent_method = getattr(super_obj, self.func.__name__, None)
                if parent_method is not None:
                    return parent_method(*args, **kwargs)
                return # no super method found, exit here

            return self.func(obj, *args, **kwargs)

        return bound_method
