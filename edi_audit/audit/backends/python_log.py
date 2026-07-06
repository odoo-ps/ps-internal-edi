import logging

from ..backend import AuditBackend, register


_logger = logging.getLogger("odoo.addons.edi_audit.audit")


@register("python_log")
class PythonLogBackend(AuditBackend):
    needs_env = False

    def start(self, env, name, metadata):
        self._name = name
        _logger.info("Audit run started: %s", name)

    def received(self, content):
        _logger.info("Audit [%s] received: %s", self._name, content)

    def sent(self, content):
        _logger.info("Audit [%s] sent: %s", self._name, content)

    def error(self, activity, exception=None, message=None):
        _logger.error("Audit [%s] error during %s: %s", self._name, activity, message or exception)

    def finalize(self, state):
        _logger.info("Audit [%s] finalized: %s", self._name, state)
