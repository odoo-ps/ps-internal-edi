# Part of Odoo. See LICENSE file for full copyright and licensing details.
from dataclasses import dataclass, field


class EdiTransportError(Exception):
    """Raised when the remote system cannot be reached or refuses a request.

    ``content`` holds what the remote system answered, if anything (e.g. an HTTP error body),
    so that it can be stored for troubleshooting.
    """

    def __init__(self, message, content=None):
        super().__init__(message)
        self.content = content


@dataclass
class EdiMessage:
    """A unit of data exchanged with the remote system, independent of the transport.

    :param name: human readable name (file name, endpoint, ...)
    :param content: raw payload, decoding is the job of the format layer
    :param ref: transport-specific locator (remote file path, URL, ...), used to acknowledge it
    :param meta: transport-specific extra information (HTTP status/headers, file size, ...)
    """

    name: str
    content: bytes
    ref: str = ""
    meta: dict = field(default_factory=dict)
