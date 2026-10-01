"""Real TCP integration tests for the teaching server."""
import contextlib
import errno
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from lan_http.server import FileServer


@contextlib.contextmanager
def running_server(root: Path, **options):
    server = FileServer(root=root, host="127.0.0.1", port=0, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.address
    finally:
        server.close()
        thread.join(timeout=3)
        if thread.is_alive():
            raise AssertionError("server failed to shut down")


def exchange(address, request: bytes) -> tuple[int, dict[str, str], bytes]:
    with socket.create_connection(address, timeout=2) as client:
        client.settimeout(3)
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        chunks = []
        while data := client.recv(65536):
            chunks.append(data)
    response = b"".join(chunks)
    head, separator, body = response.partition(b"\r\n\r\n")
    if not separator:
        raise AssertionError(f"Missing header terminator: {response!r}")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return status, {key.lower(): value for key, value in headers.items()}, body


class FileServerTests(unittest.TestCase):
    def test_cli_exposes_root_host_and_port(self):
        result = subprocess.run([sys.executable, "-m", "lan_http", "--help"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--root", "--host", "--port"):
            self.assertIn(flag, result.stdout)

    def test_get_returns_file_over_real_tcp_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "hello.txt").write_bytes(b"hello network")
            with running_server(Path(tmp)) as address:
                status, headers, body = exchange(address, b"GET /hello.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"hello network")
            self.assertEqual(headers["content-length"], "13")
            self.assertEqual(headers["connection"], "close")
    def test_encoded_traversal_cannot_read_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "public")
            root.mkdir()
            Path(tmp, "secret.txt").write_bytes(b"PRIVATE")
            with running_server(root) as address:
                for target in (b"/../secret.txt", b"/%2e%2e/secret.txt"):
                    with self.subTest(target=target):
                        status, _, body = exchange(address, b"GET " + target + b" HTTP/1.1\r\nHost: localhost\r\n\r\n")
                        self.assertEqual(status, 403)
                        self.assertNotIn(b"PRIVATE", body)

    def test_replaced_file_symlink_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "public")
            root.mkdir()
            victim = root / "victim.txt"
            victim.write_bytes(b"public")
            secret = Path(tmp, "secret.txt")
            secret.write_bytes(b"PRIVATE")
            original_open = os.open
            replaced = threading.Event()

            def replace_before_open(path, flags, *args, **kwargs):
                if path == "victim.txt" and kwargs.get("dir_fd") is not None:
                    victim.unlink()
                    victim.symlink_to(secret)
                    replaced.set()
                return original_open(path, flags, *args, **kwargs)

            with patch("lan_http.server.os.open", side_effect=replace_before_open):
                with running_server(root) as address:
                    status, _, body = exchange(address, b"GET /victim.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(replaced.is_set(), "final file open was not intercepted")
            self.assertNotEqual(status, 200)
            self.assertNotIn(b"PRIVATE", body)

    def test_nested_file_and_intermediate_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "public")
            (root / "sub").mkdir(parents=True)
            (root / "sub" / "ok.txt").write_bytes(b"OK")
            outside = Path(tmp, "outside")
            outside.mkdir()
            (outside / "secret.txt").write_bytes(b"PRIVATE")
            (root / "link").symlink_to(outside, target_is_directory=True)
            with running_server(root) as address:
                status, _, body = exchange(address, b"GET /sub/ok.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
                unsafe_status, _, unsafe_body = exchange(address, b"GET /link/secret.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual((status, body), (200, b"OK"))
            self.assertNotEqual(unsafe_status, 200)
            self.assertNotIn(b"PRIVATE", unsafe_body)

    def test_get_reports_mime_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "hello.txt").write_text("hello")
            with running_server(Path(tmp)) as address:
                _, headers, _ = exchange(address, b"GET /hello.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertTrue(headers["content-type"].startswith("text/plain"))

    def test_large_binary_file_is_streamed_without_read_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = bytes(range(256)) * 4096
            Path(tmp, "large.bin").write_bytes(content)
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                with running_server(Path(tmp)) as address:
                    status, headers, body = exchange(address, b"GET /large.bin HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-length"], str(len(content)))
            self.assertEqual(body, content)

    def test_content_length_uses_open_file_not_later_path_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp, "sample.bin")
            file.write_bytes(b"ABCDEF")
            original_stat = os.stat

            def altered_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path == "sample.bin" and kwargs.get("dir_fd") is not None:
                    fields = list(result)
                    fields[6] = 999
                    return os.stat_result(fields)
                return result

            with patch("lan_http.server.os.stat", side_effect=altered_stat):
                with running_server(Path(tmp)) as address:
                    status, headers, body = exchange(address, b"GET /sample.bin HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-length"], "6")
            self.assertEqual(body, b"ABCDEF")

    def test_slow_client_does_not_block_another_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ok.txt").write_bytes(b"OK")
            with running_server(Path(tmp)) as address:
                with socket.create_connection(address, timeout=2) as slow:
                    slow.sendall(b"GET /ok.txt HTTP/1.1\r\nHost: ")
                    started = time.monotonic()
                    status, _, body = exchange(address, b"GET /ok.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    self.assertLess(time.monotonic() - started, 1.0)
            self.assertEqual(status, 200)
            self.assertEqual(body, b"OK")

    def test_oversized_headers_return_413(self):
        with tempfile.TemporaryDirectory() as tmp:
            with running_server(Path(tmp)) as address:
                status, _, _ = exchange(address, b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Long: " + b"x" * 17000 + b"\r\n\r\n")
            self.assertEqual(status, 413)

    def test_post_returns_405_even_for_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "hello.txt").write_bytes(b"hello")
            with running_server(Path(tmp)) as address:
                status, headers, _ = exchange(address, b"POST /hello.txt HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
            self.assertEqual(status, 405)
            self.assertEqual(headers["allow"], "GET, HEAD")

    def test_idle_client_receives_408_without_blocking_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            with running_server(Path(tmp)) as address:
                with socket.create_connection(address, timeout=2) as client:
                    client.settimeout(3)
                    client.sendall(b"GET / HTTP/1.1\r\nHost: ")
                    response = client.recv(4096)
            self.assertTrue(response.startswith(b"HTTP/1.1 408 "), response)

    def test_stream_timeout_does_not_append_second_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "file.bin").write_bytes(b"PAYLOAD")
            original_sendall = socket.socket.sendall

            def timeout_on_body(sock, data, *args, **kwargs):
                if data == b"PAYLOAD":
                    raise socket.timeout("simulated stalled stream")
                return original_sendall(sock, data, *args, **kwargs)

            with patch.object(socket.socket, "sendall", timeout_on_body):
                with running_server(Path(tmp)) as address:
                    status, _, body = exchange(address, b"GET /file.bin HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"")

    def test_read_permission_error_after_200_does_not_append_403(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "file.bin").write_bytes(b"PAYLOAD")
            original_fdopen = os.fdopen

            class FailingReader:
                def __init__(self, source):
                    self.source = source

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return self.source.__exit__(*args)

                def fileno(self):
                    return self.source.fileno()

                def read(self, size):
                    raise PermissionError(errno.EACCES, "simulated file read denial")

            def fail_file_read(fd, *args, **kwargs):
                return FailingReader(original_fdopen(fd, *args, **kwargs))

            with patch("lan_http.server.os.fdopen", side_effect=fail_file_read):
                with running_server(Path(tmp)) as address:
                    status, _, body = exchange(address, b"GET /file.bin HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"")

    def test_incomplete_request_returns_400_on_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            with running_server(Path(tmp)) as address:
                status, _, _ = exchange(address, b"GET / HTTP/1.1\r\nHost: localhost\r\n")
            self.assertEqual(status, 400)

    def test_double_slash_target_is_rejected_instead_of_being_reinterpreted(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "foo").write_bytes(b"foo")
            with running_server(Path(tmp)) as address:
                status, _, _ = exchange(address, b"GET //foo HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 400)

    def test_missing_host_header_returns_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Host:").write_bytes(b"not a header")
            with running_server(Path(tmp)) as address:
                status, _, _ = exchange(address, b"GET /Host: HTTP/1.1\r\nX-Thing: Host: fake\r\n\r\n")
            self.assertEqual(status, 400)

    def test_malformed_request_returns_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            with running_server(Path(tmp)) as address:
                status, _, _ = exchange(address, b"NOT A VALID REQUEST\r\n\r\n")
            self.assertEqual(status, 400)

    def test_invalid_target_encoding_returns_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            with running_server(Path(tmp)) as address:
                for target in (b"/caf\xe9", b"/file%00.txt"):
                    with self.subTest(target=target):
                        status, _, _ = exchange(address, b"GET " + target + b" HTTP/1.1\r\nHost: localhost\r\n\r\n")
                        self.assertEqual(status, 400)

    def test_missing_file_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            with running_server(Path(tmp)) as address:
                status, _, _ = exchange(address, b"GET /missing HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 404)

    def test_head_has_get_headers_but_no_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "hello.txt").write_bytes(b"hello network")
            with running_server(Path(tmp)) as address:
                status, headers, body = exchange(address, b"HEAD /hello.txt HTTP/1.1\r\nHost: localhost\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-length"], "13")
            self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
