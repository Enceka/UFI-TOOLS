"""AT channel tests, including a fake ``atd.py`` and a real pty."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest

from ufitools.at import (
    ATError,
    ATRunner,
    CharDeviceBackend,
    CommandBackend,
    UnixSocketBackend,
    normalize_response,
)


class FakeAtd:
    """The subset of atd.py the backend talks to: one line in, lines out."""

    def __init__(self, path, responder):
        self.path = path
        self.responder = responder
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(4)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.commands = []

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def _loop(self):
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                command = data.decode().strip()
                self.commands.append(command)
                conn.sendall(self.responder(command).encode())


class UnixSocketBackendTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "atd.sock")
            with FakeAtd(path, lambda cmd: "+CSQ: 25,99\nOK\n"):
                backend = UnixSocketBackend(path)
                self.assertTrue(backend.available())
                self.assertEqual(backend.run("AT+CSQ", 3.0), "+CSQ: 25,99\nOK\n")

    def test_missing_socket_is_unavailable(self):
        backend = UnixSocketBackend("/nonexistent/atd.sock")
        self.assertFalse(backend.available())
        with self.assertRaises(ATError):
            backend.run("AT", 1.0)


class CommandBackendTests(unittest.TestCase):
    def test_template_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = os.path.join(tmp, "fake-at")
            with open(helper, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\necho \"got $1\"; echo OK\n")
            os.chmod(helper, 0o755)
            backend = CommandBackend("%s {cmd}" % helper)
            self.assertTrue(backend.available())
            output = backend.run("AT+CSQ", 3.0)
            self.assertIn("got AT+CSQ", output)


class CharDeviceBackendTests(unittest.TestCase):
    """The tty fallback is exercised on a pty that behaves like a modem."""

    def test_reads_until_final_token(self):
        try:
            master, slave = os.openpty()
        except OSError:  # pragma: no cover - platform without ptys
            self.skipTest("no pty available")
        device = os.ttyname(slave)
        # Keep the slave open: on macOS the pty is torn down (and the master's
        # read starts failing) if the last slave descriptor goes away.

        def modem():
            buffer = b""
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                if b"\r" in buffer or b"\n" in buffer:
                    os.write(master, b"AT+CSQ\r\r\n+CSQ: 25,99\r\n\r\nOK\r\n")
                    return

        worker = threading.Thread(target=modem, daemon=True)
        worker.start()
        try:
            backend = CharDeviceBackend(device)
            self.assertTrue(backend.available())
            output = backend.run("AT+CSQ", 2.0)
            self.assertIn("+CSQ: 25,99", output)
            self.assertIn("OK", output)
        finally:
            worker.join(timeout=1)
            os.close(master)
            os.close(slave)


class RunnerTests(unittest.TestCase):
    def test_rejects_non_at_command(self):
        runner = ATRunner(socket_path="/nonexistent", device="/nonexistent", command="")
        with self.assertRaises(ATError):
            runner.run("HELLO")

    def test_reports_no_backend(self):
        runner = ATRunner(socket_path="/nonexistent", device="/nonexistent", command="")
        self.assertFalse(runner.available())
        self.assertEqual(runner.describe(), "none")
        with self.assertRaises(ATError):
            runner.run("AT")

    def test_prefers_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "atd.sock")
            with FakeAtd(path, lambda cmd: "OK\n"):
                runner = ATRunner(socket_path=path, device="/nonexistent", command="")
                self.assertEqual(runner.describe(), "unix")
                self.assertIn("OK", runner.run("AT"))


class NormalizeResponseTests(unittest.TestCase):
    def test_strips_newlines_and_normalises_ok(self):
        self.assertEqual(normalize_response("+CSQ: 25,99\r\nOK\r\n"), "+CSQ: 25,99 OK")

    def test_escapes_quotes(self):
        self.assertEqual(normalize_response('+"A"\r\nOK'), '+\\"A\\" OK')

    def test_drops_leading_comma(self):
        self.assertEqual(normalize_response(",1,2\r\nOK"), "1,2 OK")

    def test_empty(self):
        self.assertEqual(normalize_response(""), "")


if __name__ == "__main__":
    unittest.main()
