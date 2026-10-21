# Part of Odoo. See LICENSE file for full copyright and licensing details.
import contextlib

import requests


class EdiHttpSession(requests.Session):
    """``requests.Session`` releasing extra resources when closed (e.g. client certificate files).

    Register them on ``exit_stack``; they are released when the session is closed, which happens at
    the end of its ``with`` block.
    """

    def __init__(self):
        super().__init__()
        self.exit_stack = contextlib.ExitStack()

    def close(self):
        try:
            super().close()
        finally:
            self.exit_stack.close()
