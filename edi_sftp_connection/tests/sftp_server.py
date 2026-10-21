# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Minimal in-process SFTP server for the tests, serving a local folder with paramiko."""

import os
import socket
import threading

import paramiko


class _SFTPHandle(paramiko.SFTPHandle):
    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)


class _SFTPServerInterface(paramiko.SFTPServerInterface):
    """Serves ``server.root`` as ``/``."""

    def __init__(self, server, *args, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.root = server.server.root

    def _local(self, path):
        path = os.path.normpath("/" + path).lstrip("/")
        return os.path.join(self.root, path)

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def list_folder(self, path):
        def list_folder(local):
            result = []
            for name in os.listdir(local):
                attributes = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(local, name)))
                attributes.filename = name
                result.append(attributes)
            return result

        return self._call(list_folder, self._local(path))

    def stat(self, path):
        return self._call(lambda p: paramiko.SFTPAttributes.from_stat(os.stat(p)), self._local(path))

    lstat = stat

    def open(self, path, flags, attr):
        def open_(local):
            fd = os.open(local, flags, 0o644)
            mode = "wb" if flags & os.O_WRONLY else "r+b" if flags & os.O_RDWR else "rb"
            handle = _SFTPHandle(flags)
            handle.readfile = handle.writefile = os.fdopen(fd, mode)
            return handle

        return self._call(open_, self._local(path))

    def remove(self, path):
        return self._call(lambda p: os.remove(p) or paramiko.SFTP_OK, self._local(path))

    def rename(self, oldpath, newpath):
        def rename(old, new):
            if os.path.exists(new):  # SFTP v3 rename does not overwrite
                return paramiko.SFTP_FAILURE
            os.rename(old, new)
            return paramiko.SFTP_OK

        return self._call(rename, self._local(oldpath), self._local(newpath))

    def posix_rename(self, oldpath, newpath):
        if not self.server.server.posix_rename:
            return paramiko.SFTP_OP_UNSUPPORTED
        return self._call(
            lambda old, new: os.replace(old, new) or paramiko.SFTP_OK, self._local(oldpath), self._local(newpath)
        )

    def canonicalize(self, path):
        return os.path.normpath("/" + path)


class _ServerInterface(paramiko.ServerInterface):
    def __init__(self, server):
        self.server = server

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_password(self, username, password):
        ok = (username, password) == (self.server.username, self.server.password)
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        authorized = self.server.authorized_key
        ok = username == self.server.username and authorized and key.get_base64() == authorized.get_base64()
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class SFTPTestServer:
    """SFTP server on ``127.0.0.1:<port>``, started in a thread, serving ``root``.

    :param root: local folder served as ``/``
    :param host_key: paramiko key of the server, or a list of keys (of different types)
    """

    def __init__(self, root, host_key, username="odoo", password="secret", authorized_key=None):
        self.root = root
        self.host_keys = list(host_key) if isinstance(host_key, (list, tuple)) else [host_key]
        self.username = username
        self.password = password
        self.authorized_key = authorized_key
        self.posix_rename = True  # support of the posix-rename@openssh.com extension
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(5)
        self.port = self.socket.getsockname()[1]
        self.transports = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.socket.close()
        for transport in self.transports:
            transport.close()

    def _serve(self):
        while True:
            try:
                sock, _address = self.socket.accept()
            except OSError:  # closed by stop()
                return
            transport = paramiko.Transport(sock)
            for host_key in self.host_keys:
                transport.add_server_key(host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _SFTPServerInterface)
            self.transports.append(transport)
            try:
                transport.start_server(server=_ServerInterface(self))
            except (paramiko.SSHException, EOFError, OSError):
                transport.close()
