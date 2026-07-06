from ..backend import AuditBackend, register


@register("ir_logging")
class IrLoggingBackend(AuditBackend):
    needs_env = True

    def start(self, env, name, metadata):
        self._env = env
        self._name = name
        self._log("start", name)

    def received(self, content):
        self._log("received", content)

    def sent(self, content):
        self._log("sent", content)

    def error(self, activity, exception=None, message=None):
        self._log(activity or "error", message or exception, level="ERROR")

    def finalize(self, state):
        self._log("finalize", state)

    def _log(self, func, message, level="INFO"):
        self._env["ir.logging"].create(
            {
                "name": "edi_audit",
                "type": "server",
                "level": level,
                "dbname": self._env.cr.dbname,
                "message": str(message),
                "func": func,
                "path": "edi_audit",
                "line": "0",
            }
        )
