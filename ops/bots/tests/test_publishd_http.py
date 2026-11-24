#!/usr/bin/env python3
"""End-to-end HTTP/ustar regression for the VM site publisher.

The previous parser ignored the last header because handle() stripped the
CRLFCRLF separator before header_map() saw it. A Content-Length last in the
request then returned 411, while an additional header after it returned 204.
Run against the actual C++ daemon, not a Python imitation of its parser.
"""
import io
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[3]
PUBLISHER = Path(os.environ.get("MAG_BOTS_PUBLISH_BIN", ROOT / "build/mag-bots-publish"))
TOKEN = "a" * 64


def archive(files):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w", format=tarfile.USTAR_FORMAT) as out:
        for name, contents in files.items():
            raw = contents.encode("utf-8")
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            item.mode = 0o644
            out.addfile(item, io.BytesIO(raw))
    return target.getvalue()


class PublisherHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PUBLISHER.is_file():
            raise RuntimeError("Build mag-bots-publish before running this test")
        cls.temp = tempfile.TemporaryDirectory(prefix="mag-publish-http-")
        cls.root = Path(cls.temp.name)
        (cls.root / "tokens").mkdir()
        for bot in ("agent2", "agent3"):
            (cls.root / "tokens" / bot).write_text(TOKEN + "\n")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        env = dict(os.environ, MAG_BOTS_ROOT=str(cls.root),
                   MAG_BOTS_PUBLISH_BIND="127.0.0.1",
                   MAG_BOTS_PUBLISH_PORT=str(cls.port), MAG_BOTS_TESTING="1")
        cls.proc = subprocess.Popen([str(PUBLISHER)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cls.proc.poll() is not None:
                cls.tearDownClass()
                raise AssertionError("publisher exited at startup")
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.03)
        else:
            cls.tearDownClass()
            raise AssertionError("publisher did not listen")

    @classmethod
    def tearDownClass(cls):
        proc = getattr(cls, "proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        temp = getattr(cls, "temp", None)
        if temp is not None:
            temp.cleanup()

    def upload(self, body, bot="agent2", length_position="last", token=TOKEN,
               length_value=None, split=False):
        fields = [
            ("Host", "127.0.0.1"),
            ("X-Mag-Bot", bot),
            ("Authorization", "Bearer " + token),
            ("Content-Type", "application/x-tar"),
        ]
        if length_position != "missing":
            field = ("Content-Length", str(len(body)) if length_value is None else length_value)
            if length_position == "middle":
                fields.insert(2, field)
            elif length_position == "mixed-case-last":
                fields.append(("cOnTeNt-LeNgTh", field[1]))
            else:
                fields.append(field)
        if length_position == "middle":
            fields.append(("X-After-Length", "test"))
        head = ("POST /publish HTTP/1.1\r\n" +
                "".join(f"{name}: {value}\r\n" for name, value in fields) +
                "\r\n").encode("ascii")
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as conn:
            conn.settimeout(3)
            if split:
                # Final header and body may arrive in separate TCP reads.
                conn.sendall(head[:-2])
                conn.sendall(head[-2:] + body)
            else:
                conn.sendall(head + body)
            response = bytearray()
            # The daemon may reject a request before reading its tar body.
            # Closing a socket with unread inbound bytes can yield TCP RST;
            # read the complete HTTP response HEADERS, not until socket EOF.
            while b"\r\n\r\n" not in response:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                response.extend(chunk)
        self.assertIn(b"\r\n\r\n", response, "complete HTTP response headers")
        return int(bytes(response).split(b" ", 2)[1])

    def test_content_length_as_final_header_for_both_bots(self):
        data = archive({"index.html": "<html>final length header</html>"})
        for bot in ("agent2", "agent3"):
            with self.subTest(bot=bot):
                self.assertEqual(self.upload(data, bot=bot, split=True), 204)
                self.assertEqual((self.root / "www" / bot / "index.html").read_text(),
                                 "<html>final length header</html>")

    def test_content_length_in_middle_and_case_insensitive(self):
        data = archive({"index.html": "<html>new</html>"})
        self.assertEqual(self.upload(data, length_position="middle"), 204)
        self.assertEqual(self.upload(data, length_position="mixed-case-last"), 204)

    def test_invalid_or_missing_length_still_rejected(self):
        data = archive({"index.html": "nothing should publish"})
        self.assertEqual(self.upload(data, length_position="missing"), 411)
        self.assertEqual(self.upload(data, length_value="not-a-number"), 411)

    def test_authentication_still_required(self):
        data = archive({"index.html": "not authorized"})
        self.assertEqual(self.upload(data, token="b" * 64), 403)

    def test_new_archive_replaces_old_site_without_stale_routes(self):
        first = archive({"index.html": "before", "old/unused.html": "obsolete"})
        second = archive({"index.html": "after"})
        self.assertEqual(self.upload(first), 204)
        self.assertTrue((self.root / "www" / "agent2" / "old/unused.html").exists())
        self.assertEqual(self.upload(second), 204)
        self.assertFalse((self.root / "www" / "agent2" / "old/unused.html").exists())
        self.assertEqual((self.root / "www" / "agent2" / "index.html").read_text(), "after")


class SiteProvisioning(unittest.TestCase):
    def test_fresh_vm_with_reused_name_gets_fresh_site(self):
        with tempfile.TemporaryDirectory(prefix="mag-site-spawn-") as work:
            root = Path(work)
            site = root / "www" / "agent1"
            site.mkdir(parents=True)
            (site / "index.html").write_text("previous owner's page")
            token = root / "tokens" / "agent1"
            token.parent.mkdir()
            token.write_text(TOKEN + "\n")
            guest = root / "guest"
            guest.mkdir()
            magbot = root / "magbot"
            magbot.write_text("""#!/bin/bash
set -e
case "$1" in
  spawn) [[ $2 == agent1 ]] ;;
  file-write)
    dest="$FAKE_GUEST/${3#/home/bot/}"
    mkdir -p "$(dirname "$dest")"
    cat > "$dest"
    ;;
  exec) test -f "$FAKE_GUEST/www/index.html" ;;
  *) exit 1 ;;
esac
""")
            magbot.chmod(0o755)
            env = dict(os.environ, MAG_BOTS_ROOT=work, MAG_BOTS_ZONE="example.test",
                       MAG_BOTS_MAGBOT=str(magbot), MAG_BOTS_SITE=str(ROOT / "ops/bots/site.sh"),
                       FAKE_GUEST=str(guest))
            subprocess.run(["bash", str(ROOT / "ops/magbot-agent-ctl"), "spawn", "agent1"],
                           check=True, env=env, capture_output=True, text=True)
            [archived] = (root / "archive").glob("agent1-*/index.html")
            self.assertEqual(archived.read_text(), "previous owner's page")
            self.assertNotEqual(token.read_text(), TOKEN + "\n")
            self.assertEqual((guest / ".site-token").read_text(), token.read_text())
            self.assertEqual((guest / "www" / "index.html").read_text(), (site / "index.html").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
