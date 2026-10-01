"""Small HTTP/1.1 file server over TCP sockets, intentionally built for study."""
from pathlib import Path
from contextlib import ExitStack
import errno
import mimetypes
import os
import stat
import socket
import threading
from urllib.parse import unquote, urlsplit


class FileServer:
    def __init__(self, root: Path, host: str = "127.0.0.1", port: int = 8080):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("root must be an existing directory")
        self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.address = self.listener.getsockname()
        self.stopped = threading.Event()
        self.workers = set()

    def serve_forever(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            worker = threading.Thread(target=self.handle_client, args=(connection,), daemon=True)
            self.workers.add(worker)
            worker.start()
            self.workers = {thread for thread in self.workers if thread.is_alive()}

    def handle_client(self, connection):
        with connection:
            connection.settimeout(2)
            headers_started = False
            try:
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    block = connection.recv(4096)
                    if not block:
                        self.respond(connection, 400, "Bad Request")
                        return
                    request.extend(block)
                    end = request.find(b"\r\n\r\n")
                    if (end if end >= 0 else len(request)) > 16384:
                        self.respond(connection, 413, "Content Too Large")
                        return
                header_lines = request[:request.find(b"\r\n\r\n")].split(b"\r\n")
                parts = header_lines[0].split(b" ")
                hosts = [line.partition(b":")[2].strip() for line in header_lines[1:] if line.partition(b":")[0].lower() == b"host"]
                if len(parts) != 3 or parts[2] != b"HTTP/1.1" or not parts[1].startswith(b"/") or parts[1].startswith(b"//") or len(hosts) != 1 or not hosts[0] or any(b":" not in line for line in header_lines[1:]):
                    self.respond(connection, 400, "Bad Request")
                    return
                method, target, _ = parts
                if method not in (b"GET", b"HEAD"):
                    self.respond(connection, 405, "Method Not Allowed", {"Allow": "GET, HEAD"})
                    return
                try:
                    name = unquote(urlsplit(target.decode("ascii")).path).lstrip("/")
                except (UnicodeError, ValueError):
                    self.respond(connection, 400, "Bad Request")
                    return
                if "\x00" in name:
                    self.respond(connection, 400, "Bad Request")
                    return
                parts = [part for part in name.split("/") if part and part != "."]
                if ".." in parts:
                    self.respond(connection, 403, "Forbidden")
                    return
                try:
                    with ExitStack() as stack:
                        directory = self.root_fd
                        for part in parts[:-1]:
                            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                            stack.callback(os.close, directory)
                        fd = os.open(parts[-1] if parts else ".", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                        source = stack.enter_context(os.fdopen(fd, "rb"))
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            self.respond(connection, 404, "Not Found")
                            return
                        mime = mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
                        headers_started = True
                        self.respond(connection, 200, "OK", {"Content-Type": mime}, size=info.st_size)
                        if method == b"GET":
                            while chunk := source.read(65536):
                                connection.sendall(chunk)
                except OSError as error:
                    if headers_started:
                        return
                    if error.errno in (errno.ELOOP, errno.EACCES, errno.EPERM):
                        self.respond(connection, 403, "Forbidden")
                    elif error.errno in (errno.ENOENT, errno.ENOTDIR):
                        self.respond(connection, 404, "Not Found")
                    else:
                        raise
            except socket.timeout:
                if not headers_started:
                    try:
                        self.respond(connection, 408, "Request Timeout")
                    except OSError:
                        pass
            except (OSError, UnicodeError, ValueError):
                pass

    @staticmethod
    def respond(connection, status, reason, headers=None, body=b"", size=None):
        fields = {"Content-Length": str(len(body) if size is None else size), "Connection": "close"}
        fields.update(headers or {})
        head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{key}: {value}\r\n" for key, value in fields.items()) + "\r\n"
        connection.sendall(head.encode("ascii") + body)

    def close(self):
        self.stopped.set()
        self.listener.close()
        for worker in list(self.workers):
            worker.join(timeout=3)
        os.close(self.root_fd)
