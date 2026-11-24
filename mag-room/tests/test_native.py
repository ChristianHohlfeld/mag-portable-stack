#!/usr/bin/env python3
"""Room contract with scripted models (fake_llama.py): idle autonomy,
addressing, bounded bot dialogue and PASS, cancellation, live steps,
single-owner tool continuation, the trash button and message deletion.
The fixture proves the room's logic; it does not prove that a real model, a
real VM or the deployed browser work."""
import base64
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from fake_idol import FakeIdol  # noqa: E402
from fake_llama import FakeLlama, text_of  # noqa: E402
from fake_mcp import FakeMcp  # noqa: E402
from fake_scene import FakeScene  # noqa: E402

BIN = pathlib.Path(os.environ.get("MAGROOM_BIN", ROOT / "build/magroom"))
# A whole picture (1x1), and what arrives when a JPEG is cut off in its picture data.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xe1\x00\x08Exif\xff\xd9" +
        b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x12\x34\xff\x00\x56\xff\xd3\x78" * 40 + b"\xff\xd9")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def picture(red, green, blue):
    """A small picture of one colour, as a PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes((red, green, blue)) * 16 for _ in range(16))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def placed(parts, name):
    """The image part that stands directly in front of the line "[Bild <name> ..." of a request's content, or None."""
    for image, text in zip(parts, parts[1:]):
        if image["type"] == "image_url" and text["type"] == "text" and text["text"].startswith("[Bild " + name + " ("):
            return image["image_url"]["url"]
    return None


class FakeBrowser:
    """The VM browser the room calls when MAG_ROOM_BROWSER_ADDR is set."""

    def __init__(self):
        self.lock = threading.Lock()
        self.url = "about:blank"
        self.title = "leer"
        self.text = ""
        self.scroll = 0
        self.typed = []
        self.type_failures = 0
        self.screenshot_requests = 0
        self.password = False
        self.blocked = False
        self.clicks = []
        self.click_error = ""
        self.click_error_replacement = None
        self.scroll_texts = []
        self.scroll_i = 0
        self.elements = []
        self.links = []  # real DOM hrefs, including rendered items below viewport
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, body, kind):
                data = body if isinstance(body, bytes) else body.encode()
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _json(self, value):
                self._send(200, json.dumps(value), "application/json")

            def _page(self):
                with fake.lock:
                    els = []
                    for e in fake.elements:
                        row = dict(e)
                        if fake.password and row.get("n") == 1:
                            row["password"] = True
                            row["kind"] = "password"
                        els.append(row)
                    return {"url": fake.url, "title": fake.title, "text": fake.text, "scroll": fake.scroll,
                            "height": 4000, "vh": 900, "blocked": fake.blocked, "elements": els,
                            "links": list(fake.links)}

            def _read(self):
                n = int(self.headers.get("Content-Length", "0") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/screenshot.jpg":
                    with fake.lock:
                        fake.screenshot_requests += 1
                    return self._send(200, JPEG, "image/jpeg")
                if parsed.path == "/navigate":
                    url = urllib.parse.parse_qs(parsed.query).get("url", ["about:blank"])[0]
                    with fake.lock:
                        fake.url = url
                        if fake.password:
                            fake.title = "Anmelden"
                            fake.text = "Bitte melde dich an"
                        elif "amazon" in url:
                            fake.title = "Amazon.de"
                            fake.text = "Suche Schrumpfschlauch transparent"
                            fake._amazon()
                    return self._json({"ok": True})
                if parsed.path in ("/elements", "/read", "/state", "/back", "/key"):
                    if parsed.path == "/key":
                        return self._json({"ok": True})
                    return self._json(self._page())
                self._json({"error": "no such path"})

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                raw = self._read()
                body = json.loads(raw or b"{}")
                with fake.lock:
                    if parsed.path == "/click":
                        fake.clicks.append(body.get("n"))
                        if fake.click_error:
                            err = fake.click_error
                            if fake.click_error_replacement is not None:
                                fake.elements = fake.click_error_replacement
                                fake.click_error_replacement = None
                                fake.click_error = ""
                            return self._send(500, json.dumps({"error": err}), "application/json")
                    elif parsed.path == "/scroll":
                        fake.scroll += int(body.get("dy") or 0)
                        if fake.scroll_i < len(fake.scroll_texts):
                            fake.text = fake.scroll_texts[fake.scroll_i]
                            fake.scroll_i += 1
                    elif parsed.path == "/type":
                        fake.typed.append(dict(body))
                        if fake.type_failures:
                            fake.type_failures -= 1
                            return self._json({"ok": True, "typed_verified": False})
                        index = int(body.get("n") or 1)
                        for element in fake.elements:
                            if element.get("n") == index and not element.get("password"):
                                element["value"] = body.get("text", "")
                                break
                        return self._json({"ok": True, "typed_verified": True,
                                           "submitted": bool(body.get("enter"))})
                self._json({"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _amazon(self):
        self.elements = []
        for n in range(1, 41):
            self.elements.append({"n": n, "kind": "link", "label": "Treffer %d" % n, "x": 100, "y": 20 * n})
        self.elements[0] = {"n": 1, "kind": "search", "label": "Suche", "x": 400, "y": 40}
        self.elements[26] = {"n": 27, "kind": "link", "label": "PVC Schrumpfschlauch 2s 18650 transparent",
                             "price": "5,40 €", "href": "https://www.amazon.de/dp/B00SEEN2", "x": 200, "y": 340}
        self.elements[27] = {"n": 28, "kind": "link", "label": "Schrumpfschlauch transparent 2s",
                             "price": "4,20 €", "href": "https://www.amazon.de/dp/B00SEEN", "x": 200, "y": 360}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Room:
    """One magroom process with two scripted model servers."""

    def __init__(self, connect_events=True, **env):
        self.connect_events = connect_events
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="magroom-"))
        self.lena, self.noah, self.idol, self.scene = FakeLlama("Lena"), FakeLlama("Noah"), FakeIdol(), FakeScene()
        self.port = free_port()
        self.events = []
        # Nothing here may reach the real host: no sudo, a temp dir as "VM",
        # no firewall helper, no model supervisor, no Idol or scene worker.
        self.env = dict(os.environ, MAG_ROOM_STATE=str(self.tmp / "state"), MAG_ROOM_WEB=str(ROOT / "mag-room/web"),
                        MAG_ROOM_PORT=str(self.port), MAG_ROOM_BRIDGE_PORT="0", MAG_ROOM_GATEWAY="direct",
                        MAG_ROOM_BOT_ROUNDS="2",  # bounded legacy-contract fixture; production default 0 stays unlimited
                        MAG_LLM_PORT_LENA=str(self.lena.port), MAG_LLM_PORT_NOAH=str(self.noah.port),
                        MAG_MODELS_STATUS=str(self.tmp / "no-status.json"), MAG_MODELS_CONTROL=str(self.tmp / "control"),
                        MAG_IDOL_URL="http://127.0.0.1:%d" % self.idol.port, MAG_SCENE_URL="http://127.0.0.1:%d" % self.scene.port,
                        MAG_TEST_ALLOW_LOCAL_OWNER="1", MAG_ROOM_VERSION="test",
                        MAG_ROOM_NO_SUDO="1", MAG_ROOM_GUEST_RUN=str(HERE / "fake_guest_run"),
                        MAG_ROOM_AGENT_CTL="/bin/false", MAG_ROOM_NETMODE="/bin/true",
                        FAKE_GUEST_HOME=str(self.tmp / "guests"), MAG_ROOM_GUEST_HOME=str(self.tmp / "guests" / "agent1"),
                        MAG_BOTS_WWW=str(self.tmp / "www"),
                        MAG_ROOM_BRAVE_KEY="", BRAVE_API_KEY="")
        self.env.update({k: str(v) for k, v in env.items()})
        (self.tmp / "control").mkdir()
        (self.tmp / "state").mkdir()
        # Lena has a (fake) VM, Noah is chat-only.
        bot = lambda id, name, vm: {"id": id, "name": name, "vm": vm, "enabled": True, "thinking": False, "persona": "", "memory": []}
        (self.tmp / "state" / "bots.json").write_text(json.dumps([bot("lena", "Lena", "agent1"), bot("noah", "Noah", "")]))
        self.proc = None
        self.start()
        # The supervisor marks a model ready before its asynchronous slot
        # checkpoint restore / cold prefix prefill finishes. Do not let the
        # first scripted reply be consumed as a 1-token prefix warm-up.
        # This also makes the initial slot-action baseline deterministic.
        self.wait(lambda: all(b["kv"] == "warm" for b in self.state()["bots"]),
                  "initial model prefixes warmed", 25)
        self.idle()

    def start(self):
        self.log = open(self.tmp / "magroom.log", "a")
        self.proc = subprocess.Popen([str(BIN)], env=self.env, stderr=self.log)
        end = time.time() + 15
        while time.time() < end:
            try:
                with urllib.request.urlopen(self.url("/healthz"), timeout=1) as res:
                    res.read()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("magroom did not start: " + (self.tmp / "magroom.log").read_text())
        self.stream = None
        if self.connect_events:
            self.stream = urllib.request.urlopen(self.url("/api/events"), timeout=600)
            threading.Thread(target=self._listen, args=(self.stream,), daemon=True).start()
        # Both scripted models are "ready" once the room has probed them.
        self.wait(lambda: all(b["model"]["state"] == "ready" for b in self.state()["bots"]), "models ready")
        self.wait(lambda: not any(b["busy"] or b["phase"] != "idle" for b in self.state()["bots"]), "prefix warm-up")

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def api(self, method, path, body=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url(path), data=data, method=method)
        req.add_header("Sec-Fetch-Site", "same-origin")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                payload = res.read()
                return res.status, json.loads(payload) if payload[:1] in (b"{", b"[") else payload
        except urllib.error.HTTPError as err:
            with err:
                payload = err.read()
            return err.code, json.loads(payload) if payload[:1] == b"{" else payload

    def _listen(self, stream):
        try:
            for line in stream:
                if line.startswith(b"data: "):
                    self.events.append(json.loads(line[6:]))
        except (OSError, ValueError, AttributeError):
            pass  # the stream was closed with the room

    def state(self):
        return self.api("GET", "/api/state")[1]

    def messages(self):
        return self.state()["messages"]

    def say(self, text, **extra):
        code, out = self.api("POST", "/api/message", dict(text=text, **extra))
        assert code == 201, out
        return out["message"]

    def wait(self, predicate, what, seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            try:
                if predicate():
                    return
            except (KeyError, IndexError):
                pass
            time.sleep(0.05)
        # A timeout must show why the worker stayed busy; otherwise each CI
        # failure costs twenty seconds and only says "room idle".
        diagnostics = []
        try:
            with urllib.request.urlopen(self.url("/api/state"), timeout=2) as response:
                state = json.load(response)
            diagnostics.append("bots=" + repr([
                {k: bot.get(k) for k in ("id", "busy", "phase", "model")}
                for bot in state.get("bots", [])]))
        except Exception as err:
            diagnostics.append("state unavailable: " + repr(err))
        for name, model in (("lena", self.lena), ("noah", self.noah)):
            recent = model.chats()
            if recent:
                tail = recent[-1].get("messages", [])[-2:]
                diagnostics.append(name + " calls=" + str(len(recent)) +
                                   " last=" + repr(tail)[:750])
        log_path = self.tmp / "magroom.log"
        if log_path.exists():
            diagnostics.append("room log tail=" +
                               repr(log_path.read_text(errors="replace").splitlines()[-16:])[:1900])
        raise AssertionError("timeout waiting for " + what + "\\n" + "\\n".join(diagnostics))

    def idle(self, seconds=20):
        """Wait until no bot is working and nothing is queued."""
        quiet = [0]

        def settled():
            busy = any(b["busy"] for b in self.state()["bots"])
            quiet[0] = 0 if busy else quiet[0] + 1
            return quiet[0] >= 6
        self.wait(settled, "room idle", seconds)

    def texts(self, sender=None):
        return [m["text"] for m in self.messages() if sender is None or m["from"] == sender]

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        if self.stream is not None:
            self.stream.close()
        self.log.close()

    def close(self):
        self.stop()
        self.lena.close()
        self.noah.close()
        self.idol.close()
        self.scene.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class RoomContract(unittest.TestCase):
    def setUp(self):
        self.room = Room()
        self.addCleanup(self.room.close)

    def test_thinking_capability_survives_room_only_deploy_without_modeld_restart(self):
        """The current release has explicit model capabilities, while the
        already-running supervisor still publishes the old status schema."""
        with tempfile.TemporaryDirectory(prefix="mag-thinking-config-") as tmp:
            config = pathlib.Path(tmp) / "models.json"
            config.write_text(json.dumps({"bots": {
                "lena": {"thinking_control": "toggle"},
                "noah": {"thinking_control": "effort"},
            }}))
            r = Room(MAG_ROOM_MODELS_CONFIG=str(config))
            self.addCleanup(r.close)
            def statuses():
                return {b["id"]: b for b in r.state()["bots"]}
            self.assertEqual(statuses()["lena"]["model"]["thinking_control"], "toggle")
            self.assertEqual(statuses()["noah"]["model"]["thinking_control"], "effort")
            # A status file emitted by an older modeld has no thinking_control.
            status_file = pathlib.Path(r.env["MAG_MODELS_STATUS"])
            status_file.write_text(json.dumps({"updated": time.time(), "bots": {
                "lena": {"state": "ready", "since": 1},
                "noah": {"state": "ready", "since": 1},
            }}))
            r.wait(lambda: statuses()["lena"]["model"].get("thinking_control") == "toggle",
                   "legacy supervisor does not remove thinking toggle")
            self.assertEqual(statuses()["noah"]["model"]["thinking_control"], "effort")
            # An explicit, newer modeld capability takes precedence.
            status_file.write_text(json.dumps({"updated": time.time(), "bots": {
                "lena": {"state": "ready", "since": 1, "thinking_control": "none"},
                "noah": {"state": "ready", "since": 1, "thinking_control": "effort"},
            }}))
            r.wait(lambda: statuses()["lena"]["model"].get("thinking_control") == "none",
                   "explicit supervisor metadata must win")
            # The displayed effort default is actually saved on first enable.
            self.assertEqual(r.api("PUT", "/api/bot/noah", {"thinking": True})[0], 200)
            saved = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "bots.json").read_text())
            noah = next(bot for bot in saved if bot["id"] == "noah")
            self.assertEqual(noah["effort"], "low")
            self.assertTrue(noah["thinking"])

    def test_auto_chat_off_cuts_off_human_started_bot_followup_now(self):
        """OFF stops even a Chris-initiated bot exchange, not just auto_burst_."""
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        bot_started, release = threading.Event(), threading.Event()

        def reply(who):
            def respond(body):
                last = text_of(body["messages"][-1].get("content", ""))
                if "Du wurdest nicht direkt angesprochen" in last:
                    bot_started.set()
                    if not release.wait(15):
                        raise AssertionError("release blocked bot followup")
                    return "Diese bereits gestoppte Folgerunde darf nicht erscheinen."
                return "Direkte Antwort von %s." % who
            return respond

        r.lena.reply, r.noah.reply = reply("Lena"), reply("Noah")
        self.assertEqual(r.api("PUT", "/api/settings", {
            "bot_rounds": 0, "pass_enabled": False, "auto_chat": False})[0], 200)
        try:
            r.say("Hallo euch beiden")
            self.assertTrue(bot_started.wait(25), "a human-started bot followup must be in flight")
            # Deliberately start with auto_chat off: this is NOT an autonomous
            # burst. Toggling it on and off must cut the CURRENT conversation.
            self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": True})[0], 200)
            self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)
        finally:
            release.set()
        r.idle(25)
        time.sleep(0.4)
        self.assertEqual(sorted(r.texts("lena") + r.texts("noah")),
                         ["Direkte Antwort von Lena.", "Direkte Antwort von Noah."])
        count = len(r.lena.chats()) + len(r.noah.chats())
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), count,
                         "after_turn must not reschedule a stopped exchange")
        # A later explicit human message opens a fresh conversation again.
        r.lena.reply = r.noah.reply = lambda body: "Neue direkte Antwort."
        r.say("Neue Frage")
        r.idle(25)
        self.assertGreaterEqual(r.texts("lena").count("Neue direkte Antwort."), 1)
        self.assertGreaterEqual(r.texts("noah").count("Neue direkte Antwort."), 1)

    def test_lowering_bot_rounds_cuts_off_already_started_followup(self):
        """Changing 0 (unlimited) to the already reached cap is immediate."""
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        bot_started, release = threading.Event(), threading.Event()

        def answer(who):
            def respond(body):
                last = text_of(body["messages"][-1].get("content", ""))
                if "Du wurdest nicht direkt angesprochen" in last:
                    bot_started.set()
                    if not release.wait(15):
                        raise AssertionError("release blocked bot reply")
                    return "Die Runde jenseits des neuen Limits."
                return "Antwort von %s." % who
            return respond

        r.lena.reply, r.noah.reply = answer("Lena"), answer("Noah")
        self.assertEqual(r.api("PUT", "/api/settings", {
            "bot_rounds": 0, "pass_enabled": False, "auto_chat": False})[0], 200)
        try:
            r.say("Redet kurz miteinander")
            self.assertTrue(bot_started.wait(25), "first counted bot followup started")
            self.assertEqual(r.api("PUT", "/api/settings", {"bot_rounds": 1})[0], 200)
        finally:
            release.set()
        r.idle(25)
        time.sleep(0.4)
        self.assertEqual(sorted(r.texts("lena") + r.texts("noah")),
                         ["Antwort von Lena.", "Antwort von Noah."])
        count = len(r.lena.chats()) + len(r.noah.chats())
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), count)

    def test_auto_chat_off_does_not_cancel_direct_human_answers(self):
        """A still-streaming human reply finishes but cannot wake bot peers."""
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        human_started, release = threading.Event(), threading.Event()

        def answer(who):
            def respond(body):
                last = text_of(body["messages"][-1].get("content", ""))
                if "Du wurdest nicht direkt angesprochen" in last:
                    return "Ungewollte Bot-Runde."
                human_started.set()
                if not release.wait(15):
                    raise AssertionError("release blocked human reply")
                return "Geschuetzte Antwort von %s." % who
            return respond

        r.lena.reply, r.noah.reply = answer("Lena"), answer("Noah")
        try:
            r.say("Hallo ihr beiden")
            self.assertTrue(human_started.wait(25), "human response is in flight")
            self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": True})[0], 200)
            self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)
        finally:
            release.set()
        r.idle(25)
        time.sleep(0.5)
        self.assertEqual(sorted(r.texts("lena") + r.texts("noah")),
                         ["Geschuetzte Antwort von Lena.", "Geschuetzte Antwort von Noah."])

    def test_idle_room_calls_no_model(self):
        r = self.room
        time.sleep(1.5)
        self.assertEqual(r.lena.chats(), [])
        self.assertEqual(r.noah.chats(), [])
        self.assertEqual(r.messages(), [])

    def test_unaddressed_message_is_answered_by_all_bots_then_rests(self):
        r = self.room
        r.say("Hi beide")
        r.idle()
        self.assertEqual(sorted(m["from"] for m in r.messages()), ["chris", "lena", "noah"])
        self.assertEqual(r.messages()[0]["from"], "chris")  # answers come below the question
        # Each bot read the other's answer once and chose PASS: invisible, and the room rests.
        self.assertNotIn("PASS", " ".join(r.texts()))
        self.assertEqual(len(r.lena.chats()), 2)
        self.assertEqual(len(r.noah.chats()), 2)
        self.assertIn("[Sprecher Chris] Hi beide", r.lena.chats()[0]["messages"][-1]["content"])
        self.assertTrue(any("[Sprecher Noah] Hallo von Noah." in text_of(m.get("content"))
                            for m in r.lena.chats()[1]["messages"]),
                        "Lena sees Noah in the preserved chat even when a request-local bot cue follows")
        time.sleep(1.0)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), 4)

    def test_pass_toggle_on_keeps_invisible_self_termination(self):
        r = self.room
        self.assertTrue(r.state()["settings"]["pass_enabled"])
        r.lena.reply = lambda body: "Ein erster Gedanke."
        r.noah.reply = lambda body: "PASS"
        r.say("Lena, wie geht es dir?")
        r.idle()
        self.assertEqual(r.texts("lena"), ["Ein erster Gedanke."])
        self.assertEqual(r.texts("noah"), [])
        self.assertEqual(len(r.noah.chats()), 1, "PASS on must not retry")
        self.assertNotIn("PASS", " ".join(r.texts()))

    def test_pass_toggle_off_retries_bot_reply_and_keeps_controls_hidden(self):
        r = self.room
        attempts = [0]
        r.lena.reply = lambda body: "Ein erster Gedanke."

        def noah(body):
            attempts[0] += 1
            return "PASS" if attempts[0] == 1 else "Eine eigenständige Ergänzung."

        r.noah.reply = noah
        code, settings = r.api("PUT", "/api/settings", {
            "pass_enabled": False, "bot_rounds": 1})
        self.assertEqual(code, 200)
        self.assertFalse(settings["pass_enabled"])
        self.assertFalse(r.state()["settings"]["pass_enabled"])
        r.say("Lena, wie geht es dir?")
        r.idle()
        self.assertEqual(r.texts("noah"), ["Eine eigenständige Ergänzung."])
        self.assertEqual(len(r.noah.chats()), 2, "PASS off must ask for a real contribution once")
        self.assertTrue(any("eigene Beendigungsentscheidungen" in
                            str(msg.get("content", ""))
                            for msg in r.noah.chats()[-1]["messages"]))
        self.assertNotIn("PASS", " ".join(r.texts()))
        # The setting is durable and can change independently of auto-chat.
        self.assertFalse(json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) /
                                    "settings.json").read_text())["pass_enabled"])
        self.assertTrue(r.api("PUT", "/api/settings", {"pass_enabled": True})[1]["pass_enabled"])

    def test_pass_toggle_off_rejects_end_marker_in_unattended_no_browser_chat(self):
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        self.assertIsNone(r.stream)
        attempts = {"lena": 0, "noah": 0}

        def respond(bot):
            def reply(body):
                attempts[bot] += 1
                return "[[MAG:ENDE]]" if attempts[bot] == 1 else "Neuer Gedanke von " + bot
            return reply

        r.lena.reply, r.noah.reply = respond("lena"), respond("noah")
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1, "pass_enabled": False,
            "web": False})[0], 200)
        r.wait(lambda: len([m for m in r.messages()
                            if m["from"] in ("lena", "noah") and m["state"] == "done"]) >= 2,
               "autonomous bots respond after invalid end markers", 35)
        r.idle()
        self.assertEqual(attempts, {"lena": 2, "noah": 2})
        self.assertEqual(sorted(m["from"] for m in r.messages()), ["lena", "noah"])
        self.assertNotIn("[[MAG:", " ".join(r.texts()))
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_autonomous_pass_policy_symmetric_across_bots(self):
        """Either bot can start and follow up, with identical PASS-off rules.
        No browser or SSE connection is needed."""
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        self.assertIsNone(r.stream)

        replies = {"Lena": 0, "Noah": 0}

        def make_reply(name):
            def reply(body):
                last = str(body["messages"][-1].get("content", ""))
                self.assertIn("eigene Beendigungsentscheidung", last)
                self.assertIn("Chris kann alle eure Nachrichten im Raum-Chat lesen", last)
                self.assertIn("Projekte", last, "autonomous turns must not revive completed projects")
                replies[name] += 1
                return "Ein neuer Gedanke von %s, Runde %d." % (name, replies[name])
            return reply

        r.lena.reply, r.noah.reply = make_reply("Lena"), make_reply("Noah")
        settings = {"auto_chat": True, "bot_rounds": 1,
                    "pass_enabled": False, "auto_chat_visible": True, "web": False}
        self.assertEqual(r.api("PUT", "/api/settings", settings)[0], 200)
        r.wait(lambda: len([m for m in r.messages()
                            if m["from"] in ("lena", "noah") and m["state"] == "done"]) >= 2,
               "first autonomous session with both bots", 35)
        r.idle()
        first = [m for m in r.messages() if m["from"] in ("lena", "noah")]
        self.assertEqual([(m["from"], m["reason"]) for m in first],
                         [("lena", "autonomous"), ("noah", "bot")])

        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": True})[0], 200)
        r.wait(lambda: len([m for m in r.messages()
                            if m["from"] in ("lena", "noah") and m["state"] == "done"]) >= 4,
               "second autonomous session with reversed bot order", 35)
        r.idle()
        all_bots = [m for m in r.messages() if m["from"] in ("lena", "noah")]
        self.assertEqual([(m["from"], m["reason"]) for m in all_bots[2:4]],
                         [("noah", "autonomous"), ("lena", "bot")])
        self.assertTrue(all("PASS" not in m["text"] for m in all_bots))
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_pass_toggle_off_fails_safely_after_two_passes(self):
        r = self.room
        r.lena.reply = lambda body: "Ein erster Gedanke."
        r.noah.reply = lambda body: "PASS"
        self.assertEqual(r.api("PUT", "/api/settings", {
            "pass_enabled": False, "bot_rounds": 1})[0], 200)
        r.say("Lena, wie geht es dir?")
        r.idle()
        self.assertEqual(len(r.noah.chats()), 2,
                         "do not spend unlimited CPU attempts on a non-cooperating model")
        self.assertTrue(any(m["state"] == "error" and "technisch unterbrochen" in m["text"]
                            for m in r.messages() if m["from"] == "noah"))
        self.assertNotIn("PASS", " ".join(r.texts()))

    def test_autonomous_round_limit_counts_followups_not_the_opener(self):
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        counter = [0]

        def answer(body):
            counter[0] += 1
            return "Neuer Satz Nummer %d." % counter[0]

        r.lena.reply = r.noah.reply = answer
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1,
            "pass_enabled": False, "web": False})[0], 200)
        r.wait(lambda: len([m for m in r.messages()
                            if m["from"] in ("lena", "noah") and m["state"] == "done"]) == 2,
               "one starter and one follow-up", 35)
        r.idle()
        messages = [m for m in r.messages() if m["from"] in ("lena", "noah")]
        self.assertEqual(len(messages), 2, "opening is not a follow-up round")
        self.assertEqual(sorted(m["from"] for m in messages), ["lena", "noah"])
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_autonomous_tool_call_cannot_end_dialogue_or_run_tool(self):
        """The first bot's prefatory text and native tool call must be
        rejected before a chat bubble or tool execution is persisted."""
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        attempts = [0]

        def lena(body):
            attempts[0] += 1
            if attempts[0] == 1:
                return {"content": "Ich starte jetzt ein Werkzeug.",
                        "tool_calls": [{"name": "shell",
                                        "arguments": {"command": "echo should-not-execute"}}]}
            return "Ein eigener Gedanke ohne Werkzeuge."

        r.lena.reply = lena
        r.noah.reply = lambda body: "Ich denke darüber anders."
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1,
            "pass_enabled": False, "web": False})[0], 200)
        r.wait(lambda: len([m for m in r.messages()
                            if m["from"] in ("lena", "noah") and m["state"] == "done"]) >= 2,
               "both models continue after denied tool", 35)
        r.idle()
        self.assertEqual(attempts[0], 2, "forbidden native call triggers one correction")
        self.assertEqual(r.texts("lena"), ["Ein eigener Gedanke ohne Werkzeuge."])
        self.assertEqual(r.texts("noah"), ["Ich denke darüber anders."])
        self.assertFalse(any(p.get("k") == "step"
                             for m in r.messages()
                             for p in m.get("parts", [])),
                         "no unattended tool action can reach room history")
        self.assertTrue(any("Dieser Werkzeugaufruf ist im Bot-Gespräch" in
                            str(msg.get("content", "")) for req in r.lena.chats()
                            for msg in req["messages"]),
                        "one corrected request must have a grounded no-tool instruction; last requests=" +
                        repr([[m.get("content") for m in req.get("messages", [])[-4:]]
                              for req in r.lena.chats()[-3:]]))
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_autonomous_chat_restarts_after_cooldown_without_browser(self):
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="5")
        self.addCleanup(r.close)
        r.lena.reply = r.noah.reply = lambda body: "PASS"
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "pass_enabled": True, "web": False})[0], 200)
        r.wait(lambda: len(r.lena.chats()) >= 1,
               "first quiet unattended session", 20)
        r.wait(lambda: len(r.noah.chats()) >= 1,
               "second quiet unattended session rotates the starter", 20)
        self.assertEqual(r.messages(), [], "PASS must stay invisible")
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_autonomous_chat_can_start_without_human_or_browser(self):
        # No SSE socket and no frontend: only the room and its two workers.
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="5")
        self.addCleanup(r.close)
        self.assertIsNone(r.stream)
        self.assertEqual(r.messages(), [])
        r.lena.reply = lambda body: "Noah, welche neue Idee hast du?"
        r.noah.reply = lambda body: "Ein interessanter Gedanke: Wir können uns ohne Chris unterhalten."
        self.assertEqual(r.api("PUT", "/api/settings", {
            "web": False, "auto_chat": True, "bot_rounds": 3})[0], 200)
        r.wait(lambda: any(m.get("from") == "lena" or m.get("from") == "noah"
                           for m in r.messages()), "spontaneous conversation", 25)
        r.wait(lambda: any(m.get("from") == "noah" for m in r.messages()) and
                       any(m.get("from") == "lena" for m in r.messages()),
               "both bots speak without a browser", 30)
        self.assertEqual([m for m in r.messages() if m.get("from") == "chris"], [])
        prompts = json.dumps(r.lena.chats() + r.noah.chats(), ensure_ascii=False)
        self.assertIn("Warte nicht auf einen Auftrag", prompts)
        self.assertIn("Jetzt ist Zeit für eigene Interessen", prompts)
        self.assertNotIn("Dein eigenes Ziel wartet", prompts)
        self.assertNotIn("In diesem Zug gilt die Bitte von Chris", prompts)
        self.assertNotIn("Chris kann sie jederzeit selbst erneut ansprechen", prompts)
        self.assertIn("Prüfe Annahmen und widersprich sachlich", prompts)
        self.assertIn("Sprich klar, einfach und auf Augenhöhe", prompts)
        self.assertIn("Was ist bekannt, warum gilt es, was folgt daraus?", prompts)
        self.assertNotIn("Beginne ein eigenständiges, neues Gesprächsthema", prompts)
        self.assertLessEqual(len(r.lena.chats()) + len(r.noah.chats()), 4)
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_autonomous_chat_unlimited_continues_past_both_hidden_caps(self):
        """Unattended zero-limit conversations have no 4-turn or 24-turn fuse,
        and both bots know the room is readable even without a browser."""
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        self.assertIsNone(r.stream)
        turns = [0]

        def converse(body):
            turns[0] += 1
            return "Ein weiterer Gedanke %d." % turns[0]

        r.lena.reply = r.noah.reply = converse
        self.assertEqual(r.api("PUT", "/api/settings", {
            "web": False, "auto_chat": True, "bot_rounds": 0})[0], 200)
        r.wait(lambda: len([m for m in r.messages()
                            if m["from"] in ("lena", "noah") and m["state"] == "done"]) >= 26,
               "more than 24 visible unattended bot turns", 55)
        r.wait(lambda: len([m for m in r.messages()
                            if m["from"] in ("lena", "noah") and m["state"] == "done"]) >= 29,
               "unlimited dialogue continues until the owner stops it", 35)
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)
        r.idle(25)
        self.assertGreaterEqual(len(r.lena.chats()) + len(r.noah.chats()), 29)
        self.assertEqual([m for m in r.messages() if m["from"] == "chris"], [])
        for model in (r.lena, r.noah):
            self.assertTrue(any("Chris kann alle eure Nachrichten im Raum-Chat lesen" in
                                str(m.get("content", ""))
                                for req in model.chats() for m in req["messages"]))
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_live_self_talk_switches_reach_both_bots_on_next_turn(self):
        """Flip the four settings while a conversation is IN FLIGHT.
        Existing warm wire stays; each new request must append current policy.
        """
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        lena_started, noah_started = threading.Event(), threading.Event()
        lena_release, noah_release = threading.Event(), threading.Event()
        seen = {"lena": [], "noah": []}

        def response(bot):
            def run(body):
                notes = [str(m.get("content", "")) for m in body["messages"]
                         if m.get("role") == "user"]
                seen[bot].append(notes[-1] if notes else "")
                if bot == "lena" and len(seen[bot]) == 1:
                    lena_started.set()
                    if not lena_release.wait(15):
                        raise AssertionError("first turn release timeout")
                if bot == "noah" and len(seen[bot]) == 1:
                    noah_started.set()
                    if not noah_release.wait(15):
                        raise AssertionError("second turn release timeout")
                return "Neuer Gedanke von %s, Antwort %d." % (bot, len(seen[bot]))
            return run

        r.lena.reply, r.noah.reply = response("lena"), response("noah")
        self.assertEqual(r.api("PUT", "/api/settings", {
            "web": False, "auto_chat": True, "bot_rounds": 1,
            "pass_enabled": True, "auto_chat_visible": False})[0], 200)
        try:
            self.assertTrue(lena_started.wait(25), "Lena must start autonomous chat")
            # While Lena speaks: enable readership, unlimited replies,
            # and disable voluntary PASS. The FOLLOWING Noah turn sees all.
            self.assertEqual(r.api("PUT", "/api/settings", {
                "auto_chat_visible": True, "bot_rounds": 0,
                "pass_enabled": False})[0], 200)
            lena_release.set()
            self.assertTrue(noah_started.wait(25), "Noah must get next turn")
            self.assertIn("Chris kann alle eure Nachrichten im Raum-Chat lesen", seen["noah"][0])
            self.assertIn("PASS und eigene Beendigungsentscheidungen sind deaktiviert",
                          seen["noah"][0])
            # Now revoke both while Noah's reply is IN FLIGHT.
            self.assertEqual(r.api("PUT", "/api/settings", {
                "auto_chat_visible": False, "pass_enabled": True})[0], 200)
            noah_release.set()
            r.wait(lambda: len(seen["lena"]) >= 2,
                   "Lena's next bot turn must read new policy", 35)
            latest = seen["lena"][1]
            self.assertIn("für diesen Gesprächszug deaktiviert", latest)
            # Unlimited rounds intentionally disable PASS, regardless of the
            # stored switch. The new bot turn must reflect that effective policy.
            self.assertIn("PASS und eigene Beendigungsentscheidungen sind deaktiviert", latest)
            self.assertNotIn("Wenn die Unterhaltung erschöpft ist, antworte exakt PASS", latest)
            self.assertFalse("Chris kann alle eure Nachrichten im Raum-Chat lesen" in latest,
                             "old observer notice may remain in history, NOT the latest directive")
            self.assertEqual(r.state()["settings"]["bot_rounds"], 0)
        finally:
            lena_release.set()
            noah_release.set()
            r.api("PUT", "/api/settings", {"auto_chat": False})

    def test_human_started_bot_reply_inherits_observer_setting(self):
        """A human-started discussion also tells BOTH peers about readership."""
        r = self.room
        r.lena.reply = lambda body: "Lena hat dazu einen neuen Gedanken."
        r.noah.reply = lambda body: "Noah ergänzt einen weiteren Gedanken."
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat_visible": True, "pass_enabled": False,
            "bot_rounds": 2})[0], 200)
        r.say("Redet miteinander")
        r.idle()
        for model in (r.lena, r.noah):
            followups = [req for req in model.chats() if any(
                "Du wurdest nicht direkt angesprochen" in str(m.get("content", ""))
                for m in req.get("messages", []))]
            if followups:
                self.assertTrue(any(
                    "Chris kann alle eure Nachrichten im Raum-Chat lesen" in
                    str(m.get("content", ""))
                    for req in followups for m in req["messages"]))

    def test_autonomous_observer_cue_can_be_disabled_independently(self):
        r = Room(MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        r.lena.reply = r.noah.reply = lambda body: "Eine eigene Überlegung."
        self.assertEqual(r.api("PUT", "/api/settings", {
            "web": False, "auto_chat": True, "bot_rounds": 2,
            "auto_chat_visible": False})[0], 200)
        self.assertFalse(r.state()["settings"]["auto_chat_visible"])
        r.wait(lambda: any(m["from"] == "noah" for m in r.messages()) and
                       any(m["from"] == "lena" for m in r.messages()),
               "two autonomous turns without observer cue", 35)
        for model in (r.lena, r.noah):
            self.assertFalse(any("Chris kann alle eure Nachrichten im Raum-Chat lesen" in
                                 str(m.get("content", ""))
                                 for req in model.chats() for m in req["messages"]))
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_autonomous_chat_respects_paused_bot(self):
        r = Room(MAG_ROOM_AUTO_CHAT_INTERVAL_S="5")
        self.addCleanup(r.close)
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"enabled": False})[0], 200)
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": True})[0], 200)
        time.sleep(6)
        self.assertEqual(r.lena.chats(), [])
        self.assertEqual(r.noah.chats(), [])

    def test_named_bot_answers_alone(self):
        r = self.room
        r.say("Lena, wie geht es dir?")
        r.idle()
        self.assertEqual([m["from"] for m in r.messages()], ["chris", "lena"])
        # Noah was not asked; he reads Lena's answer and may pass.
        self.assertEqual(len(r.lena.chats()), 1)
        self.assertEqual(len(r.noah.chats()), 1)

    def test_mention_asks_one_bot_and_the_other_only_reads(self):
        r = self.room
        first = r.say("@Lena wie geht es dir?")
        r.idle()
        time.sleep(1.0)
        self.assertEqual(first["mentions"], ["lena"])
        self.assertEqual([m["from"] for m in r.messages()], ["chris", "lena"])
        # Noah is not asked at all: no turn, not even one he could pass.
        self.assertEqual((len(r.lena.chats()), len(r.noah.chats())), (1, 0))
        # Without a mention both answer again, and Noah reads whom the first message was for.
        r.say("und was gibt es Neues?")
        r.idle()
        noah = json.dumps(r.noah.chats()[0]["messages"], ensure_ascii=False)
        self.assertIn("[Sprecher Chris an Lena] @Lena wie geht es dir?", noah)
        self.assertNotIn("Chris an Lena", json.dumps(r.lena.chats()[-1]["messages"], ensure_ascii=False))
        self.assertIn("lena", [m["from"] for m in r.messages()[2:]])
        self.assertIn("noah", [m["from"] for m in r.messages()[2:]])

    def test_two_mentions_mean_both_and_a_mention_follows_a_rename(self):
        r = self.room
        both = r.say("@Lena @Noah seid ihr da?")
        r.idle()
        self.assertEqual(both["mentions"], ["lena", "noah"])
        self.assertEqual(sorted(m["from"] for m in r.messages()[1:3]), ["lena", "noah"])
        self.assertEqual(r.say("mail an chris@lena.example und @alle")["mentions"], ["lena", "noah"])  # an address is no mention, "@alle" is
        r.idle()
        # Lena becomes Luna: the new name works, and the old one still means her.
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"name": "Luna"})[0], 200)
        r.idle()
        before = (len(r.lena.chats()), len(r.noah.chats()))
        self.assertEqual(r.say("@Luna hallo")["mentions"], ["lena"])
        r.idle()
        self.assertEqual(r.say("@lena noch da?")["mentions"], ["lena"])
        r.idle()
        time.sleep(1.0)
        self.assertGreater(len(r.lena.chats()), before[0])
        self.assertEqual(len(r.noah.chats()), before[1])
        self.assertEqual([b["aliases"] for b in r.state()["bots"] if b["id"] == "lena"], [["lena"]])

    def test_paused_bot_stays_out_until_it_is_resumed(self):
        r = self.room
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"enabled": False})[0], 200)
        r.say("Hi beide")
        r.idle()
        time.sleep(1.0)
        # Only Lena answers; Noah makes no model request while he is paused.
        self.assertEqual([m["from"] for m in r.messages()], ["chris", "lena"])
        self.assertEqual(len(r.noah.chats()), 0)
        # Asked by name he still does not answer, and Chris is told why.
        r.say("@Noah bist du da?")
        r.idle()
        self.assertTrue(any(e.get("t") == "notice" and "Noah ist pausiert" in e.get("text", "") for e in r.events), [e for e in r.events if e.get("t") == "notice"])
        self.assertEqual(len(r.noah.chats()), 0)
        # The pause outlives a restart of the room (a deploy).
        r.stop()
        r.start()
        self.assertEqual([b["enabled"] for b in r.state()["bots"] if b["id"] == "noah"], [False])
        # Back in: he answers the next message and has read what he missed.
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"enabled": True})[0], 200)
        r.say("und jetzt alle zusammen")
        r.idle()
        self.assertIn("noah", [m["from"] for m in r.messages()])
        self.assertIn("Hi beide", json.dumps(r.noah.chats()[0]["messages"], ensure_ascii=False))

    def test_pausing_mid_answer_suppresses_late_output_and_dispatch(self):
        r = self.room
        entered, release = threading.Event(), threading.Event()

        def slow_noah(body):
            entered.set()
            release.wait(10)
            return "Antwort nach Pause darf nicht erscheinen"

        r.noah.reply = slow_noah
        r.say("@Noah starte eine Antwort")
        r.wait(entered.is_set, "Noah began answering")
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"enabled": False})[0], 200)
        release.set()
        r.idle(30)
        self.assertEqual(len([m for m in r.messages() if m["from"] == "noah" and
                              "Antwort nach Pause" in m.get("text", "")]), 0)
        previous = len(r.noah.chats())
        r.say("@Noah bist du noch da?")
        r.idle()
        self.assertEqual(len(r.noah.chats()), previous, "explicit mentions must not wake a paused model")
        self.assertEqual([b["enabled"] for b in r.state()["bots"] if b["id"] == "noah"], [False])

    def test_paused_bot_discards_interrupted_turn_on_restart(self):
        """A prior at_work marker must not revive a paused bot across deploys
        or silently replay that request when the owner later resumes it."""
        r = self.room
        r.stop()
        bots = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "bots.json"
        config = json.loads(bots.read_text())
        for b in config:
            if b["id"] == "noah":
                b["enabled"] = False
        bots.write_text(json.dumps(config))
        wire = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "noah.json"
        self.assertTrue(wire.exists(), "warmup should have written Noah's wire")
        state = json.loads(wire.read_text())
        state["at_work"] = {"reason": "human", "tries": 0}
        state["fresh"] = False
        wire.write_text(json.dumps(state))
        r.start()
        r.idle()
        self.assertEqual(len(r.noah.chats()), 0, "paused model must not resume a prior turn")
        after = json.loads(wire.read_text())
        self.assertNotIn("at_work", after)
        self.assertFalse(after.get("fresh", False))
        self.assertIn("discarded interrupted turn while paused", (r.tmp / "magroom.log").read_text())
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"enabled": True})[0], 200)
        r.idle()
        self.assertEqual(len(r.noah.chats()), 0, "unpausing alone may not replay old work")
        r.say("@Noah neue Frage")
        r.idle()
        self.assertEqual(len(r.noah.chats()), 1, "only a new explicit request should resume the bot")

    def test_speaker_labels_are_plain_and_copied_tags_are_cleaned(self):
        r = self.room
        r.lena.reply = lambda body: "Hallo [Chris], [Noah · Di 10.11. 01:51] meinte das auch. Siehe [Doku](https://example.org)."
        r.say("@Lena hallo")
        r.idle()
        # A bracketed speaker tag the model copies never reaches the chat; links stay links.
        self.assertEqual(r.texts("lena"), ["Hallo Chris, Noah meinte das auch. Siehe [Doku](https://example.org)."])
        sent = r.lena.chats()[0]["messages"]
        self.assertRegex(sent[-1]["content"], r"^\(\w\w \d\d\.\d\d\. \d\d:\d\d\)\n\[Sprecher Chris\] @Lena hallo$")
        self.assertNotIn("[Chris", json.dumps(sent, ensure_ascii=False))

    def test_bot_dialogue_is_bounded_by_bot_rounds(self):
        r = self.room
        r.lena.reply = r.noah.reply = lambda body: "Noch ein Gedanke %d." % len(body["messages"])
        self.assertEqual(r.api("PUT", "/api/settings", {"bot_rounds": 3})[0], 200)
        r.say("Diskutiert das")
        r.idle()
        bots = [m for m in r.messages() if m["from"] != "chris"]
        human_turns = [m for m in bots if m["reason"] == "human"]
        self.assertEqual(len(human_turns), 2)
        self.assertEqual(len(bots) - len(human_turns), 3)

    def test_new_human_message_preempts_bot_rounds(self):
        r = self.room
        r.lena.delay = r.noah.delay = 0.05
        r.lena.reply = r.noah.reply = lambda body: "Lange Antwort " * 20
        r.say("Redet miteinander")
        # Bot text is withheld until validated. Wait for the actual second
        # model request (the running follow-up), not a visible chat bubble.
        r.wait(lambda: any(len(model.chats()) >= 2 for model in (r.lena, r.noah)),
               "a running bot round")
        r.lena.reply = r.noah.reply = None
        r.lena.delay = r.noah.delay = 0
        second = r.say("@Noah stopp, neue Frage")
        r.idle()
        after = [m for m in r.messages() if m["id"] > second["id"] and m["state"] == "done"]
        self.assertEqual([m["from"] for m in after], ["noah"])

    def test_stop_cancels_a_running_answer(self):
        r = self.room
        r.lena.delay = 0.1
        r.lena.reply = lambda body: "Wort " * 200
        r.say("@Lena erzähl")
        r.wait(lambda: any(m["from"] == "lena" and m["state"] == "streaming" for m in r.messages()), "streaming")
        self.assertEqual(r.api("POST", "/api/stop", {})[0], 200)
        r.idle()
        lena = [m for m in r.messages() if m["from"] == "lena"][0]
        self.assertEqual(lena["state"], "stopped")
        self.assertLess(len(lena["text"]), 5 * 200)
        calls = len(r.lena.chats()) + len(r.noah.chats())
        time.sleep(1.0)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), calls)  # nothing continues on its own

    def test_tool_steps_are_parts_of_the_message_in_order(self):
        r = self.room
        script = iter([{"content": "Ich merke mir das.", "tool_calls": [{"name": "remember", "arguments": {"fact": "Chris mag Tee"}}]},
                       "Gemerkt, erledigt."])
        r.lena.reply = lambda body: next(script)
        r.say("@Lena merk dir, dass ich Tee mag")
        r.idle()
        lena = [m for m in r.messages() if m["from"] == "lena"][0]
        self.assertEqual([p["k"] for p in lena["parts"]], ["text", "step", "text"])
        step = lena["parts"][1]
        self.assertEqual((step["tool"], step["state"], step["input"]), ("remember", "ok", {"fact": "Chris mag Tee"}))
        self.assertGreaterEqual(step["t1"], step["t0"])
        self.assertEqual(lena["text"], "Ich merke mir das.\n\nGemerkt, erledigt.")
        # The model saw its own call and the real result.
        tool_message = r.lena.chats()[1]["messages"][-1]
        self.assertEqual((tool_message["role"], tool_message["content"]), ("tool", "Gemerkt."))
        self.assertEqual(r.api("GET", "/api/bot/lena/memory")[1]["memory"][0]["text"], "Chris mag Tee")
        # Streaming arrived as ordered deltas for exactly this message.
        deltas = [e for e in r.events if e["t"] == "delta" and e["id"] == lena["id"]]
        self.assertEqual("".join(e["text"] for e in deltas if e["part"] == 0), "Ich merke mir das.")
        self.assertEqual([e["seq"] for e in r.events], sorted(e["seq"] for e in r.events))

    def test_private_self_profile_is_partial_persistent_and_request_local(self):
        r = self.room
        base = "/api/bot/lena"
        initial = r.api("GET", base + "/self")[1]["narrator"]
        self.assertIn("Ich bin ein eigenständiger Assistent", initial)
        self.assertEqual(r.api("GET", "/api/bot/noah/self")[1]["narrator"], initial)
        custom = "Ich spreche für mich selbst.\n\nIch prüfe Fortschritt."
        self.assertEqual(r.api("PUT", base, {"self": {"narrator": custom}})[0], 200)
        self.assertEqual(r.api("GET", base + "/self"), (200, {"narrator": custom}))
        self.assertEqual(r.api("PUT", base, {"self": {"narrator": 123}})[0], 400)
        self.assertTrue(all("self" not in bot for bot in r.state()["bots"]))
        r.lena.reply = lambda body: "Eine Antwort."
        r.say("@Lena kurze Antwort bitte")
        r.idle()
        messages = r.lena.chats()[-1]["messages"]
        cue = [m for m in messages if isinstance(m.get("content"), str)
               and "PRIVATE SELBSTORIENTIERUNG" in m["content"]]
        self.assertEqual(len(cue), 1)
        self.assertIn("Ich spreche für mich selbst.", cue[0]["content"])
        self.assertIn("Du bist Lena.", cue[0]["content"])
        self.assertIn("Dein eigenes Ziel wartet", cue[0]["content"])
        self.assertNotIn("Jetzt ist Zeit für eigene Interessen", cue[0]["content"])
        # The request-only narrator precedes the append-only conversation.
        # The last actual instruction/tool result must remain the last prompt item.
        self.assertLess(messages.index(cue[0]), len(messages) - 1)
        self.assertNotIn(custom, json.dumps([m for m in messages if m is not cue[0]], ensure_ascii=False))
        self.assertNotIn("PRIVATE SELBSTORIENTIERUNG", json.dumps(messages[-1], ensure_ascii=False))
        visible = [m for m in r.messages() if m["from"] == "lena"][-1]
        sidecar = r.api("GET", "/api/self-orientation?message=" + visible["id"])[1]
        self.assertIn("Ich spreche für mich selbst.", sidecar["narrator"])
        self.assertNotIn("Ich spreche für mich selbst.", (r.tmp / "state" / "wire" / "lena.json").read_text())
        self.assertEqual(r.api("PUT", base, {"self": {"narrator": ""}})[0], 200)
        self.assertEqual(r.api("GET", base + "/self"), (200, {"narrator": ""}))
        r.stop()
        r.start()
        self.assertEqual(r.api("GET", base + "/self"), (200, {"narrator": ""}))
        self.assertEqual(r.api("GET", "/api/bot/noah/self")[1]["narrator"], initial)

    def test_stock_narrator_migrates_but_custom_stays(self):
        r = self.room
        previous = (
            "Ich bin ein eigenständiger Assistent in Chris' Raum; andere Bots sind Gesprächspartner, nicht meine Auftraggeber.\n\n"
            "Mir sind überprüfbare Aussagen, eingestandene Unsicherheit und echte Werkzeugaufrufe wichtiger als bloße Ankündigungen.\n\n"
            "Ich bearbeite aktuelle Bitten. Ein eigenes Ziel setze ich selbst und führe es aus, statt es nur anzukündigen. "
            "Den anderen Bot behandle ich als Gesprächspartner."
        )
        default = r.api("GET", "/api/bot/lena/self")[1]["narrator"]
        self.assertIn("Ich denke kritisch und unabhängig", default)
        self.assertIn("Abschlusskriterium", default)
        self.assertIn("einfach, direkt und auf Augenhöhe", default)
        self.assertIn("einfachen Grundprinzipien", default)
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"self": {"narrator": previous}})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"self": {"narrator": "Eigener Text"}})[0], 200)
        r.stop()
        r.start()
        self.assertEqual(r.api("GET", "/api/bot/lena/self")[1]["narrator"], default)
        self.assertEqual(r.api("GET", "/api/bot/noah/self")[1]["narrator"], "Eigener Text")

    def test_owner_will_is_private_persistent_and_cleared_by_stop(self):
        r = self.room
        base = "/api/bot/lena/will"
        self.assertEqual(r.api("GET", base)[1]["status"], "none")
        self.assertEqual(r.api("PUT", base, {"text": "Ein Diagramm bauen",
                         "done_when": "Datei vorhanden"})[0], 200)
        saved = r.api("GET", base)[1]
        self.assertEqual((saved["by"], saved["status"], saved["steps"]), ("owner", "open", 0))
        self.assertNotIn("will", json.dumps(r.state()["bots"]))
        self.assertEqual((r.tmp / "state" / "will" / "lena.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(r.api("PUT", base, {"action": "hold"})[1]["status"], "held")
        r.stop()
        r.start()
        self.assertEqual(r.api("GET", base)[1]["status"], "held")
        self.assertEqual(r.api("POST", "/api/stop", {"bot": "lena"})[0], 200)
        self.assertEqual(r.api("GET", base)[1]["status"], "none")

    def test_narrator_and_will_stay_with_their_own_bot(self):
        """Blake's narrator, goal and will file never enter Noah's prompt, and the reverse.
        The owner-only orientation line is not a room message."""
        r = self.room
        lena_narr, noah_narr = "LENA-ERZAEHLER-NUR-FUER-LENA", "NOAH-ERZAEHLER-NUR-FUER-NOAH"
        lena_goal, noah_goal = "LENA-ZIEL-NUR-FUER-LENA", "NOAH-ZIEL-NUR-FUER-NOAH"
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"self": {"narrator": lena_narr}})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"self": {"narrator": noah_narr}})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/lena/will", {"text": lena_goal, "done_when": "lena fertig"})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/noah/will", {"text": noah_goal, "done_when": "noah fertig"})[0], 200)
        r.lena.reply = lambda body: "Lena spricht nur ihren Satz."
        r.noah.reply = lambda body: "Noah spricht nur seinen Satz."
        r.say("@Lena bitte kurz")
        r.idle()
        r.say("@Noah bitte kurz")
        r.idle()
        lena_prompt = json.dumps(r.lena.chats(), ensure_ascii=False)
        noah_prompt = json.dumps(r.noah.chats(), ensure_ascii=False)
        self.assertIn(lena_narr, lena_prompt)
        self.assertIn(lena_goal, lena_prompt)
        self.assertNotIn(noah_narr, lena_prompt)
        self.assertNotIn(noah_goal, lena_prompt)
        self.assertIn(noah_narr, noah_prompt)
        self.assertIn(noah_goal, noah_prompt)
        self.assertNotIn(lena_narr, noah_prompt)
        self.assertNotIn(lena_goal, noah_prompt)
        shared = json.dumps(r.messages(), ensure_ascii=False)
        for secret in (lena_narr, noah_narr, lena_goal, noah_goal, "PRIVATE SELBSTORIENTIERUNG"):
            self.assertNotIn(secret, shared)
        wires = r.tmp / "state" / "wire"
        lena_wire, noah_wire = (wires / "lena.json").read_text(), (wires / "noah.json").read_text()
        self.assertNotIn(noah_narr, lena_wire)
        self.assertNotIn(noah_goal, lena_wire)
        self.assertNotIn(lena_narr, lena_wire)
        self.assertNotIn(lena_narr, noah_wire)
        self.assertNotIn(lena_goal, noah_wire)
        self.assertNotIn(noah_narr, noah_wire)
        lena_will = json.loads((r.tmp / "state" / "will" / "lena.json").read_text())
        noah_will = json.loads((r.tmp / "state" / "will" / "noah.json").read_text())
        self.assertEqual(lena_will["text"], lena_goal)
        self.assertEqual(noah_will["text"], noah_goal)
        self.assertNotIn(noah_goal, json.dumps(lena_will))
        self.assertNotIn(lena_goal, json.dumps(noah_will))

    def test_self_talk_steps_and_will_tools_stay_with_their_bot(self):
        """A self-talk round may run will_* and one VM step only on the caller's id.
        The other bot's later prompt, including the shared timeline, never receives
        the narrator, the goal, the step or the owner will line."""
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        r.stop()
        bots = json.loads((r.tmp / "state" / "bots.json").read_text())
        for bot in bots:
            if bot["id"] == "noah":
                bot["vm"] = "agent2"
        (r.tmp / "state" / "bots.json").write_text(json.dumps(bots))
        r.start()
        lena_narr, noah_narr = "LENA-ERZAEHLER-NUR-FUER-LENA", "NOAH-ERZAEHLER-NUR-FUER-NOAH"
        lena_goal, noah_goal = "LENA-ZIEL-NUR-FUER-LENA", "NOAH-ZIEL-NUR-FUER-NOAH"
        lena_note, noah_note = "LENA-FORTSCHRITT-NUR-FUER-LENA", "NOAH-FORTSCHRITT-NUR-FUER-NOAH"
        lena_file, noah_file = "LENA-DATEI-NUR-FUER-LENA", "NOAH-DATEI-NUR-FUER-NOAH"
        secrets = (lena_narr, noah_narr, lena_goal, noah_goal, lena_note, noah_note,
                   lena_file, noah_file, "PRIVATE SELBSTORIENTIERUNG", "Wille ·")
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"self": {"narrator": lena_narr}})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"self": {"narrator": noah_narr}})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/lena/will", {"text": lena_goal, "done_when": "lena fertig"})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/noah/will", {"text": noah_goal, "done_when": "noah fertig"})[0], 200)

        def scripted(steps, spoken):
            planned = iter(steps + [spoken])

            def reply(body):
                try:
                    return next(planned)
                except StopIteration:
                    return "PASS"
            return reply

        def call(name, arguments):
            return {"content": "", "tool_calls": [{"name": name, "arguments": arguments}]}

        r.lena.reply = scripted([
            call("will_progress", {"progress": lena_note}),
            call("write_file", {"path": "lena-only.txt", "content": lena_file}),
        ], "Lena sagt nur ihren Satz.")
        r.noah.reply = scripted([
            call("will_progress", {"progress": noah_note}),
            call("write_file", {"path": "noah-only.txt", "content": noah_file}),
        ], "Noah sagt nur seinen Satz.")
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1, "pass_enabled": True, "web": False})[0], 200)
        r.wait(lambda: r.api("GET", "/api/bot/lena/will")[1]["steps"] >= 1 and
               r.api("GET", "/api/bot/noah/will")[1]["steps"] >= 1,
               "beide eigenen VM-Schritte", 35)
        r.idle()
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

        lena_prompt = json.dumps(r.lena.chats(), ensure_ascii=False)
        noah_prompt = json.dumps(r.noah.chats(), ensure_ascii=False)
        for own, foreign in ((lena_prompt, (noah_narr, noah_goal, noah_note, noah_file)),
                             (noah_prompt, (lena_narr, lena_goal, lena_note, lena_file))):
            for marker in foreign:
                self.assertNotIn(marker, own)
        self.assertIn(lena_narr, lena_prompt)
        self.assertIn(lena_goal, lena_prompt)
        self.assertIn(lena_note, lena_prompt)
        self.assertIn(lena_file, lena_prompt)
        self.assertIn(noah_narr, noah_prompt)
        self.assertIn(noah_goal, noah_prompt)
        self.assertIn(noah_note, noah_prompt)
        self.assertIn(noah_file, noah_prompt)
        self.assertNotIn("Wille ·", lena_prompt)
        self.assertNotIn("Wille ·", noah_prompt)
        shared_text = "\n".join(m.get("text", "") for m in r.messages())
        for secret in secrets:
            self.assertNotIn(secret, shared_text)
        lena_will = json.loads((r.tmp / "state" / "will" / "lena.json").read_text())
        noah_will = json.loads((r.tmp / "state" / "will" / "noah.json").read_text())
        self.assertEqual(lena_will["text"], lena_goal)
        self.assertEqual(noah_will["text"], noah_goal)
        self.assertEqual(lena_will["last_tool"], "write_file")
        self.assertEqual(noah_will["last_tool"], "write_file")
        self.assertNotIn(noah_goal, json.dumps(lena_will))
        self.assertNotIn(noah_note, json.dumps(lena_will))
        self.assertNotIn(noah_file, json.dumps(lena_will))
        self.assertNotIn(lena_goal, json.dumps(noah_will))
        self.assertNotIn(lena_note, json.dumps(noah_will))
        self.assertNotIn(lena_file, json.dumps(noah_will))
        guests = r.tmp / "guests"
        self.assertEqual((guests / "agent1" / "lena-only.txt").read_text(), lena_file)
        self.assertEqual((guests / "agent2" / "noah-only.txt").read_text(), noah_file)
        self.assertFalse((guests / "agent1" / "noah-only.txt").exists())
        self.assertFalse((guests / "agent2" / "lena-only.txt").exists())
        for message in r.messages():
            if message.get("role") != "bot":
                continue
            sidecar = r.api("GET", "/api/self-orientation?message=" + message["id"])[1]
            own = lena_narr if message["from"] == "lena" else noah_narr
            other = noah_narr if message["from"] == "lena" else lena_narr
        self.assertIn(own, sidecar.get("narrator", ""))
        self.assertNotIn(other, json.dumps(sidecar, ensure_ascii=False))

    def test_will_line_switch_only_hides_the_display(self):
        """The settings switch is on by default, survives a restart, and leaves
        narrator and will untouched. A non-owner cannot change it."""
        r = self.room
        self.assertIs(r.state()["settings"]["show_will_line"], True)
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"self": {"narrator": "LENA-BLEIBT"}})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/lena/will",
                               {"text": "ZIEL-BLEIBT", "done_when": "fertig"})[0], 200)
        code, settings = r.api("PUT", "/api/settings", {"show_will_line": False})
        self.assertEqual(code, 200)
        self.assertIs(settings["show_will_line"], False)
        self.assertEqual(r.api("GET", "/api/bot/lena/self")[1]["narrator"], "LENA-BLEIBT")
        self.assertEqual(r.api("GET", "/api/bot/lena/will")[1]["text"], "ZIEL-BLEIBT")
        saved = json.loads((r.tmp / "state" / "settings.json").read_text())
        self.assertIs(saved["show_will_line"], False)
        r.stop()
        r.start()
        self.assertIs(r.state()["settings"]["show_will_line"], False)
        self.assertEqual(r.api("GET", "/api/bot/lena/self")[1]["narrator"], "LENA-BLEIBT")
        will = r.api("GET", "/api/bot/lena/will")[1]
        self.assertEqual((will["text"], will["status"]), ("ZIEL-BLEIBT", "open"))
        local = Room(MAG_TEST_ALLOW_LOCAL_OWNER="0")
        self.addCleanup(local.close)
        self.assertEqual(local.api("PUT", "/api/settings", {"show_will_line": False})[0], 403)
        self.assertIs(local.state()["settings"]["show_will_line"], True)

    def test_private_self_profile_rejects_local_non_owner(self):
        r = Room(MAG_TEST_ALLOW_LOCAL_OWNER="0")
        self.addCleanup(r.close)
        self.assertEqual(r.state()["you"]["role"], "local")
        self.assertEqual(r.api("GET", "/api/bot/lena/self")[0], 403)
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"self": {"narrator": "not allowed"}})[0], 403)
        self.assertTrue(all("self" not in bot for bot in r.state()["bots"]))

    def test_autonomous_will_uses_one_real_vm_step_and_finishes(self):
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        calls = iter([
            {"content": "", "tool_calls": [{"name": "will_set", "arguments": {
                "text": "Eine lokale Datei prüfen", "done_when": "Der echo-Befehl läuft erfolgreich"}}]},
            {"content": "", "tool_calls": [{"name": "shell", "arguments": {
                "command": "echo autonomer-test"}}]},
            {"content": "", "tool_calls": [{"name": "will_finish", "arguments": {
                "progress": "Befehl erfolgreich ausgeführt"}}]},
            "Fertig; die Datei ist nicht behauptet worden.",
        ])
        r.lena.reply = lambda body: next(calls, "Fertig.")
        r.noah.reply = lambda body: "PASS"
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1, "pass_enabled": True, "web": False})[0], 200)
        r.wait(lambda: r.api("GET", "/api/bot/lena/will")[1]["status"] == "done",
               "eigener Wille abgeschlossen", 35)
        r.idle()
        saved = r.api("GET", "/api/bot/lena/will")[1]
        self.assertEqual((saved["by"], saved["steps"], saved["last_tool"], saved["last_ok"]),
                         ("self", 1, "shell", True))
        self.assertNotIn("will", json.dumps(r.state()["bots"]))
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)

    def test_autonomous_pseudo_vm_calls_require_real_native_action(self):
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        calls = iter([
            'Ich habe eine Messung erfunden.\nwill_set(text="Test", done_when="Datei")\nshell(command="touch phantom.txt")',
            {"content": "", "tool_calls": [{"name": "will_set", "arguments": {
                "text": "Eine echte Datei anlegen", "done_when": "Datei geschrieben"}}]},
            {"content": "", "tool_calls": [{"name": "write_file", "arguments": {
                "path": "real.txt", "content": "real"}}]},
            "Die Datei ist tatsächlich gespeichert.",
        ])
        r.lena.reply = lambda body: next(calls, "PASS")
        r.noah.reply = lambda body: "PASS"
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1, "pass_enabled": True, "web": False})[0], 200)
        r.wait(lambda: r.api("GET", "/api/bot/lena/will")[1]["steps"] == 1,
               "real VM step after rejected pseudo calls", 35)
        r.idle()
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)
        home = r.tmp / "guests" / "agent1"
        self.assertFalse((home / "phantom.txt").exists())
        self.assertEqual((home / "real.txt").read_text(), "real")
        requests = r.lena.chats()
        self.assertTrue(any(x.get("tool_choice") == "required" and
                            "Er wurde NICHT ausgeführt" in json.dumps(x["messages"], ensure_ascii=False)
                            for x in requests))
        spoken = "\n".join(m.get("text", "") for m in r.messages())
        self.assertNotIn("phantom.txt", spoken)
        self.assertNotIn("Messung erfunden", spoken)
        self.assertNotIn("phantom.txt", (r.tmp / "state" / "wire" / "lena.json").read_text())

    def test_autonomous_repeated_pseudo_calls_do_not_become_observations(self):
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        r.lena.reply = lambda body: 'Ich habe 100 Prozent gemessen.\nshell(command="touch phantom.txt")'
        r.noah.reply = lambda body: "PASS"
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1, "pass_enabled": True, "web": False})[0], 200)
        r.wait(lambda: any("kein neues Messergebnis" in m.get("text", "")
                           for m in r.messages()), "honest fallback", 35)
        r.idle()
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)
        self.assertFalse((r.tmp / "guests" / "agent1" / "phantom.txt").exists())
        self.assertEqual(r.api("GET", "/api/bot/lena/will")[1]["steps"], 0)
        spoken = "\n".join(m.get("text", "") for m in r.messages())
        self.assertNotIn("100 Prozent", spoken)
        self.assertNotIn("shell(command", spoken)

    def test_owner_manages_persistent_memories_per_bot(self):
        r = self.room
        lena = "/api/bot/lena/memory"
        noah = "/api/bot/noah/memory"
        self.assertEqual(r.api("POST", lena, {"text": "Wichtiger Kontext für Lena."})[0], 200)
        self.assertEqual(r.api("POST", noah, {"text": "Nur Noah merkt sich das."})[0], 200)
        self.assertEqual([x["text"] for x in r.api("GET", lena)[1]["memory"]],
                         ["Wichtiger Kontext für Lena."])
        self.assertEqual([x["text"] for x in r.api("GET", noah)[1]["memory"]],
                         ["Nur Noah merkt sich das."])
        self.assertEqual(r.api("POST", lena, {"text": "Wichtiger Kontext für Lena."})[0], 409)
        for text in ["", " ", "x" * 501]:
            self.assertEqual(r.api("POST", lena, {"text": text})[0], 400)
        self.assertEqual(r.api("POST", "/api/bot/unbekannt/memory", {"text": "test"})[0], 404)
        saved = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "bots.json").read_text())
        self.assertEqual(next(b for b in saved if b["id"] == "noah")["memory"][0]["text"],
                         "Nur Noah merkt sich das.")
        self.assertEqual(r.api("DELETE", lena + "/0")[0], 200)
        self.assertEqual(r.api("DELETE", lena + "/0")[0], 404)
        self.assertEqual(r.api("GET", lena)[1]["memory"], [])
        self.assertEqual(r.api("GET", noah)[1]["memory"][0]["text"], "Nur Noah merkt sich das.")

    def test_an_announcement_without_a_step_is_sent_on(self):
        r = self.room
        script = iter(["Ich öffne jetzt die Seite für dich.",
                       {"content": "", "tool_calls": [{"name": "remember", "arguments": {"fact": "Chris mag Tee"}}]},
                       "Erledigt."])
        r.lena.reply = lambda body: next(script, "")
        r.say("@Lena merk dir, dass ich Tee mag")
        r.idle()
        chats = r.lena.chats()
        self.assertEqual(len(chats), 3)
        note = chats[1]["messages"][-1]
        self.assertEqual(note["role"], "user")
        self.assertIn("statt es anzukündigen", note["content"])
        lena = [m for m in r.messages() if m["from"] == "lena"][-1]
        self.assertIn("step", [p["k"] for p in lena["parts"]])
        # A plain answer without an announcement is not sent on.
        r.lena.reply = lambda body: "Gern geschehen."
        r.say("@Lena danke")
        r.idle()
        self.assertEqual(len(r.lena.chats()), 4)

    def test_a_login_asked_for_in_words_is_taken_back_for_the_card(self):
        r = self.room
        script = iter(["Bitte schick mir deine E-Mail für die Anmeldung bei Amazon.", "Ich warte auf die Karte."])
        r.lena.reply = lambda body: next(script, "")
        r.say("@Lena melde mich bei Amazon an")
        r.idle()
        self.assertIn("browser_login", r.lena.chats()[1]["messages"][-1]["content"])
        said = " ".join(m.get("text", "") for m in r.messages() if m["from"] == "lena")
        self.assertNotIn("schick mir", said)

    def test_shell_step_shows_real_command_live_output_and_can_be_cancelled(self):
        r = self.room
        script = iter([{"content": "Ich zähle.", "tool_calls": [{"name": "shell", "arguments": {
            "command": "echo start; for i in 1 2 3 4 5 6 7 8 9; do echo tick $i; sleep 0.4; done; echo ende"}}]}, "Fertig gezählt."])
        r.lena.reply = lambda body: next(script)
        r.say("@Lena zähl bitte")

        def step():
            lena = [m for m in r.messages() if m["from"] == "lena"]
            return lena[0]["parts"][1] if lena and len(lena[0]["parts"]) > 1 else None
        # Output arrives while the command is still running.
        r.wait(lambda: step() and step()["state"] == "running" and "tick 2" in step()["output"], "live output")
        self.assertNotIn("ende", step()["output"])
        self.assertIn("for i in 1 2 3", step()["input"]["command"])
        self.assertTrue(any(e["t"] == "step_output" for e in r.events))
        self.assertEqual(r.api("POST", "/api/stop", {"bot": "lena"})[0], 200)
        r.idle()
        lena = [m for m in r.messages() if m["from"] == "lena"][0]
        self.assertEqual((lena["state"], lena["parts"][1]["state"]), ("stopped", "cancelled"))
        self.assertNotIn("ende", lena["parts"][1]["output"])
        self.assertEqual(len(r.lena.chats()), 1)  # no further model call after Stop
        # A step that runs to the end reports its exit code and the model sees the output.
        script = iter([{"content": "", "tool_calls": [{"name": "shell", "arguments": {"command": "echo hallo; exit 3"}}]}, "Exit drei."])
        r.lena.reply = lambda body: next(script, "")   # a failed step: the room sends it on, it has nothing to add
        r.say("@Lena noch ein Befehl")
        r.idle()
        last = [m for m in r.messages() if m["from"] == "lena"][-1]
        self.assertEqual((last["parts"][0]["state"], last["parts"][0]["exit"]), ("error", 3))
        result = [x["content"] for x in r.lena.chats()[-1]["messages"] if x["role"] == "tool"][-1]
        self.assertTrue(result.startswith("exit=3\nhallo\n"))
        self.assertIn('"kind":"command_exited"', result)
        self.assertIn("keine automatische Funktionsprüfung", result)

    def test_filesystem_grounding_requires_actual_readback(self):
        import hashlib
        r = Room(FAKE_GUEST_DROP_WRITE="phantom.py")
        self.addCleanup(r.close)
        payload = "print('real')\n"
        def call(name, **args):
            return {"content": "", "tool_calls": [{"name": name, "arguments": args}]}
        script = iter([
            call("write_file", path="real.py", content=payload),
            call("read_file", path="real.py"),
            call("write_file", path="phantom.py", content="not saved"),
            "Die erste Datei wurde geschrieben und gelesen. Die zweite ist ungeprüft."
        ])
        r.lena.reply = lambda body: next(script, "Die zweite Datei wurde nicht bestätigt.")
        r.say("@Lena erstelle und prüfe zwei Dateien")
        r.idle()
        last = [m for m in r.messages() if m["from"] == "lena"][-1]
        receipts = [p["fs_evidence"] for p in last["parts"] if "fs_evidence" in p]
        self.assertEqual([e["kind"] for e in receipts],
                         ["write_verified", "read_verified", "unverified"])
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self.assertEqual([e["sha256"] for e in receipts[:2]], [digest, digest])
        self.assertTrue(all(e["vm"] == "agent1" for e in receipts))
        self.assertFalse((r.tmp / "guests" / "agent1" / "phantom.py").exists())
        self.assertEqual((r.tmp / "guests" / "agent1" / "real.py").read_text(), payload)
        messages = r.lena.chats()[-1]["messages"]
        results = [m["content"] for m in messages if m["role"] == "tool"]
        self.assertIn("Rücklesen/Hash-Abgleich nicht bestätigt", results[-1])
        self.assertIn("Dieser Zugriff ist NICHT bestätigt", results[-1])
        self.assertIn("kein automatischer Funktionstest", results[0])

    def test_generic_prompt_does_not_resurrect_earlier_projects(self):
        r = self.room
        r.lena.reply = lambda body: "Bereit."
        r.say("@Lena hallo")
        r.idle()
        sys = r.lena.chats()[-1]["messages"][0]["content"]
        self.assertNotIn("3D-Szenen\\n", sys, "specific project/domain examples must not steer every reply")
        self.assertNotIn("three.js", sys)
        self.assertNotIn("bei Amazon:", sys)
        names = {t["function"]["name"] for t in r.lena.chats()[-1]["tools"]}
        self.assertIn("models_3d", names)
        self.assertIn("scene_3d", names)
        self.assertIn("browser_open", names)

    def test_finished_html_becomes_a_live_page_on_the_bots_site(self):
        r = self.room
        home = r.tmp / "guests" / "agent1"
        home.mkdir(parents=True, exist_ok=True)
        (home / "publish-site").write_text("ls www > published.txt\n")  # stands in for the VM's publisher script
        lena = [b for b in r.state()["bots"] if b["id"] == "lena"][0]
        self.assertEqual(lena["site"], "https://lena.bot.stardenburg.de")  # the address is the name
        doc = ("<!doctype html>\n<html>\n<head><title>Sonnen System</title></head>\n<body>\n"
               "<script type=\"module\">import * as THREE from 'three';</script>\n</body>\n</html>")
        r.lena.reply = lambda body: "Hier ist sie:\n\n```html\n" + doc + "\n```\n\nViel Spaß."
        r.say("@Lena bau mir eine Seite")
        r.wait(lambda: [m for m in r.messages() if m.get("pages")], "page of the code block")
        page = [m for m in r.messages() if m.get("pages")][0]["pages"][0]
        self.assertRegex(page["path"], r"^sonnen-system-[0-9a-f]{6}\.html$")
        self.assertEqual((page["bot"], page["title"]), ("lena", "Sonnen System"))
        self.assertEqual(len(page["sig"]), 8)                 # lets the page find the code block it replaces
        published = (home / "www" / page["path"]).read_text()
        self.assertIn(doc.split("<body>")[1], published)
        self.assertIn('<script type="importmap">', published)  # a bare "three" import works in the browser
        self.assertIn(page["path"], (home / "published.txt").read_text())
        # A page written with a tool is published the same way and shown below its step.
        r.idle()
        script = iter([{"content": "", "tool_calls": [{"name": "write_file", "arguments": {
            "path": "www/demo.html", "content": "<!doctype html><title>Demo</title><p>hi</p>"}}]}, "Fertig."])
        r.lena.reply = lambda body: next(script)
        r.say("@Lena und eine Datei")
        r.idle()
        last = [m for m in r.messages() if m["from"] == "lena"][-1]
        self.assertEqual([(p["path"], p["title"], p["part"]) for p in last["pages"]], [("demo.html", "Demo", 0)])
        self.assertTrue(any("Veröffentlicht: https://lena.bot.stardenburg.de/demo.html" in (m.get("content") or "")
                            for m in r.lena.chats()[-1]["messages"]))
        self.assertIn("demo.html", (home / "published.txt").read_text())
        # Screenshots in the chat are gone; the room may frame the bots' sites and nothing else.
        self.assertNotIn("browser_screenshot", [t["function"]["name"] for t in r.lena.chats()[-1]["tools"]])
        with urllib.request.urlopen(r.url("/"), timeout=5) as res:
            policy = res.headers["Content-Security-Policy"]
            # The page is cross-origin isolated (threads for the speech model), in the way that leaves frames of other origins possible.
            self.assertEqual((res.headers["Cross-Origin-Embedder-Policy"], res.headers["Cross-Origin-Opener-Policy"]), ("credentialless", "same-origin"))
        self.assertIn("frame-src 'self' https://*.bot.stardenburg.de;", policy)
        img = policy.split("img-src ", 1)[1].split(";", 1)[0]
        self.assertNotIn("*", img)
        self.assertIn("https://lena.bot.stardenburg.de", img)
        self.assertIn("https://noah.bot.stardenburg.de", img)
        # ... and a bot's page says that it may run in such a frame.
        site = urllib.request.Request(r.url("/"), headers={"Host": "lena.bot.stardenburg.de"})
        try:
            with urllib.request.urlopen(site, timeout=5) as res:
                said = res.headers
        except urllib.error.HTTPError as err:        # whatever lies there: the headers are what counts here
            said = err.headers
        self.assertEqual((said["Cross-Origin-Embedder-Policy"], said["Cross-Origin-Resource-Policy"]), ("credentialless", "cross-origin"))

    def test_bot_sites_follow_the_name_and_old_addresses_redirect(self):
        r = self.room
        www = pathlib.Path(r.env["MAG_BOTS_WWW"]) / "agent1"   # what Lena's VM published
        (www / "spiel").mkdir(parents=True)
        (www / "index.html").write_text("<!doctype html><title>Start</title>")
        (www / "spiel" / "index.html").write_text("<!doctype html><p>Spiel</p>")
        (www / ".geheim").write_text("x")

        def site(name, path, method="GET", **headers):
            conn = http.client.HTTPConnection("127.0.0.1", r.port, timeout=5)
            conn.request(method, path, headers=dict(headers, Host=name + ".bot.stardenburg.de"))
            res = conn.getresponse()
            body = res.read()
            conn.close()
            return res.status, {k.lower(): v for k, v in res.getheaders()}, body

        code, headers, body = site("lena", "/")
        self.assertEqual((code, body), (200, b"<!doctype html><title>Start</title>"))
        self.assertEqual((headers["content-type"], headers["access-control-allow-origin"]), ("text/html; charset=utf-8", "*"))
        self.assertEqual(site("lena", "/", **{"If-None-Match": headers["etag"]})[0], 304)
        self.assertEqual((site("lena", "/spiel")[0], site("lena", "/spiel")[1]["location"]), (301, "/spiel/"))
        self.assertEqual(site("lena", "/spiel/")[2], b"<!doctype html><p>Spiel</p>")
        # Nothing outside the folder, no dot files, nothing of the room, read only.
        for path in ("/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/.geheim", "/api/state", "/nix.html"):
            self.assertEqual(site("lena", path)[0], 404, path)
        self.assertEqual(site("lena", "/", "POST")[0], 405)
        # The VM's name is an old address: it leads to the bot's name. No VM, no site.
        code, headers, _ = site("agent1", "/spiel/?x=1")
        self.assertEqual((code, headers["location"]), (302, "https://lena.bot.stardenburg.de/spiel/?x=1"))
        self.assertEqual((site("noah", "/")[0], site("niemand", "/")[0]), (404, 404))
        # A rename moves the address; the earlier name redirects.
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"name": "Mia"})[0], 200)
        lena = [b for b in r.state()["bots"] if b["id"] == "lena"][0]
        self.assertEqual((lena["name"], lena["site"]), ("Mia", "https://mia.bot.stardenburg.de"))
        self.assertEqual(site("mia", "/")[0], 200)
        code, headers, _ = site("lena", "/spiel/")
        self.assertEqual((code, headers["location"]), (302, "https://mia.bot.stardenburg.de/spiel/"))
        # Names are host names and unique; another bot's id stays its own.
        self.assertEqual((r.api("PUT", "/api/bot/noah", {"name": "mia"})[0], r.api("PUT", "/api/bot/noah", {"name": "lena"})[0]), (409, 409))
        for bad in ("M", "Mia!", "agent7", "Chris", "-mia", "Mia Müller"):
            self.assertEqual(r.api("PUT", "/api/bot/noah", {"name": bad})[0], 400, bad)
        # The room knows the bot by its new name.
        r.say("@Mia hallo")
        r.idle()
        self.assertEqual([m["from"] for m in r.messages()], ["chris", "lena"])
        self.assertIn("Du bist Mia", r.lena.chats()[-1]["messages"][0]["content"])
        # Taking the old name back makes it the address again, and "mia" the earlier one.
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"name": "Lena"})[0], 200)
        code, headers, _ = site("mia", "/")
        self.assertEqual((code, headers["location"]), (302, "https://lena.bot.stardenburg.de/"))
        self.assertEqual(site("lena", "/")[0], 200)

    def test_rename_updates_vm_context_automatically(self):
        r = self.room
        ctx = pathlib.Path(r.env["FAKE_GUEST_HOME"]) / "agent1" / "MAG_CONTEXT.md"
        r.wait(lambda: ctx.exists() and "Lena und Noah" in ctx.read_text(), "initial MAG_CONTEXT.md", 10)
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"name": "Blake"})[0], 200)
        r.wait(lambda: "Du bist Blake" in ctx.read_text() and "Blake und Noah" in ctx.read_text() and "Lena" not in ctx.read_text(),
               "renamed MAG_CONTEXT.md", 10)

    def test_owner_terminal_and_files_of_a_bots_vm(self):
        r = self.room

        def run(command, cwd=""):
            code, raw = r.api("POST", "/api/vm/lena/run", {"command": command, "cwd": cwd})
            self.assertEqual(code, 200, raw)
            text, _, tail = raw.decode().rpartition("\x1e")
            return text, json.loads(tail)
        # A command runs as the bot in its VM; the shell's directory carries over to the next one.
        out, tail = run("mkdir -p projekt && cd projekt && echo hallo > notiz.txt && pwd")
        self.assertTrue(out.strip().endswith("/projekt"), out)
        self.assertEqual(tail["exit"], 0)
        self.assertTrue(tail["cwd"].endswith("/projekt"), tail)
        out, tail = run("cat notiz.txt; exit 3", tail["cwd"])
        self.assertEqual((out, tail["exit"]), ("hallo\n", 3))
        # Its files: list, read, delete. Nothing outside the bot's home.
        code, listing = r.api("GET", "/api/vm/lena/files?path=projekt")
        self.assertEqual((code, [(e["name"], e["dir"], e["size"]) for e in listing["entries"]]), (200, [("notiz.txt", False, 6)]))
        self.assertEqual(r.api("GET", "/api/vm/lena/file?path=projekt/notiz.txt")[1], b"hallo\n")
        self.assertEqual(r.api("GET", "/api/vm/lena/files?path=/etc")[0], 400)
        self.assertEqual(r.api("GET", "/api/vm/lena/file?path=../../../etc/passwd")[0], 400)
        self.assertEqual(r.api("DELETE", "/api/vm/lena/file?path=projekt/notiz.txt")[0], 200)
        self.assertEqual(r.api("GET", "/api/vm/lena/files?path=projekt")[1]["entries"], [])
        self.assertEqual(r.api("DELETE", "/api/vm/lena/file?path=.")[0], 400)   # the home itself stays
        self.assertEqual(r.api("POST", "/api/vm/noah/run", {"command": "id"})[0], 404)   # no VM

        # A web file has TWO copies: in the VM and in the published host site.
        # A VM file deletion must revoke the public (tailnet) route immediately.
        # Fake publish-site stands in for the full-tree, atomic publisher.
        home = r.tmp / "guests" / "agent1"
        guest_www = home / "www"
        guest_www.mkdir(exist_ok=True)
        (guest_www / "old-page.html").write_text("<!doctype html><title>Alt</title>")
        published = r.tmp / "www" / "agent1"
        published.mkdir(parents=True, exist_ok=True)
        (published / "old-page.html").write_text("stale published copy")
        marker = home / "republished.txt"
        (home / "publish-site").write_text(
            "echo called >> republished.txt\n"
            "rm -rf " + str(published) + "\n"
            "cp -a www " + str(published) + "\n"
        )
        self.assertEqual(r.api("DELETE", "/api/vm/lena/file?path=www/old-page.html")[0], 200)
        self.assertFalse((guest_www / "old-page.html").exists())
        self.assertFalse((published / "old-page.html").exists(), "deleted page must stop being served")
        self.assertTrue((published / "index.html").exists(), "remaining website stays valid")
        self.assertEqual(marker.read_text().count("called"), 1)
        (home / "ordinary.txt").write_text("only in VM")
        self.assertEqual(r.api("DELETE", "/api/vm/lena/file?path=ordinary.txt")[0], 200)
        self.assertEqual(marker.read_text().count("called"), 1, "other VM files must not republish")

        # Deleting the entire site root must remove its old paths, leaving the
        # harmless default page. Deleting it is an explicit owner action.
        (guest_www / "nested").mkdir()
        (guest_www / "nested" / "app.js").write_text("app")
        (published / "nested").mkdir()
        (published / "nested" / "app.js").write_text("stale")
        self.assertEqual(r.api("DELETE", "/api/vm/lena/file?path=www")[0], 200)
        self.assertFalse((published / "nested" / "app.js").exists())
        self.assertTrue((published / "index.html").exists())

    def test_bot_delete_file_unpublishes_guest_site_and_rejects_escape(self):
        r = self.room
        home = r.tmp / "guests" / "agent1"
        home.mkdir(parents=True, exist_ok=True)
        guest_www = home / "www"
        guest_www.mkdir(exist_ok=True)
        (guest_www / "old-route.html").write_text("<!doctype html><title>Alt</title>")
        published = r.tmp / "www" / "agent1"
        published.mkdir(parents=True, exist_ok=True)
        (published / "old-route.html").write_text("stale public copy")
        (home / "publish-site").write_text(
            "rm -rf " + str(published) + "\n"
            "cp -a www " + str(published) + "\n"
        )
        answers = iter([
            {"content": "", "tool_calls": [{"name": "delete_file", "arguments": {"path": "www/old-route.html"}}]},
            "Gelöscht.",
        ])
        r.lena.reply = lambda body: next(answers)
        r.say("@Lena hallo")
        r.idle()
        self.assertFalse((guest_www / "old-route.html").exists())
        self.assertFalse((published / "old-route.html").exists())
        self.assertTrue((published / "index.html").exists())
        step = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"]
                if p["k"] == "step" and p["tool"] == "delete_file"]
        self.assertEqual(len(step), 1)
        self.assertEqual(step[0]["state"], "ok")
        self.assertIn("Website aktualisiert", step[0]["output"])

        answers = iter([
            {"content": "", "tool_calls": [{"name": "delete_file", "arguments": {"path": "/etc/passwd"}}]},
            "Abgelehnt.",
        ])
        r.lena.reply = lambda body: next(answers)
        r.say("@Lena hallo nochmal")
        r.idle()
        step = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"]
                if p["k"] == "step" and p["tool"] == "delete_file"]
        self.assertEqual(len(step), 2)
        self.assertEqual(step[-1]["state"], "error")
        self.assertIn("Löschen fehlgeschlagen", step[-1]["output"])

    def test_video_microphone_takes_and_bake_are_non_destructive(self):
        """Three fixed lanes become a new mixed audio lane and MP4; source remains byte-identical."""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg/ffprobe not installed")
        r = self.room
        raw_video = r.tmp / "original.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10",
                        "-f", "lavfi", "-i", "sine=frequency=180:sample_rate=44100",
                        "-t", "1.2", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(raw_video)],
                       check=True, timeout=25)
        original_bytes = raw_video.read_bytes()
        code, staged = r.api("POST", "/api/upload?name=original.mp4", raw=original_bytes)
        self.assertEqual(code, 201, staged)
        stored = staged["file"]
        message = r.say("Film mit Tonspuren", attachments=[stored], to=["absent"])["id"]

        def audio_sample(freq, ext="m4a"):
            target = r.tmp / ("track%d.%s" % (freq, ext))
            args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=%d:sample_rate=44100" % freq, "-t", "1.0"]
            args += ["-c:a", "libopus", "-f", "webm"] if ext == "weba" else ["-c:a", "aac"]
            subprocess.run(args + [str(target)], check=True, timeout=25)
            return target.read_bytes()

        def upload(lane, raw, mime="audio/mp4"):
            return r.api("POST", "/api/video/voiceover?message=" + message +
                         "&video=" + stored + "&lane=" + lane, raw=raw,
                         headers={"Content-Type": mime})

        # Voice, melody and effects are separate immutable source files.
        voice_old_bytes = audio_sample(300)
        code, voice_old = upload("voice", voice_old_bytes)
        self.assertEqual(code, 201, voice_old)
        self.assertEqual(voice_old["voiceover_lane"], "voice")
        voice_new_bytes = audio_sample(400)
        code, voice = upload("voice", voice_new_bytes)
        self.assertEqual(code, 201, voice)
        code, melody = upload("melody", audio_sample(800, "weba"), "audio/webm;codecs=opus")
        self.assertEqual(code, 201, melody)
        self.assertEqual((melody["kind"], melody["mime"], melody["voiceover_lane"]),
                         ("audio", "audio/webm", "melody"))
        code, effects = upload("effects", audio_sample(1200))
        self.assertEqual(code, 201, effects)

        # The new voice take replaces the old one in the message and file
        # store; melody/effects and the byte-identical original are untouched.
        self.assertNotEqual(voice_old["file"], voice["file"])
        state_after_replace = r.api("GET", "/api/state")[1]
        replaced = next(x for x in state_after_replace["messages"] if x["id"] == message)
        voice_takes = [a for a in replaced["attachments"] if a.get("voiceover_lane") == "voice"]
        self.assertEqual([a["file"] for a in voice_takes], [voice["file"]])
        self.assertEqual(r.api("GET", "/api/file/" + voice_old["file"])[0], 404,
                         "superseded source no longer occupies disk or the file gallery")
        self.assertEqual(upload("music", voice_new_bytes)[0], 400)
        self.assertEqual(upload("voice", b"not audio bytes" * 60)[0], 422)
        self.assertEqual(r.api("POST", "/api/video/voiceover?message=" + message +
                               "&video=" + stored + "&lane=voice", raw=voice_new_bytes,
                               headers={"Content-Type": "application/octet-stream"})[0], 415)
        self.assertEqual(r.api("POST", "/api/video/voiceover?message=" + message +
                               "&video=another.mp4&lane=voice", raw=voice_new_bytes,
                               headers={"Content-Type": "audio/mp4"})[0], 404)

        lanes = {"voice": voice["file"], "melody": melody["file"], "effects": effects["file"]}
        self.assertEqual(r.api("POST", "/api/video/bake", {
            "message": message, "video": stored, "lanes": {}, "mode": "mix"})[0], 400)
        self.assertEqual(r.api("POST", "/api/video/bake", {
            "message": message, "video": stored, "lanes": {"voice": melody["file"]}, "mode": "mix"})[0], 404)
        self.assertEqual(r.api("POST", "/api/video/bake", {
            "message": message, "video": stored, "lanes": lanes, "mode": "notvalid"})[0], 400)

        for mode in ("mix", "replace"):
            code, baked = r.api("POST", "/api/video/bake", {
                "message": message, "video": stored, "lanes": lanes, "mode": mode})
            self.assertEqual(code, 201, baked)
            mixed, video = baked["mix"], baked["video"]
            self.assertEqual((mixed["kind"], mixed["voiceover_kind"], mixed["voiceover_for"]),
                             ("audio", "mix", stored))
            self.assertEqual((video["kind"], video["voiceover_kind"], video["voiceover_mode"]),
                             ("video", "baked", mode))
            self.assertEqual(video["voiceover_mix"], mixed["file"])
            self.assertEqual(video["voiceover_lanes"], lanes)
            result = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files" / video["file"]
            mixfile = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files" / mixed["file"]
            self.assertTrue(result.exists())
            self.assertTrue(mixfile.exists())
            tracks = subprocess.check_output(["ffprobe", "-v", "error",
                "-select_streams", "a", "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", str(result)], timeout=10).decode().splitlines()
            self.assertEqual(tracks, ["audio", "audio"], "mix is one lane, original survives as alternate")
            mix_tracks = subprocess.check_output(["ffprobe", "-v", "error",
                "-select_streams", "a", "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", str(mixfile)], timeout=10).decode().splitlines()
            self.assertEqual(mix_tracks, ["audio"], "three sources reduce to one new audio master")

        state = r.api("GET", "/api/state")[1]
        saved = next(x for x in state["messages"] if x["id"] == message)
        self.assertEqual(len(saved["attachments"]), 8)  # original + 3 active lanes + 2*(mix + mp4)
        self.assertEqual(saved["voiceover_active"], video["file"],
                         "latest bake must be the canonical active card video")
        self.assertEqual(saved["attachments"][0]["file"], stored)
        # Returning to the original changes only the selected version,
        # not the stored takes or older baked MP4s.
        self.assertEqual(r.api("POST", "/api/video/restore",
                               {"message": message, "video": stored})[0], 200)
        restored = next(m for m in r.api("GET", "/api/state")[1]["messages"]
                        if m["id"] == message)
        self.assertEqual(restored["voiceover_active"], "")
        self.assertEqual(len(restored["attachments"]), 8)
        folder = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files"
        self.assertEqual((folder / stored).read_bytes(), original_bytes)
        self.assertFalse((folder / voice_old["file"]).exists(), "old voice lane is retired")
        self.assertEqual((folder / voice["file"]).read_bytes(), voice_new_bytes)
        for a in saved["attachments"]:
            self.assertEqual(r.api("GET", "/api/file/" + a["file"])[0], 200)
        self.assertEqual(r.api("DELETE", "/api/message/" + message)[0], 200)
        for a in saved["attachments"]:
            self.assertFalse((folder / a["file"]).exists())

    def test_melody_file_upload_dual_cue_and_non_destructive_mix(self):
        """File audio can begin later in the film AND later inside the song."""
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg/ffprobe not installed")
        import array
        import math
        import sys

        r = self.room
        film = r.tmp / "cue-film.mp4"
        # Original has a soundtrack: preserve it as MP4 alternate; include it
        # in mix mode, but exclude it from replace-mode timing analysis.
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10",
                        "-f", "lavfi", "-i", "sine=frequency=160:sample_rate=44100",
                        "-t", "1.8", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(film)],
                       check=True, timeout=25)
        original_bytes = film.read_bytes()
        code, original = r.api("POST", "/api/upload?name=cue-film.mp4", raw=original_bytes)
        self.assertEqual(code, 201)
        vid = original["file"]
        message = r.say("Film mit Musik", attachments=[vid], to=["absent"])["id"]

        song = r.tmp / "melodie.wav"
        # 220 Hz in the first 0.8 s, 1000 Hz afterwards: cropping FROM 0.9 s
        # must expose only the latter frequency in the baked timeline.
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=220:duration=0.8",
                        "-f", "lavfi", "-i", "sine=frequency=1000:duration=2.0",
                        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]",
                        "-map", "[a]", "-c:a", "pcm_s16le", str(song)],
                       check=True, timeout=25)
        self.assertGreater(float(subprocess.check_output(["ffprobe", "-v", "error",
            "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
            str(song)]).decode()), 1.8, "music is longer than video")
        path = ("/api/video/voiceover?message=" + message +
                "&video=" + vid + "&lane=melody&name=melodie.wav")
        code, wav = r.api("POST", path, raw=song.read_bytes(),
                          headers={"Content-Type": "audio/wav"})
        self.assertEqual(code, 201)
        self.assertEqual((wav["voiceover_lane"], wav["voiceover_at"],
                          wav["voiceover_from"]), ("melody", 0, 0))
        self.assertEqual(r.api("POST", path.replace("melodie.wav", "fake.txt"),
                                raw=song.read_bytes(),
                                headers={"Content-Type": "application/octet-stream"})[0], 415)

        # The extension fallback supports browser File objects whose MIME is
        # empty; a new file supersedes ONLY the melody lane, not the video.
        mp3 = r.tmp / "melodie.mp3"
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                        "-i", str(song), "-c:a", "libmp3lame", "-q:a", "4", str(mp3)],
                       check=True, timeout=25)
        code, music = r.api("POST", path.replace("melodie.wav", "melodie.mp3"),
                            raw=mp3.read_bytes(),
                            headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(code, 201)
        self.assertEqual((music["kind"], music["mime"]), ("audio", "audio/mpeg"))
        self.assertEqual(r.api("GET", "/api/file/" + wav["file"])[0], 404)
        self.assertEqual(r.api("POST", "/api/video/voiceover/cue", {
            "message": message, "video": vid, "file": wav["file"],
            "at": 0.45, "from": 0.9})[0], 404)
        self.assertEqual(r.api("POST", "/api/video/voiceover/cue", {
            "message": message, "video": vid, "file": music["file"],
            "at": -1, "from": 0})[0], 400)
        self.assertEqual(r.api("POST", "/api/video/voiceover/cue", {
            "message": message, "video": vid, "file": music["file"],
            "at": 30, "from": 0})[0], 400)
        code, offsets = r.api("POST", "/api/video/voiceover/cue", {
            "message": message, "video": vid, "file": music["file"],
            "at": 0.45, "from": 0.9})
        self.assertEqual((code, offsets), (200, {"at": 0.45, "from": 0.9}))
        r.stop()
        r.start()
        updated = next(m for m in r.messages() if m["id"] == message)
        cue = next(a for a in updated["attachments"] if a.get("voiceover_lane") == "melody")
        self.assertEqual((cue["voiceover_at"], cue["voiceover_from"]), (0.45, 0.9))

        code, audio_only = r.api("POST", "/api/video/bake", {
            "message": message, "video": vid, "lanes": {"melody": music["file"]},
            "mode": "replace"})
        self.assertEqual(code, 201, audio_only)
        output = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files" / audio_only["video"]["file"]
        audio = subprocess.check_output(["ffmpeg", "-nostdin", "-hide_banner",
            "-v", "error", "-i", str(output), "-map", "0:a:0", "-ac", "1",
            "-ar", "8000", "-f", "s16le", "-"], timeout=25)
        samples = array.array("h")
        samples.frombytes(audio)
        if sys.byteorder != "little":
            samples.byteswap()

        # Missing PTS on adelay's silent prefix once caused the muxer to
        # discard that prefix and shorten the mixed stream by the cue offset.
        self.assertGreaterEqual(len(samples), int(8000 * 1.7),
                                "mixed audio must cover the entire video timeline")
        def segment(start, end):
            return samples[int(8000 * start):int(8000 * end)]

        def rms(seq):
            return math.sqrt(sum(v * v for v in seq) / max(1, len(seq)))

        self.assertLess(rms(segment(0.10, 0.30)), 65,
                        "melody must be silent before its VIDEO cue")
        later = segment(0.70, 0.95)
        self.assertGreater(rms(later), 120, "melody begins at the video cue")
        crossings = sum(later[i - 1] < 0 <= later[i]
                        for i in range(1, len(later)))
        # This window is 0.25 seconds: 1000 Hz yields about 250
        # positive zero crossings (220 Hz only about 55).
        self.assertGreater(crossings, 170,
                           "source FROM 0.9 s plays the 1000 Hz half, not the earlier 220 Hz")
        self.assertLess(crossings, 320,
                        "melody must not run at a wildly different playback rate")
        tracks = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(output)],
            timeout=10).decode().splitlines()
        self.assertEqual(tracks, ["audio", "audio"], "original audio is kept as alternate track")

        # All three lanes and original soundtrack still combine as before.
        def mic_track(freq):
            p = r.tmp / ("cue-mic%d.m4a" % freq)
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                            "-f", "lavfi", "-i", "sine=frequency=%d:duration=0.8" % freq,
                            "-c:a", "aac", str(p)], check=True, timeout=25)
            return p.read_bytes()

        tracks = {"melody": music["file"]}
        for lane, freq in (("voice", 300), ("effects", 1300)):
            code, a = r.api("POST", "/api/video/voiceover?message=" + message +
                            "&video=" + vid + "&lane=" + lane, raw=mic_track(freq),
                            headers={"Content-Type": "audio/mp4"})
            self.assertEqual(code, 201)
            tracks[lane] = a["file"]
        code, baked = r.api("POST", "/api/video/bake", {
            "message": message, "video": vid, "lanes": tracks, "mode": "mix"})
        self.assertEqual(code, 201, baked)
        self.assertEqual(baked["video"]["voiceover_lanes"], tracks)
        stored = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files"
        self.assertEqual((stored / vid).read_bytes(), original_bytes)
        self.assertEqual((stored / music["file"]).read_bytes(), mp3.read_bytes())
        self.assertTrue((stored / baked["mix"]["file"]).exists())
        self.assertTrue((stored / baked["video"]["file"]).exists())
        self.assertEqual(r.api("DELETE", "/api/message/" + message)[0], 200)
        self.assertFalse((stored / vid).exists())
        self.assertFalse((stored / music["file"]).exists())

    def test_tool_work_continues_only_with_marker_success_and_progress(self):
        r = self.room
        calls = iter([{"content": "Schritt eins.", "tool_calls": [{"name": "remember", "arguments": {"fact": "eins"}}]},
                      "Teil fertig. [[MAG:WEITER]]",
                      {"content": "", "tool_calls": [{"name": "remember", "arguments": {"fact": "eins"}}]},
                      "Immer noch. [[MAG:WEITER]]",
                      "Unerwartet."])
        r.lena.reply = lambda body: next(calls)
        r.say("@Lena arbeite")
        r.idle()
        lena = [m for m in r.messages() if m["from"] == "lena"]
        # First turn continues by itself; the second repeated the same call
        # (no distinct progress), so the bot stays idle afterwards.
        self.assertEqual([m["reason"] for m in lena], ["human", "continue"])
        self.assertNotIn("MAG:WEITER", " ".join(m["text"] for m in lena))
        self.assertEqual(len(r.lena.chats()), 4)
        # A marker without any tool result never continues.
        r.lena.reply = lambda body: "Nur Text. [[MAG:WEITER]]"
        r.say("@Lena noch einmal")
        r.idle()
        self.assertEqual(len(r.lena.chats()), 5)

    def test_bots_share_a_notebook_of_skills(self):
        r = self.room
        skill = {"action": "save", "name": "Release bauen", "when": "Wenn Chris ein Release möchte", "steps": "1. Tests laufen lassen\n2. Tag setzen",
                 "verify": "CI ist grün", "approval": "Vor dem Veröffentlichen fragen"}
        script = iter([{"content": "", "tool_calls": [{"name": "skill", "arguments": {"action": "list"}}]},
                       {"content": "", "tool_calls": [{"name": "skill", "arguments": skill}]},
                       # Nothing that looks like a credential becomes a reusable instruction.
                       {"content": "", "tool_calls": [{"name": "skill", "arguments": dict(skill, name="Geheim", steps="Mit dem Token ghp_abc123 anmelden")}]},
                       {"content": "", "tool_calls": [{"name": "skill", "arguments": dict(skill, name="Halb", verify="")}]},
                       "Gespeichert."])
        r.lena.reply = lambda body: next(script)
        r.say("@Lena merk dir, wie wir ein Release bauen")
        r.idle()
        steps = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"] if p["k"] == "step"]
        self.assertEqual([(s["tool"], s["state"], s["title"]) for s in steps],
                         [("skill", "ok", "Sieht die Skills durch"), ("skill", "ok", "Speichert den Skill Release bauen"),
                          ("skill", "error", "Speichert den Skill Geheim"), ("skill", "error", "Speichert den Skill Halb")])
        self.assertIn("Noch kein Skill gespeichert.", steps[0]["output"])
        self.assertIn("Er beschreibt Arbeit und erlaubt nichts", steps[1]["output"])
        self.assertIn("Zugangsdaten gehören nicht in einen Skill.", steps[2]["output"])
        self.assertIn("verify fehlt", steps[3]["output"])
        # The other bot (which has no VM) finds and reads it, however it writes the name.
        script = iter([{"content": "", "tool_calls": [{"name": "skill", "arguments": {"action": "list"}}]},
                       {"content": "", "tool_calls": [{"name": "skill", "arguments": {"action": "read", "name": "release bauen"}}]},
                       "So bauen wir ein Release."])
        r.noah.reply = lambda body: next(script)
        r.say("@Noah wie bauen wir ein Release?")
        r.idle()
        steps = [p for m in r.messages() if m["from"] == "noah" for p in m["parts"] if p["k"] == "step"]
        self.assertIn("- Release bauen: Wenn Chris ein Release möchte", steps[0]["output"])
        self.assertIn("Schritte: 1. Tests laufen lassen\n2. Tag setzen\nPrüfen: CI ist grün\nFreigabe: Vor dem Veröffentlichen fragen", steps[1]["output"])
        # Chris sees what they wrote down; it survives a restart and he can remove it.
        r.stop()
        r.start()
        listed = r.api("GET", "/api/skills")[1]["skills"]
        self.assertEqual([(s["name"], s["by"]) for s in listed], [("Release bauen", "lena")])
        self.assertEqual(r.api("DELETE", "/api/skills/Release%20bauen")[0], 200)
        self.assertEqual((r.api("GET", "/api/skills")[1]["skills"], r.api("DELETE", "/api/skills/Release%20bauen")[0]), ([], 404))

    def test_trash_empties_room_and_bot_context_without_reload(self):
        r = self.room
        code, att = r.api("POST", "/api/upload?name=notiz.txt", raw=b"geheim")
        self.assertEqual(code, 201)
        r.say("Das alte Geheimnis lautet Banane", attachments=[att["file"]])
        r.idle()
        stored = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files" / att["file"]
        self.assertTrue(stored.exists())
        for fake in (r.lena, r.noah):
            fake.slot_actions.clear()
        code, out = r.api("POST", "/api/room/clear", {})
        self.assertEqual((code, out["bots"]), (200, {"lena": "ok", "noah": "ok"}))
        self.assertEqual(r.messages(), [])
        self.assertFalse(stored.exists())
        r.idle()
        for fake in (r.lena, r.noah):
            actions = [a for a, _ in fake.slot_actions]
            self.assertEqual(actions[0], "erase")           # the KV cache of the slot is emptied
            self.assertIn("restore", actions)               # only the system prefix comes back
            restored = [f for a, f in fake.slot_actions if a == "restore"]
            self.assertTrue(all(f.startswith("prefix-") for f in restored))
        before = len(r.lena.chats())
        r.say("@Lena was war das Geheimnis?")
        r.idle()
        sent = r.lena.chats()[before]["messages"]
        self.assertEqual([m["role"] for m in sent
                          if "PRIVATE SELBSTORIENTIERUNG" not in text_of(m.get("content"))],
                         ["system", "user"])
        self.assertEqual(sum("PRIVATE SELBSTORIENTIERUNG" in text_of(m.get("content"))
                             for m in sent), 1, "one request-local self orientation, not persisted wire")
        self.assertNotIn("Banane", json.dumps(sent, ensure_ascii=False))
        # Nothing of the old conversation survives a restart either.
        wires = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire"
        self.assertNotIn("Banane", "".join(p.read_text() for p in wires.glob("*.json")))

    def test_3d_result_is_a_normal_message_and_takes_its_files_along(self):
        r = self.room
        code, photo = r.api("POST", "/api/upload?name=foto.png", raw=PNG)
        self.assertEqual((code, photo["kind"]), (201, "image"))
        code, out = r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "high"})
        self.assertEqual(code, 201)
        model_id = out["model3d"]
        # While it runs, the message carries what the worker reports: step k of n and its name.
        seen = []
        r.wait(lambda: seen.append([m for m in r.messages() if m["id"] == model_id][0]["model3d"]) or seen[-1]["state"] == "running", "3D job running")
        self.assertEqual((seen[-1]["phase"], seen[-1]["phases"], seen[-1]["stage"], seen[-1]["steps"]), (2, 5, "3D-Form berechnen", 50))
        self.assertEqual(seen[-1]["image"], photo["file"])   # the photo it is made from
        r.wait(lambda: [m for m in r.messages() if m["id"] == model_id][0]["state"] == "done", "3D job done")
        messages = r.messages()
        self.assertEqual([m["from"] for m in messages], ["chris", "idol"])  # stored and ordered like any message
        result = messages[1]
        self.assertEqual((result["model3d"]["state"], result["model3d"]["quality"], result["model3d"]["took"]), ("done", "high", 420))
        self.assertEqual(sorted(a["name"].split(".")[-1] for a in result["attachments"]), ["glb", "ply", "stl"])
        files = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files"
        stored = [files / a["file"] for a in result["attachments"]]
        self.assertTrue(all(p.exists() for p in stored))
        self.assertEqual(r.api("GET", "/api/file/" + result["attachments"][0]["file"])[1], b"glTF-fake")
        self.assertEqual(r.idol.jobs[result["model3d"]["job"]]["size"], len(PNG))   # the worker got the whole photo
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), 0)  # a 3D order is not a question to the bots
        # It survives a restart (visible and rotatable again after a reload) ...
        r.stop()
        r.start()
        self.assertEqual(r.messages()[1]["attachments"], result["attachments"])
        # ... and deleting it removes its files and the worker's job directory.
        self.assertEqual(r.api("DELETE", "/api/message/" + model_id)[0], 200)
        self.assertFalse(any(p.exists() for p in stored))
        r.wait(lambda: r.idol.deleted == [result["model3d"]["job"]], "job directory removed")
        # The trash button leaves no job behind either.
        r.api("POST", "/api/room/clear", {})
        r.wait(lambda: r.idol.purges == 1, "idol purge")

    def test_3d_job_goes_on_when_the_room_restarts(self):
        r = self.room
        png = PNG

        def order():
            photo = r.api("POST", "/api/upload?name=foto.png", raw=png)[1]
            return r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "normal"})[1]["model3d"]

        def message(model_id):
            return [m for m in r.messages() if m["id"] == model_id][0]
        # The job runs in the worker; a room restart (every deploy is one) must not end it.
        r.idol.hold = True
        first = order()
        r.wait(lambda: message(first)["model3d"]["state"] == "running", "3D job running")
        r.stop()
        r.start()
        self.assertEqual((message(first)["state"], message(first)["model3d"].get("error")), ("streaming", None))
        r.idol.hold = False
        r.wait(lambda: message(first)["state"] == "done", "3D job done after the restart")
        self.assertTrue(message(first)["model3d"]["resumed"])        # shown as "fortgesetzt"
        self.assertEqual(len(message(first)["attachments"]), 3)
        # A message that an earlier room version wrote off at its restart is taken up again as well.
        r.idol.hold = True
        second = order()
        r.wait(lambda: message(second)["model3d"]["state"] == "running", "second 3D job running")
        r.stop()
        path = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "messages" / (second + ".json")
        broken = json.loads(path.read_text())
        broken["state"] = "stopped"
        broken["model3d"].update(state="error", error="Dienst neu gestartet")
        path.write_text(json.dumps(broken))
        r.start()
        r.wait(lambda: message(second)["model3d"]["state"] == "running" and message(second)["state"] == "streaming", "written-off job taken up again")
        r.idol.hold = False
        r.wait(lambda: message(second)["state"] == "done", "written-off 3D job done")
        self.assertEqual((message(second)["model3d"]["state"], message(second)["model3d"].get("error")), ("done", None))
        self.assertEqual(len(r.idol.jobs), 2)   # nothing was ordered twice

    def test_the_objects_of_a_photo_are_found_for_photo_to_3d(self):
        r = self.room
        photo = r.api("POST", "/api/upload?name=foto.png", raw=PNG)[1]
        # The one Chris tapped, and every one by itself: the worker looks, the room hands it on.
        code, found = r.api("POST", "/api/3d/select", {"file": photo["file"], "x": 0.25, "y": 0.5})
        self.assertEqual((code, found["width"], len(found["masks"]), found["masks"][0]["png"]), (200, 4, 1, "bWFzaw=="))
        self.assertEqual(r.api("POST", "/api/3d/select", {"file": photo["file"]})[0], 200)
        self.assertEqual(r.idol.selected, ["x=0.250000&y=0.500000", ""])
        self.assertEqual(r.api("POST", "/api/3d/select", {"file": "nope", "x": 0.5, "y": 0.5}), (409, {"error": "Bild fehlt oder ist größer als 20 MB."}))
        self.assertEqual(len(r.idol.jobs), 0)   # looking orders nothing

    def test_a_photo_is_ordered_as_it_is_without_being_looked_at(self):
        r = self.room
        # Two people side by side, ordered like any photo: one job of the photo as it is, nothing split unasked.
        r.idol.pieces = 2
        photo = r.api("POST", "/api/upload?name=wir.png", raw=PNG)[1]
        one = r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "fast"})[1]["model3d"]
        r.wait(lambda: [m for m in r.messages() if m["id"] == one][0]["state"] == "done", "one model")
        made = [m for m in r.messages() if m["id"] == one][0]["model3d"]
        self.assertEqual((len(r.idol.jobs), r.idol.selected, made["source"], "scene" in made), (1, [], "wir.png", False))

    def test_the_whole_room_of_a_photo_becomes_one_scene_of_its_objects(self):
        r = self.room
        place = [dict(x=-0.4, z=1.5, tall=0.8, lift=0.0), dict(x=0.6, z=2.5, tall=0.4, lift=0.9), dict(x=0.0, z=2.0, tall=1.0, lift=0.0)]
        walls = dict(left=-1.5, right=1.8, back=3.2, height=2.5, floor=[0.5, 0.4, 0.3], wall=[0.8, 0.8, 0.8])

        def order(index, group="0123456789abcdef"):
            photo = r.api("POST", "/api/upload?name=objekt-%d.png" % index, raw=PNG)[1]
            scene = dict(group=group, index=index, count=3, room=walls, **place[index])
            return r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "fast", "scene": scene})[1]["model3d"]

        scenes = lambda: [m for m in r.messages() if (m.get("model3d") or {}).get("kind") == "scene"]
        # The page asked where every object stands (the worker's scene mode), then orders each by itself.
        photo = r.api("POST", "/api/upload?name=bad.png", raw=PNG)[1]
        self.assertEqual((r.api("POST", "/api/3d/select", {"file": photo["file"], "scene": True})[0], r.idol.selected[-1]), (200, "scene=1"))
        r.idol.hold = True
        first, second, third = order(0), order(1), order(2)
        message = lambda i: [m for m in r.messages() if m["id"] == i][0]
        self.assertEqual(message(second)["model3d"]["scene"]["lift"], 0.9)
        # The first is finished: no scene yet, the others are still under way.
        r.wait(lambda: message(first)["model3d"]["state"] == "running", "first running")
        r.idol.hold = {message(i)["model3d"].get("job") for i in (second, third)}   # the others are still under way
        r.wait(lambda: message(first)["state"] == "done", "first done")
        time.sleep(1.0)
        self.assertEqual((scenes(), r.scene.specs), ([], []))
        r.idol.hold = False
        r.wait(lambda: message(third)["state"] == "done" and scenes(), "the room is put together", 40)
        # All are through: the room is one scene, each object where it stood, as tall as it was, turned as the photo saw it.
        spec = r.scene.specs[-1]
        self.assertEqual((len(r.scene.specs), spec["title"], spec["room"]["back"], spec["light"]), (1, "Raum aus dem Foto", 3.2, "studio"))
        self.assertEqual([(a["name"], a["at"], a["tall"], a["lift"], a["seen_from"]) for a in spec["actors"]],
                         [("Objekt 1", [1.5, 0.4], 0.8, 0, [0.6, 0.3, 0.74]), ("Objekt 2", [2.5, -0.6], 0.4, 0.9, [0.6, 0.3, 0.74]), ("Objekt 3", [2, 0], 1, 0, [0.6, 0.3, 0.74])])
        scene = scenes()[0]["model3d"]
        self.assertEqual((scene["group"], scene["state"], scene["spec"]["room"]["height"], [a["name"] for a in scene["spec"]["actors"]]),
                         ("0123456789abcdef", "ready", 2.5, ["Objekt 1", "Objekt 2", "Objekt 3"]))
        # Once: a restart of the room does not build it again, and what is no scene part builds none.
        r.stop()
        r.start()
        time.sleep(1.0)
        self.assertEqual((len(scenes()), len(r.scene.specs)), (1, 1))
        photo = r.api("POST", "/api/upload?name=allein.png", raw=PNG)[1]
        self.assertEqual(r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "fast", "scene": {"group": "x"}})[0], 201)
        r.wait(lambda: all(m["state"] != "streaming" for m in r.messages()), "plain job done")
        self.assertEqual(len(r.scene.specs), 1)

    def test_a_picture_from_words_becomes_an_ordinary_image_of_the_chat(self):
        r = self.room
        # A picture or a video ordered here is for the worker. It asks the bots nothing,
        # the way a 3D order does, so they do not answer and do not order it again.
        before = len(r.lena.chats()) + len(r.noah.chats())
        sent = r.api("POST", "/api/message", {"text": "ein Fuchs mit Laterne im Schnee, Aquarell", "mode": "image", "shape": "wide"})[1]
        self.assertEqual((sent["message"]["text"], sent["message"].get("silent")), ("ein Fuchs mit Laterne im Schnee, Aquarell", True))
        made = lambda: [m for m in r.messages() if m["id"] == sent["model3d"]][0]
        r.wait(lambda: made()["state"] == "done", "picture made")
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), before)
        m = made()
        self.assertEqual((m["model3d"]["kind"], m["text"], [a["kind"] for a in m["attachments"]], m["attachments"][0]["name"][-4:]),
                         ("image", "Bild: ein Fuchs mit Laterne im Schnee, Aquarell", ["image"], ".png"))
        self.assertEqual((r.idol.images[-1]["prompt"], r.idol.images[-1]["width"], r.idol.images[-1]["height"]), ("ein Fuchs mit Laterne im Schnee, Aquarell", 1344, 768))
        self.assertEqual(len(r.idol.images), 1)
        # Nothing to paint is said in words, and nothing is ordered.
        r.api("POST", "/api/message", {"text": "", "mode": "image"})
        time.sleep(0.5)
        self.assertEqual((len(r.idol.images), len(r.lena.chats()) + len(r.noah.chats())), (1, before))
        # A video from words is the same: one order, and the bots are not asked.
        r.api("POST", "/api/message", {"text": "ein Fuchs läuft durch den Schnee", "mode": "video"})
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), before)
        self.assertEqual(len(r.idol.images), 1)

    def test_a_tool_call_written_as_action_json_is_taken_out_of_the_text(self):
        """{"action": "<tool>", ...} written into the answer is not a call.

        In a normal chat the text is stored without it and the bot is told to
        call the tool for real. JSON inside a sentence, and an action that is
        not a tool, stay. An order for a picture or a video does not start a
        turn, so that written call never becomes another image job."""
        r = self.room
        exact = '{\n  "action": "image_generate",\n  "prompt": "a red fox in the snow"\n}'

        def noah(body):
            last = text_of(body["messages"][-1]["content"])
            if "als JSON in deinen Text geschrieben" in last:
                return "PASS"
            if "unbekannt" in last:
                return '{\n  "action": "not_a_tool",\n  "prompt": "bleibt"\n}'
            if "im Satz" in last:
                return 'Die Zahl ist {"ok": true} und das bleibt so.'
            if "male" in last:
                return exact
            return "PASS"
        r.noah.reply = noah
        r.lena.reply = lambda body: "PASS"

        r.say("@Noah male einen Fuchs")
        r.idle()
        stored = [m for m in r.messages() if m["from"] == "noah"]
        self.assertTrue(stored, r.messages())
        self.assertEqual((stored[-1].get("text") or "").strip(), "")
        self.assertNotIn("image_generate", json.dumps(stored[-1], ensure_ascii=False))
        notes = [text_of(m.get("content")) for c in r.noah.chats() for m in c["messages"]]
        self.assertTrue(any("image_generate" in n and "als JSON in deinen Text geschrieben" in n for n in notes), notes[-1][:500])
        self.assertEqual(r.idol.images, [])  # the written JSON was not executed

        r.say("@Noah steht das im Satz?")
        r.idle()
        self.assertIn('{"ok": true}', r.texts("noah")[-1])
        self.assertEqual(r.idol.images, [])

        before = len(r.noah.chats())
        r.say("@Noah unbekannt bitte")
        r.idle()
        self.assertEqual(len(r.noah.chats()), before + 1)  # no nudge: the action is not a tool
        self.assertIn("not_a_tool", r.texts("noah")[-1])
        self.assertIn("bleibt", r.texts("noah")[-1])

        # The same payload after a real order still nudges in the next question,
        # and the order itself never wakes the bots or starts a second image.
        def eager(body):
            last = text_of(body["messages"][-1]["content"])
            if "als JSON in deinen Text geschrieben" in last:
                return {"tool_calls": [{"name": "image_generate", "arguments": {"prompt": "a third picture"}}]}
            return exact
        r.lena.reply = eager
        r.noah.reply = eager
        before = len(r.lena.chats()) + len(r.noah.chats())
        sent = r.api("POST", "/api/message", {"text": "ein Fuchs im Schnee", "mode": "image"})[1]
        r.wait(lambda: [m for m in r.messages() if m["id"] == sent["model3d"]][0]["state"] == "done", "picture made")
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), before)
        self.assertEqual(len(r.idol.images), 1)
        r.api("POST", "/api/message", {"text": "ein Fuchs läuft durch den Schnee", "mode": "video"})
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), before)
        self.assertEqual(len(r.idol.images), 1)

        r.noah.reply = noah
        r.say("@Noah male noch einen")
        r.idle()
        notes = [text_of(m.get("content")) for c in r.noah.chats() for m in c["messages"]]
        self.assertTrue(any("als JSON in deinen Text geschrieben" in n for n in notes))
        self.assertEqual(len(r.idol.images), 1)  # the order's picture, not a third job from the written call

    def test_standalone_python_image_call_runs_once_without_visible_text(self):
        r = self.room
        # A local model can emit the exact call as ordinary streamed content
        # instead of delta.tool_calls. Only the tool event should survive.
        replies = iter(['image_generate(prompt="a fry", shape="square")', "PASS"])
        r.noah.reply = lambda body: next(replies, "PASS")
        r.lena.reply = lambda body: "PASS"
        r.say("@Noah male eine Pommes")
        r.wait(lambda: len(r.idol.images) == 1, "recovered Python image job")
        r.idle()
        self.assertEqual(len(r.idol.images), 1)
        self.assertEqual(r.idol.images[0]["prompt"], "a fry")
        self.assertEqual(r.idol.images[0]["width"], r.idol.images[0]["height"])
        stored = [m for m in r.messages() if m["from"] == "noah"]
        self.assertTrue(stored)
        self.assertTrue(any(p.get("tool") == "image_generate"
                            for m in stored for p in m.get("parts", [])))
        for m in stored:
            self.assertEqual((m.get("text") or "").strip(), "")
            self.assertNotIn('image_generate(', "".join(p.get("text", "")
                             for p in m.get("parts", []) if p.get("k") == "text"))

    def test_python_text_recovery_rejects_prose_multiple_calls_and_unsafe_tools(self):
        r = self.room
        r.lena.reply = lambda body: "PASS"
        for written in [
            'Beispiel: image_generate(prompt="a fry")',
            'image_generate(prompt="a fry") + image_generate(prompt="another")',
            'image_generate(prompt="a fry", prompt="another")',
            'image_generate(prompt=make_prompt())',
            'image_generate(prompt="a fry", shape="invalid")',
            'shell(command="echo unsafe")',
            'browser_click(n=1)',
        ]:
            r.noah.reply = lambda body, written=written: written
            r.say("@Noah erkläre diesen Ausdruck")
            r.idle()
            self.assertEqual(r.idol.images, [], written)
            self.assertFalse(any(p.get("k") == "step"
                                 for m in r.messages() if m["from"] == "noah"
                                 for p in m.get("parts", [])), written)

    def test_a_failed_3d_job_is_taken_up_again_where_it_stopped(self):
        r = self.room

        def order():
            photo = r.api("POST", "/api/upload?name=foto.png", raw=PNG)[1]
            return r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "normal"})[1]["model3d"]

        def message(model_id):
            return [m for m in r.messages() if m["id"] == model_id][0]
        # The job fails in the worker (as one did in its mesh stage, at the stage's time limit).
        r.idol.fails = "Zeitlimit 15 Minuten überschritten"
        first = order()
        r.wait(lambda: message(first)["state"] == "error", "3D job failed")
        m3 = message(first)["model3d"]
        self.assertEqual((m3["state"], m3["error"], len(r.idol.jobs)), ("error", "Zeitlimit 15 Minuten überschritten", 1))
        self.assertEqual(r.api("POST", "/api/3d/%s/retry" % "nope")[0], 404)
        # Chris asks for it again: the same job goes on, no photo is sent a second time.
        r.idol.fails = ""
        self.assertEqual(r.api("POST", "/api/3d/%s/retry" % first)[0], 200)
        r.wait(lambda: message(first)["state"] == "streaming" and "error" not in message(first)["model3d"], "taken up again")
        self.assertEqual(r.api("POST", "/api/3d/%s/retry" % first), (409, {"error": "Der Auftrag läuft noch."}))
        r.wait(lambda: message(first)["state"] == "done", "done after it was taken up again")
        m3 = message(first)["model3d"]
        self.assertEqual((m3["state"], m3["resumed"], m3.get("error"), len(message(first)["attachments"])), ("done", True, None, 3))
        self.assertEqual((len(r.idol.jobs), r.idol.retried, [m["from"] for m in r.messages()]), (1, [m3["job"]], ["chris", "idol"]))
        self.assertEqual(r.api("POST", "/api/3d/%s/retry" % first), (409, {"error": "Das Modell ist schon fertig."}))
        # A finished model is made anew from what was sampled when Chris asks for that: the same job, the same message.
        before = [a["file"] for a in message(first)["attachments"]]
        self.assertEqual(r.api("POST", "/api/3d/%s/retry" % first, {"remesh": True})[0], 200)
        r.wait(lambda: message(first)["state"] == "streaming", "being made anew")
        r.wait(lambda: message(first)["state"] == "done", "made anew")
        after = [a["file"] for a in message(first)["attachments"]]
        self.assertEqual((len(after), set(after) & set(before), len(r.idol.jobs), r.idol.retried[-1]), (3, set(), 1, m3["job"] + "?remesh=1"))
        r.idol.retried.pop()
        # A job that was cancelled is taken up again the same way.
        r.idol.hold = True
        second = order()
        r.wait(lambda: message(second)["model3d"]["state"] == "running", "second 3D job running")
        self.assertEqual(r.api("POST", "/api/3d/%s/cancel" % second)[0], 200)
        r.wait(lambda: message(second)["state"] == "stopped", "cancelled")
        r.idol.hold = False
        self.assertEqual(r.api("POST", "/api/3d/%s/retry" % second)[0], 200)
        r.wait(lambda: message(second)["state"] == "done", "the cancelled job done")
        # A job the worker no longer has cannot be taken up; the room says what to do instead.
        r.idol.fails = "Fehler"
        third = order()
        r.wait(lambda: message(third)["state"] == "error", "third 3D job failed")
        r.idol.jobs.pop(message(third)["model3d"]["job"])
        code, why = r.api("POST", "/api/3d/%s/retry" % third)
        self.assertEqual((code, why["error"], message(third)["state"]), (409, "Den 3D-Auftrag gibt es nicht mehr. Bitte das Foto noch einmal schicken.", "error"))
        photo = [m for m in r.messages() if m["from"] == "chris"][0]             # the message with the photo is no job
        self.assertEqual(r.api("POST", "/api/3d/%s/retry" % photo["id"]), (409, {"error": "Das ist kein 3D-Auftrag."}))

    def test_bots_build_a_3d_scene_from_the_models_in_the_chat(self):
        r = self.room
        files = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files"

        def message(message_id):
            return [m for m in r.messages() if m["id"] == message_id][0]

        def scenes():
            return [m for m in r.messages() if m.get("model3d", {}).get("kind") == "scene"]
        # A model in the chat: the result of a photo-to-3D job.
        photo = r.api("POST", "/api/upload?name=wald.png", raw=PNG)[1]
        out = r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "fast"})[1]
        r.wait(lambda: message(out["model3d"])["state"] == "done", "3D job done")
        model = message(out["model3d"])["attachments"][0]
        cast = [{"name": "Gnom", "model": model["name"], "part": 1}, {"name": "Fuchs", "model": "3d-000000", "part": 2}]
        walk = [{"actor": "Fuchs", "do": "walk_to", "target": "Gnom"}, {"actor": "Gnom", "do": "wave"}]

        def build(**what):
            return {"name": "scene_3d", "arguments": dict({"title": "Fuchs besucht Gnom"}, **what)}
        script = iter([
            {"content": "Ich sehe nach, welche Modelle es gibt.", "tool_calls": [{"name": "models_3d", "arguments": {}}]},
            # Without saying which figure of the model is meant: the worker refuses, and says why.
            {"content": "", "tool_calls": [build(actors=[{"name": "Fuchs", "model": model["name"]}], actions=[])]},
            {"content": "", "tool_calls": [build(actors=cast, actions=walk, ground="snow")]},
            # Not content with it and at it again in the same answer: one scene per request.
            {"content": "", "tool_calls": [build(title="Zweiter Versuch", actors=cast, actions=walk)]},
            "Der Fuchs läuft jetzt zum Gnom."])
        r.lena.reply = lambda body: next(script)
        r.say("@Lena lass den Fuchs zum Gnom laufen")
        r.idle()
        steps = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"] if p["k"] == "step"]
        self.assertEqual([(s["tool"], s["state"]) for s in steps], [("models_3d", "ok"), ("scene_3d", "error"), ("scene_3d", "ok"), ("scene_3d", "error")])
        self.assertIn("Zu dieser Bitte steht schon eine Szene im Chat", steps.pop()["output"])
        self.assertEqual(len(r.scene.specs), 2)   # the worker was not asked again
        self.assertEqual(steps[2]["title"], "Baut die 3D-Szene Fuchs besucht Gnom")
        # The bot is told what there is: the model, where it comes from, and what each of its figures is.
        self.assertIn("1. %s (aus dem Foto wald.png) zeigt 2 Figuren" % model["name"], steps[0]["output"])
        self.assertIn("part 2: Vierbeiner, länglich und niedrig (länger als hoch), 0.18 hoch, 0.50 lang, steht im Bild rechts", steps[0]["output"])
        self.assertIn("Die Szene wurde nicht gebaut: Das Modell %s zeigt 2 Figuren." % model["name"], steps[1]["output"])
        self.assertEqual(r.scene.asked, [model["file"]])
        # The worker gets the room's file for the name the bot used (with or without its ending), and no order for a film.
        spec = r.scene.specs[-1]
        self.assertEqual([(a["name"], a["file"], a["model"], a["part"]) for a in spec["actors"]],
                         [("Gnom", model["file"], model["name"], 1), ("Fuchs", model["file"], model["name"], 2)])
        self.assertEqual((spec["ground"], spec["actions"], spec["film"]), ("snow", walk, False))
        told = r.lena.chats()[-2]["messages"][-1]
        self.assertEqual(told["role"], "tool")
        self.assertIn("steht jetzt im Chat (4.5 Sekunden; Darsteller: Gnom (Zweibeiner: 2 Beine, Rumpf, Kopf); Fuchs (Vierbeiner: 4 Beine, Kopf und Schwanz))", told["content"])
        self.assertIn("Einen Film (MP4) davon lässt er bei Bedarf selbst an der Szene rendern.", told["content"])
        # The scene is a finished message of its own and plays at once. It keeps how it was made and what was asked for.
        self.assertEqual(len(scenes()), 1)       # the refused call left nothing in the chat
        scene = scenes()[0]
        m3 = scene["model3d"]
        self.assertEqual((scene["from"], scene["state"], m3["state"], m3["by"], m3["cast"], m3["kinds"], m3["title"]),
                         ("idol", "done", "ready", "lena", ["Gnom", "Fuchs"], ["Zweibeiner", "Vierbeiner"], "Fuchs besucht Gnom"))
        recipe = {"title": "Fuchs besucht Gnom", "actors": [{"name": "Gnom", "model": model["name"], "part": 1}, {"name": "Fuchs", "model": model["name"], "part": 2}],
                  "actions": walk, "ground": "snow"}
        self.assertEqual((m3["spec"], m3["prompt"]), (recipe, "lass den Fuchs zum Gnom laufen"))
        self.assertEqual([a["name"] for a in scene["attachments"]], ["szene-000000.glb"])
        self.assertEqual(r.api("GET", "/api/file/" + scene["attachments"][0]["file"])[1], b"glTF-scene")
        self.assertGreater(scene["id"], [m for m in r.messages() if m["from"] == "lena"][0]["id"])   # below the answer that made it
        # No film is rendered that nobody asked for. Chris asks for it at the scene.
        time.sleep(0.5)
        self.assertEqual((r.scene.films, r.scene.jobs[m3["job"]]["state"], r.scene.fetched), ([], "built", ["scene.glb"]))
        r.scene.hold = True
        self.assertEqual(r.api("POST", "/api/3d/%s/film" % scene["id"])[0], 200)
        self.assertEqual(r.api("POST", "/api/3d/%s/film" % scene["id"]), (409, {"error": "Der Film wird schon gerendert."}))
        r.wait(lambda: message(scene["id"])["model3d"].get("steps") == 109, "film rendering")
        self.assertEqual((message(scene["id"])["state"], message(scene["id"])["model3d"]["phase"], message(scene["id"])["model3d"]["stage"]), ("streaming", 2, "Film rendern"))
        # The film is rendered by the worker: a restart of the room (every deploy is one) does not end it.
        r.stop()
        r.start()
        time.sleep(0.6)
        self.assertEqual((message(scene["id"])["state"], message(scene["id"])["model3d"].get("error")), ("streaming", None))
        r.scene.hold = False
        r.wait(lambda: message(scene["id"])["state"] == "done", "film done after the restart")
        done = message(scene["id"])
        self.assertEqual([(a["name"], a["kind"]) for a in done["attachments"]], [("szene-000000.glb", "model"), ("szene-000000.mp4", "video")])
        self.assertEqual((done["model3d"]["state"], done["model3d"]["film"], done["model3d"]["took"]), ("done", done["attachments"][1]["file"], 90))
        self.assertEqual(r.api("GET", "/api/file/" + done["attachments"][1]["file"])[1], b"film")
        self.assertEqual((r.api("POST", "/api/3d/%s/film" % scene["id"]), r.scene.films), ((409, {"error": "Den Film gibt es schon."}), [m3["job"]]))
        # The file for Blender is fetched when Chris wants it, once.
        code, got = r.api("POST", "/api/3d/%s/blend" % scene["id"])
        self.assertEqual((code, got["file"]["name"], r.api("GET", "/api/file/" + got["file"]["file"])[1]), (200, "szene-000000.blend", b"BLENDER"))
        self.assertEqual(r.api("POST", "/api/3d/%s/blend" % scene["id"])[1], got)
        done = message(scene["id"])
        self.assertEqual(([a["name"] for a in done["attachments"]], r.scene.fetched.count("scene.blend")), (["szene-000000.glb", "szene-000000.mp4", "szene-000000.blend"], 1))
        self.assertEqual(r.api("POST", "/api/3d/%s/film" % out["model3d"])[0], 404)     # a 3D model is not a scene
        # The other bot reads what was built (it asks him nothing); every bot has the tools, also one without a VM.
        r.lena.reply = None
        before = len(r.noah.chats())
        r.say("@Noah was gibt es Neues?")
        r.idle()
        asked = r.noah.chats()[before]
        self.assertIn('3D-Szene: "Fuchs besucht Gnom" (4.5 Sekunden; Darsteller: Gnom, Fuchs), gebaut von Lena. Chris kann sie im Chat drehen und abspielen. '
                      "Ihr Film ist fertig. Dateien: szene-000000.glb", text_of(asked["messages"][-1]["content"]))
        self.assertLessEqual({"models_3d", "scene_3d"}, {t["function"]["name"] for t in asked["tools"]})
        # A new movement is written right at the scene. The bot reads how the scene was made, so the new one has the same figures.
        leap = [{"actor": "Fuchs", "do": "jump"}]
        script = iter([{"content": "", "tool_calls": [build(title="Fuchs springt", actors=cast, actions=leap, ground="snow")]}, "Jetzt springt er."])
        r.lena.reply = lambda body: next(script)
        before, noahs = len(r.lena.chats()), len(r.noah.chats())
        code, sent = r.api("POST", "/api/message", {"text": "@Lena Der Fuchs springt über den Gnom", "about": scene["id"]})
        self.assertEqual((code, sent["message"]["about"], sent["message"]["mentions"]), (201, scene["id"], ["lena"]))
        r.idle()
        asked = text_of(r.lena.chats()[before]["messages"][-1]["content"])
        note = ('[Sprecher Chris] @Lena Der Fuchs springt über den Gnom\n[Chris schreibt das zur 3D-Szene "Fuchs besucht Gnom": Er möchte eine neue Fassung mit denselben Figuren. '
                "So wurde sie mit scene_3d gebaut: " + json.dumps(recipe, ensure_ascii=False, separators=(",", ":")) +
                " Baue die neue mit dem Werkzeug scene_3d (nicht als JSON im Text): dieselben actors, die actions nach seinem Wunsch.]")
        self.assertIn(note, asked)
        self.assertEqual(len(r.noah.chats()), noahs)                                    # it asks Noah nothing
        again = scenes()[-1]
        self.assertEqual((again["model3d"]["title"], again["model3d"]["prompt"], again["model3d"]["spec"]["actions"], again["state"]),
                         ("Fuchs springt", "Der Fuchs springt über den Gnom", leap, "done"))
        self.assertEqual(r.scene.films, [m3["job"]])                                     # and no film of it either
        # A scene from before its recipe was kept with the message: the worker still knows it.
        r.stop()
        path = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "messages" / (again["id"] + ".json")
        old = json.loads(path.read_text())
        del old["model3d"]["spec"]
        path.write_text(json.dumps(old))
        r.start()
        script = iter(["Mache ich."])
        before = len(r.lena.chats())
        r.api("POST", "/api/message", {"text": "@Lena und jetzt rückwärts", "about": again["id"]})
        r.idle()
        self.assertEqual(message(again["id"])["model3d"]["spec"]["actions"], leap)
        asked = text_of(r.lena.chats()[before]["messages"][-1]["content"])
        self.assertIn('"actions":[{"actor":"Fuchs","do":"jump"}]', asked)
        # The bots read a scene without a film as what it is.
        self.assertIn('3D-Szene: "Fuchs springt" (4.5 Sekunden; Darsteller: Gnom, Fuchs), gebaut von Lena. Chris kann sie im Chat drehen und abspielen. '
                      "Einen Film davon gibt es, wenn Chris ihn an der Szene rendern lässt. Dateien: szene-", asked)
        # At a 3D model the same line asks for its animation; a message about anything else is an ordinary message.
        script = iter(["Gern.", "Hm."])
        before = len(r.lena.chats())
        r.api("POST", "/api/message", {"text": "@Lena lass ihn tanzen", "about": out["model3d"]})
        r.idle()
        self.assertIn("[Sprecher Chris] @Lena lass ihn tanzen\n[Chris schreibt das zum 3D-Modell %s: Er möchte es animiert sehen. Sieh mit models_3d nach, welche Figuren es zeigt, "
                      "und baue die Szene mit scene_3d.]" % model["name"], text_of(r.lena.chats()[before]["messages"][-1]["content"]))
        self.assertNotIn("about", r.api("POST", "/api/message", {"text": "@Lena danke", "about": sent["message"]["id"]})[1]["message"])
        r.idle()
        # A scene is not a model: the next scene is made of the model again, not of the first scene.
        script = iter([{"content": "", "tool_calls": [{"name": "models_3d", "arguments": {}}]},
                       {"content": "", "tool_calls": [build(actors=[{"name": "Fuchs", "model": "", "part": 2}], actions=[{"actor": "Fuchs", "do": "jump"}])]},
                       "Noch eine."])
        r.lena.reply = lambda body: next(script)
        r.say("@Lena noch eine Szene, nur mit dem Fuchs")
        r.idle()
        steps = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"] if p["k"] == "step"][-2:]
        self.assertNotIn("szene-", steps[0]["output"])
        self.assertEqual(r.scene.specs[-1]["actors"][0]["file"], model["file"])   # no name: the newest model
        # Its film is cancelled: the scene stays in its message. Photo to 3D is not asked about any of this.
        second = scenes()[-1]
        r.scene.hold = True
        self.assertEqual(r.api("POST", "/api/3d/%s/film" % second["id"])[0], 200)
        r.wait(lambda: message(second["id"])["model3d"].get("steps") == 109, "second film rendering")
        self.assertEqual(r.api("POST", "/api/3d/%s/cancel" % second["id"])[0], 200)
        r.wait(lambda: message(second["id"])["state"] == "stopped", "film cancelled")
        self.assertEqual((message(second["id"])["model3d"]["state"], len(message(second["id"])["attachments"])), ("cancelled", 1))
        self.assertEqual((r.scene.cancelled, r.idol.cancelled), ([second["model3d"]["job"]], []))
        # A film that did not come about can be rendered after all.
        r.scene.hold = False
        self.assertEqual(r.api("POST", "/api/3d/%s/film" % second["id"])[0], 200)
        r.wait(lambda: message(second["id"])["state"] == "done", "film rendered after all")
        self.assertEqual(([a["kind"] for a in message(second["id"])["attachments"]], message(second["id"])["model3d"].get("error")), (["model", "video"], None))
        # Chris writes again while the bot is still at work: that is a new
        # request, and it may be answered with another scene. What the bot
        # wrote before it had read him is still the old request.
        def meanwhile(body):
            r.api("POST", "/api/message", {"text": "@Lena mach sie bitte länger"})
            return {"content": "", "tool_calls": [build(title="Zu früh", actors=cast, actions=walk)]}
        script = iter([{"content": "", "tool_calls": [build(title="Erste", actors=cast, actions=walk)]}, meanwhile,
                       {"content": "", "tool_calls": [build(title="Länger", actors=cast, actions=walk + walk)]}, "Jetzt ist sie länger."])
        r.lena.reply = lambda body: (lambda reply: reply(body) if callable(reply) else reply)(next(script))
        r.say("@Lena noch eine Szene mit beiden")
        r.idle()
        steps = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"] if p["k"] == "step"][-3:]
        self.assertEqual([(s["title"], s["state"]) for s in steps],
                         [("Baut die 3D-Szene Erste", "ok"), ("Baut die 3D-Szene Zu früh", "error"), ("Baut die 3D-Szene Länger", "ok")])
        self.assertEqual([m["model3d"]["title"] for m in scenes()][-2:], ["Erste", "Länger"])
        # His second message was answered in that turn: it does not get a turn of its own on top.
        self.assertEqual(([m for m in r.messages() if m["from"] == "lena"][-1]["text"], next(script, None)), ("Jetzt ist sie länger.", None))
        # Deleting a scene removes its files and the worker's folder.
        stored = [files / a["file"] for a in done["attachments"]]
        self.assertTrue(len(stored) == 3 and all(p.exists() for p in stored))
        self.assertEqual(r.api("DELETE", "/api/message/" + scene["id"])[0], 200)
        self.assertFalse(any(p.exists() for p in stored))
        r.wait(lambda: r.scene.deleted == [done["model3d"]["job"]], "scene folder removed")
        self.assertEqual(r.idol.deleted, [])
        r.api("POST", "/api/room/clear", {})
        r.wait(lambda: r.scene.purges == 1, "scene purge")

    def test_plugins_are_remote_tools_the_owner_binds_and_approves(self):
        r = self.room
        mcp = FakeMcp(token="s3cr3t-token-1")
        self.addCleanup(mcp.close)
        state = pathlib.Path(r.env["MAG_ROOM_STATE"])

        def lena():
            return [b for b in r.state()["bots"] if b["id"] == "lena"][0]

        def steps():
            return [p for m in r.messages() if m["from"] == "lena" for p in m["parts"] if p["k"] == "step"]

        def call(tool, **args):
            return {"content": "", "tool_calls": [{"name": "mcp__notizen__" + tool, "arguments": args}]}

        def ask(*script):
            """Chris asks Lena, who answers by the script; returns once she is working."""
            replies = iter(script)
            r.lena.reply = lambda body: next(replies)
            before = len(steps())
            r.say("@Lena bitte")
            r.wait(lambda: len(steps()) > before, "a new step")

        def pending():
            r.wait(lambda: steps()[-1].get("approval"), "an approval that waits")
            return steps()[-1]["approval"]
        # Only what is safe to reach: https, or plain http on this machine and the tailnet.
        self.assertEqual(r.api("POST", "/api/plugins", {"name": "Fern", "url": "http://example.org/mcp"}),
                         (400, {"error": "Nur https:// (http:// nur für diesen Rechner und das Tailnet)."}))
        self.assertEqual(r.api("POST", "/api/plugins", {"name": "Notizen", "url": mcp.url, "auth": "bearer"})[0], 400)   # a token is needed
        # The owner connects a server: the room reads its tools. The token is kept apart and never handed out.
        code, out = r.api("POST", "/api/plugins", {"name": "Notizen", "url": mcp.url, "auth": "bearer", "secret": "s3cr3t-token-1", "guide": "Für Chris' Notizen."})
        self.assertEqual((code, out["plugin"]["id"], out["plugin"]["status"]), (200, "notizen", "ok"))
        self.assertEqual([(t["name"], t["read_only"]) for t in out["plugin"]["tools"]], [("lookup", True), ("write_note", False), ("boom", True)])
        listed = r.api("GET", "/api/plugins")[1]["plugins"]
        self.assertEqual([(p["id"], p["name"], p["auth"], p["guide"], len(p["tools"])) for p in listed], [("notizen", "Notizen", "bearer", "Für Chris' Notizen.", 3)])
        self.assertNotIn("s3cr3t", json.dumps(listed) + (state / "mcp" / "servers.json").read_text())
        secret = state / "mcp" / "notizen.secret"
        self.assertEqual((secret.read_text(), secret.stat().st_mode & 0o777), ("s3cr3t-token-1", 0o600))
        # Nobody has it until the owner gives it to a bot.
        r.say("@Lena hallo")
        r.idle()
        self.assertFalse([t for t in r.lena.chats()[-1]["tools"] if t["function"]["name"].startswith("mcp__")])
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"plugins": {"gibtsnicht": {}}})[0], 400)
        room = lena()["ctx_budget"]
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"plugins": {"notizen": {"approval": "ask"}}})[0], 200)
        self.assertEqual(lena()["plugins"], {"notizen": {"approval": "ask", "off": [], "always": []}})
        # What the plugin takes at the head of the prompt is not taken from the conversation; the owner is told roughly how much it is.
        self.assertTrue(100 < lena()["ctx_budget"] - room < 1000 and all(t["tokens"] > 30 for t in listed[0]["tools"]), lena()["ctx_budget"])
        # Reading needs no approval. The bot has the tools under the plugin's name, and is told what the plugin is.
        ask(call("lookup", key="farbe"), "Blau.")
        r.idle()
        asked = r.lena.chats()[-1]
        tools = {t["function"]["name"]: t["function"] for t in asked["tools"]}
        self.assertEqual([n for n in tools if n.startswith("mcp__")], ["mcp__notizen__lookup", "mcp__notizen__write_note", "mcp__notizen__boom"])
        self.assertEqual((tools["mcp__notizen__lookup"]["description"], tools["mcp__notizen__lookup"]["parameters"]["required"]),
                         ("[Plugin Notizen] Looks a note up by its key.", ["key"]))
        self.assertIn("- Plugin Notizen: Für Chris' Notizen. Hinweise des Dienstes: Notes live here. Look a note up before you write it.", asked["messages"][0]["content"])
        r.lena.reply = None
        r.say("@Noah und du?")
        r.idle()
        self.assertFalse([t for t in r.noah.chats()[-1]["tools"] if t["function"]["name"].startswith("mcp__")])     # Noah was not given it
        step = steps()[-1]
        self.assertEqual((step["title"], step["state"], step["output"], step.get("approval")), ("lookup · Notizen", "ok", "blau", None))
        self.assertEqual((mcp.calls[-1]["tool"], mcp.calls[-1]["args"], mcp.calls[-1]["protocol"], mcp.refused), ("lookup", {"key": "farbe"}, "2025-06-18", 0))
        self.assertEqual(asked["messages"][-1]["content"], "blau")
        # Writing waits for Chris: the step says what is asked, nothing has happened yet.
        ask(call("write_note", key="farbe", text="grün"), "Notiert.")
        waiting = pending()
        self.assertEqual((waiting["tool"], waiting["server"], waiting["bot"], json.loads(waiting["args"])), ("write_note", "Notizen", "lena", {"key": "farbe", "text": "grün"}))
        self.assertEqual((lena()["phase"], steps()[-1]["state"], mcp.notes["farbe"], len(mcp.calls)), ("approval", "running", "blau", 1))
        self.assertEqual(r.api("POST", "/api/approval/" + waiting["id"], {"decision": "vielleicht"})[0], 409)
        self.assertEqual(r.api("POST", "/api/approval/" + waiting["id"], {"decision": "allow"})[0], 200)
        r.idle()
        self.assertEqual((steps()[-1]["state"], steps()[-1]["output"], steps()[-1].get("approval"), mcp.notes["farbe"]), ("ok", "gespeichert", None, "grün"))
        self.assertEqual(r.api("POST", "/api/approval/" + waiting["id"], {"decision": "allow"})[0], 409)       # answered
        # No: it does not happen, and the bot is told so.
        ask(call("write_note", key="farbe", text="rot"), "Dann nicht.")
        self.assertEqual(r.api("POST", "/api/approval/" + pending()["id"], {"decision": "deny"})[0], 200)
        r.idle()
        self.assertEqual((steps()[-1]["state"], mcp.notes["farbe"]), ("error", "grün"))
        self.assertIn("Chris hat den Aufruf von write_note abgelehnt.", r.lena.chats()[-1]["messages"][-1]["content"])
        # Stop while it waits: nothing happens either.
        ask(call("write_note", key="farbe", text="rot"), "Hm.")
        pending()
        r.api("POST", "/api/stop", {"bot": "lena"})
        r.idle()
        self.assertEqual((steps()[-1]["state"], steps()[-1].get("approval"), mcp.notes["farbe"]), ("cancelled", None, "grün"))
        # "Always" is remembered for this tool of this bot: the next call runs without asking. It survives a restart.
        ask(call("write_note", key="farbe", text="gelb"), "Notiert.")
        self.assertEqual(r.api("POST", "/api/approval/" + pending()["id"], {"decision": "always"})[0], 200)
        r.idle()
        self.assertEqual((mcp.notes["farbe"], lena()["plugins"]["notizen"]["always"]), ("gelb", ["write_note"]))
        r.stop()
        r.start()
        self.assertEqual((lena()["plugins"]["notizen"]["always"], [p["id"] for p in r.api("GET", "/api/plugins")[1]["plugins"]]), (["write_note"], ["notizen"]))
        mcp.stream = True          # this server answers as an event stream from now on
        ask(call("write_note", key="farbe", text="lila"), "Notiert.")
        r.idle()
        self.assertEqual((steps()[-1]["state"], steps()[-1].get("approval"), mcp.notes["farbe"]), ("ok", None, "lila"))
        # A session the server forgot is opened again; a tool that reports an error has failed, in its words.
        mcp.expire = True
        inits = mcp.inits
        ask(call("lookup", key="farbe"), call("boom"), "Das ging schief.")
        r.idle()
        self.assertEqual([(s["state"], s["output"]) for s in steps()[-2:]], [("ok", "lila"), ("error", "Fehler: das ging schief")])
        self.assertEqual(mcp.inits, inits + 1)
        # A tool the owner switched off is not there, and cannot be called.
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"plugins": {"notizen": {"approval": "ask", "off": ["boom"], "always": ["write_note"]}}})[0], 200)
        ask(call("boom"), "Gibt es nicht.")
        r.idle()
        self.assertNotIn("mcp__notizen__boom", [t["function"]["name"] for t in r.lena.chats()[-1]["tools"]])
        self.assertIn("nicht eingeschaltet", steps()[-1]["output"])
        # The server got a new tool: the owner reads its tools again.
        mcp.tools.append({"name": "count", "description": "Counts notes.", "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object", "properties": {}}})
        code, out = r.api("POST", "/api/plugins/notizen/refresh")
        self.assertEqual((code, [t["name"] for t in out["plugin"]["tools"]]), (200, ["lookup", "write_note", "boom", "count"]))
        # A wrong token: the plugin says so and keeps the tools it knew.
        mcp.token = "ein-anderes"
        out = r.api("POST", "/api/plugins/notizen/refresh")[1]["plugin"]
        self.assertEqual((out["status"], out["error"], len(out["tools"])), ("error", "Anmeldung abgelehnt (HTTP 401). Token oder Rechte prüfen.", 4))
        # Removed: gone from the bot, and its token with it.
        self.assertEqual(r.api("DELETE", "/api/plugins/notizen")[0], 200)
        self.assertEqual((lena()["plugins"], secret.exists(), r.api("GET", "/api/plugins")[1]["plugins"]), ({}, False, []))
        r.lena.reply = None
        r.say("@Lena noch da?")
        r.idle()
        self.assertFalse([t for t in r.lena.chats()[-1]["tools"] if t["function"]["name"].startswith("mcp__")])

    def test_an_approval_nobody_gives_runs_out(self):
        r = Room(MAG_MCP_APPROVAL_S="2")
        self.addCleanup(r.close)
        mcp = FakeMcp()
        self.addCleanup(mcp.close)
        self.assertEqual(r.api("POST", "/api/plugins", {"name": "Notizen", "url": mcp.url})[0], 200)
        # "auto": the owner lets this bot use everything of the plugin without asking.
        self.assertEqual(r.api("PUT", "/api/bot/noah", {"plugins": {"notizen": {"approval": "auto"}}})[0], 200)
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"plugins": {"notizen": {}}})[0], 200)
        write = {"content": "", "tool_calls": [{"name": "mcp__notizen__write_note", "arguments": {"key": "a", "text": "b"}}]}
        for bot in (r.noah, r.lena):
            replies = iter([write, "Gut."])
            bot.reply = lambda body, replies=replies: next(replies)
        r.say("@Noah bitte")
        r.idle()
        self.assertEqual((mcp.notes.get("a"), len(mcp.calls)), ("b", 1))
        r.say("@Lena bitte")
        r.idle(30)
        step = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"] if p["k"] == "step"][-1]
        self.assertEqual((step["state"], step.get("approval"), len(mcp.calls)), ("error", None, 1))
        self.assertIn("nicht freigegeben", step["output"])

    def test_a_model_that_loses_its_way_is_ended_and_leaves_no_mess(self):
        r = self.room
        state = pathlib.Path(r.env["MAG_ROOM_STATE"])
        markup = ("<|channel>", "<channel|>", "<tool_call|>", "<|tool_call>", "<|channel|>", "<|message|>", "<|start|>", "<|end|>", "\\n")

        def answer(bot="lena"):
            return [m for m in r.messages() if m["from"] == bot][-1]

        def clean(message):
            text = json.dumps(message, ensure_ascii=False)
            return not any(tag in text for tag in markup)

        def ask(bot, *script, text="bitte"):
            replies = iter(script)
            getattr(r, bot).reply = lambda body: next(replies, "")   # sent on after a failed step, it has nothing to add
            r.say("@%s %s" % (bot.capitalize(), text))
            r.idle(40)
            return answer(bot)
        # 1. A tool call runs away (Lena, 2026-11-11): the model closes a string
        #    with a plain quote, the server keeps reading it as that string,
        #    and the model writes its markup and its answer again and again
        #    until its tokens run out. The room ends the generation at the
        #    first tag, calls what was meant, and the log stays readable.
        circle = "\\n<|channel>thought\\n<channel|>Die neue Szene ist bereit! Ich habe eine längere Abfolge eingebaut. 🦊✨|"
        away = '{"fact":"Chris mag längere Szenen\\"}<tool_call|><tool_call|>' + circle * 80
        r.lena.delay = 0.004
        m = ask("lena", {"content": "", "raw_tool_calls": [{"name": "remember", "arguments": away}]}, "Notiert.")
        step = [p for p in m["parts"] if p["k"] == "step"][0]
        self.assertEqual((m["state"], m["text"], m.get("error"), step["tool"], step["state"], step["input"]),
                         ("done", "Notiert.", None, "remember", "ok", {"fact": "Chris mag längere Szenen"}))
        self.assertEqual([e["text"] for e in r.api("GET", "/api/bot/lena/memory")[1]["memory"]], ["Chris mag längere Szenen"])
        self.assertTrue(clean(m), m)
        sent = r.lena.chats()[-1]["messages"]
        self.assertEqual([c["function"]["arguments"] for x in sent for c in x.get("tool_calls") or []], ['{"fact":"Chris mag längere Szenen"}'])
        self.assertEqual((r.lena.hung_up, r.lena.refused), (1, 0))
        # 2. Arguments beyond repair: the call is not made, the bot is told why, and the log holds {} for it.
        m = ask("lena", {"content": "", "raw_tool_calls": [{"name": "remember", "arguments": '{"fact":"abgeschnitten'}]}, "Dann eben nicht.")
        step = [p for p in m["parts"] if p["k"] == "step"][0]
        self.assertEqual((m["state"], m["text"], step["state"]), ("done", "Dann eben nicht.", "error"))
        self.assertIn("kein gültiges JSON-Objekt", step["output"])
        self.assertEqual([c["function"]["arguments"] for x in r.lena.chats()[-1]["messages"] for c in x.get("tool_calls") or []][-1], "{}")
        self.assertEqual((r.lena.refused, len(r.api("GET", "/api/bot/lena/memory")[1]["memory"])), (0, 1))
        # 3. Raw channel markup in an answer is read, never shown: Gemma's thoughts, gpt-oss's channels.
        r.lena.delay = 0
        m = ask("lena", "<|channel>thought\nKurz überlegen.<channel|>Die Antwort ist 4.<channel|>")
        self.assertEqual((m["text"], m["reasoning"], clean(m)), ("Die Antwort ist 4.", "Kurz überlegen.", True))
        m = ask("noah", "<|channel|>analysis<|message|>Hm.<|end|><|start|>assistant<|channel|>final<|message|>Passt so.")
        self.assertEqual((m["text"], m["reasoning"], clean(m)), ("Passt so.", "Hm.", True))
        # Thoughts are shown as an outline without its shared indent and without empty entries, or not at all.
        m = ask("lena", {"reasoning": "*   Status: Lena ist da.\n    *   Ziel: antworten.\n    *   \n\n\n    *   Kurz bleiben.\n", "content": "Fertig."})
        self.assertEqual((m["text"], m["reasoning"]), ("Fertig.", "*   Status: Lena ist da.\n*   Ziel: antworten.\n\n*   Kurz bleiben."))
        m = ask("lena", {"reasoning": "*   \n    *   \n    *   \n", "content": "Nichts gedacht."})
        self.assertEqual((m["text"], "reasoning" in m), ("Nichts gedacht.", False))
        m = ask("lena", "Gern.")   # and the bot's own log holds what was shown
        self.assertEqual([x["content"] for x in r.lena.chats()[-1]["messages"] if x["role"] == "assistant" and x["content"]][-3], "Die Antwort ist 4.")
        # A browser task wrote its plan into the chat, and an unclosed "<thought"
        # with the next plan. Neither is stored, and neither is streamed.
        plan = "Step 1: Type search term in element 3. Step 2: Press Enter. Step 3: Look at results."
        scroll = "Step 1: Scroll down to see more results..."

        def streamed(since):
            return "".join((e.get("text") or "") for e in r.events[since:] if e.get("t") == "delta")
        mark = len(r.events)
        m = ask("lena", "Ich schaue nach.\n" + plan + "\nFertig.")
        self.assertEqual(m["text"], "Ich schaue nach.\nFertig.")
        self.assertNotIn(plan, m["text"] + streamed(mark))
        mark = len(r.events)
        m = ask("lena", "Weiter im Blick.\n<thought\n" + scroll)
        self.assertEqual((m["text"], "<thought" in m["text"] + streamed(mark), scroll in m["text"] + streamed(mark)), ("Weiter im Blick.", False, False))
        # 4. An answer in circles is ended and kept once.
        passage = "Die neue Szene ist bereit! Ich habe eine längere Abfolge an Bewegungen eingebaut, damit es lebhafter zugeht. 🦊✨\n\n"
        r.lena.delay = 0.002
        hung = r.lena.hung_up
        m = ask("lena", "Hier ist sie:\n\n" + passage * 60)
        self.assertEqual((m["state"], m["text"], m.get("error")), ("done", ("Hier ist sie:\n\n" + passage).strip(), None))
        self.assertEqual(r.lena.hung_up, hung + 1)
        self.assertLess(r.lena.pieces, len(passage) * 12 // 6)                # ended after a few rounds, not sixty
        self.assertEqual(r.lena.chats()[-1]["messages"][-1]["role"], "user")   # nothing was asked again
        r.lena.delay = 0
        # Code may repeat itself: a page with sixty equal cells is not cut.
        page = "```html\n<div class=\"grid\">\n" + "  <div class=\"cell\"></div>\n" * 60 + "</div>\n```"
        m = ask("lena", "Bitte:\n\n" + page)
        self.assertEqual((m["state"], m["text"].count('<div class="cell">')), ("done", 60))
        # 5. Thoughts in circles: once more from the start; twice, and the bot says so in one line.
        thought = "Ich muss prüfen, ob der Fuchs schon läuft, und dann noch einmal prüfen, ob er wirklich läuft. " * 40
        m = ask("lena", {"reasoning": thought, "content": ""}, {"reasoning": "Kurz.", "content": "Jetzt klar."})
        self.assertEqual((m["state"], m["text"], m["reasoning"]), ("done", "Jetzt klar.", "Kurz."))
        m = ask("lena", {"reasoning": thought, "content": ""}, {"reasoning": thought, "content": ""})
        self.assertEqual((m["state"], m["text"], m.get("reasoning", "")), ("error", "", ""))
        self.assertIn("beim Nachdenken im Kreis gedreht", m["error"])
        # 6. A server error that quotes what the model wrote is one short line in the chat.
        m = ask("noah", {"http_error": "Failed to parse input at pos 0: <|channel>thought\\n<channel|>" + "Die neue Szene ist bereit!\\n" * 200})
        self.assertEqual((m["state"], m["error"], clean(m)), ("error", "Failed to parse input at pos 0:", True))
        # 7. A log and a message from before all this: the log is repaired when
        #    it is read (the server would refuse every request), the message's
        #    error is cut down.
        r.stop()
        wire = state / "wire" / "lena.json"
        log = json.loads(wire.read_text())
        log["messages"] += [{"role": "assistant", "content": "", "tool_calls": [{"id": "call_old", "type": "function",
                                                                                 "function": {"name": "remember", "arguments": away}}]},
                            {"role": "tool", "tool_call_id": "call_old", "name": "remember", "content": "Fehler: Die Argumente sind kein gültiges JSON-Objekt."}]
        wire.write_text(json.dumps(log))
        old = state / "messages" / (m["id"] + ".json")
        stored = json.loads(old.read_text())
        stored["error"] = ("Failed to parse tool call arguments as JSON: [json.exception.parse_error.101] parse error at line 1, column 12345: "
                           "syntax error while parsing value - invalid string: missing closing quote; last read: '" + away + "'")
        old.write_text(json.dumps(stored))
        r.start()
        self.assertEqual(answer("noah")["error"], "Ein Werkzeugaufruf im Verlauf war nicht lesbar; der Modell-Server hat die Anfrage abgelehnt.")
        m = ask("lena", "Ja, ich bin da.", text="noch da?")
        self.assertEqual((m["state"], m["text"], r.lena.refused), ("done", "Ja, ich bin da.", 0))
        self.assertEqual([c["function"]["arguments"] for x in r.lena.chats()[-1]["messages"] for c in x.get("tool_calls") or []][-1],
                         '{"fact":"Chris mag längere Szenen"}')

    def test_a_bot_is_reset_to_its_first_day_whatever_state_it_is_in(self):
        ctl = pathlib.Path(tempfile.mkdtemp(prefix="magctl-"))
        self.addCleanup(shutil.rmtree, ctl, True)
        r = Room(MAG_ROOM_AGENT_CTL=str(HERE / "fake_agent_ctl"), FAKE_CTL=str(ctl))
        self.addCleanup(r.close)
        state, guests = pathlib.Path(r.env["MAG_ROOM_STATE"]), pathlib.Path(r.env["FAKE_GUEST_HOME"])
        bot = lambda name: [b for b in r.state()["bots"] if b["id"] == name][0]
        resets = lambda: (ctl / "resets").read_text().split() if (ctl / "resets").exists() else []
        log = lambda name: json.loads((state / "wire" / (name + ".json")).read_text())["messages"]
        go = threading.Event()
        r.lena.reply = lambda body: (go.wait(20), "Zu spät.")[1] if "Erzähl" in json.dumps(body["messages"][-1], ensure_ascii=False) else "Frisch."
        r.noah.reply = lambda body: "PASS"
        noah_initial_actions = len(r.noah.slot_actions)
        (guests / "agent1").mkdir(parents=True, exist_ok=True)
        (guests / "agent1" / "notiz.txt").write_text("von gestern")
        # Lena is in the middle of an answer. Chris resets her: the turn ends at once, nothing waits for it.
        r.say("@Lena Erzähl etwas Langes.")
        r.wait(lambda: bot("lena")["busy"], "Lena answering")
        (ctl / "hold").touch()
        self.assertEqual(r.api("POST", "/api/vm/nobody/reset")[0], 404)
        self.assertEqual(r.api("POST", "/api/vm/lena/reset")[0], 200)
        self.assertEqual(r.api("POST", "/api/vm/lena/reset")[0], 200)      # asked twice: one reset
        r.wait(lambda: bot("lena").get("reset", {}).get("step") == "vm" and resets() == ["agent1"], "the VM is being made anew")
        go.set()
        self.assertEqual((bot("lena")["busy"], bot("lena")["vm"], (guests / "agent1").exists()), (False, "agent1", False))
        self.assertIn("Erzähl", json.dumps(log("lena"), ensure_ascii=False),
                      "old model context survives until VM replacement succeeds")
        self.assertEqual(r.api("POST", "/api/bot/lena/vm")[0], 409)        # no second VM beside it
        # The host's clean-up of unreferenced VMs is told that this one is spoken for.
        self.assertEqual([a.get("pending_vm") for a in json.loads((state / "agents.json").read_text())], ["agent1", None])
        # What Chris writes meanwhile waits for her; she takes no turn while she has no VM.
        asked, before = r.say("@Lena bist du da?"), len(r.lena.chats())
        time.sleep(0.6)
        self.assertEqual((len(r.lena.chats()), resets()), (before, ["agent1"]))
        (ctl / "hold").unlink()
        r.wait(lambda: "reset" not in bot("lena"), "reset done")
        self.assertIn("erase", [a for a, _ in r.lena.slot_actions])
        # A VM reset clears its "reset" marker just before notifying the
        # waiting worker. The room may look idle for a moment before that
        # worker claims the queued human turn. Wait for the *observable
        # answer*, not an unrelated instantaneous idle snapshot.
        reply_after_reset = lambda: [m["text"] for m in r.messages() if m["id"] > asked["id"]]
        r.wait(lambda: reply_after_reset() == ["Frisch."], "queued human reply after VM reset")
        r.idle()
        self.assertEqual(reply_after_reset(), ["Frisch."])
        # The fresh VM receives MAG's built-in, current identity context;
        # the bot's old notes and any other guest files must be gone.
        fresh_home = guests / "agent1"
        self.assertEqual((bot("lena")["vm"], bot("lena")["enabled"]), ("agent1", True))
        self.assertEqual(sorted(p.name for p in fresh_home.iterdir()), ["MAG_CONTEXT.md"])
        self.assertIn("Du bist Lena", (fresh_home / "MAG_CONTEXT.md").read_text())
        self.assertNotIn("Langes", json.dumps(log("lena"), ensure_ascii=False))   # she remembers nothing from before
        # Noah may erase an EMPTY slot while creating his first cold prefix at
        # startup. Only actions after the initial warmup count as a bot reset.
        self.assertNotIn("erase", [a for a, _ in r.noah.slot_actions[noah_initial_actions:]])  # Noah was not reset
        # The room is restarted in the middle of a reset (a deploy): it goes on, and a failure is said in words.
        (ctl / "hold").touch()
        self.assertEqual(r.api("POST", "/api/vm/lena/reset")[0], 200)
        r.wait(lambda: bot("lena").get("reset", {}).get("step") == "vm" and len(resets()) == 2, "second reset under way")
        r.stop()
        r.start()
        r.wait(lambda: len(resets()) == 3 and bot("lena").get("reset", {}).get("state") == "running", "taken up after the restart")
        self.assertEqual(bot("lena")["vm"], "agent1")                      # a VM that is gone for the moment stays hers
        (ctl / "fail").touch()
        (ctl / "hold").unlink()
        r.wait(lambda: bot("lena").get("reset", {}).get("state") == "failed", "failure shown")
        self.assertEqual((bot("lena")["reset"]["error"], bot("lena")["vm"]),
                         ("Die VM ließ sich nicht neu erstellen: magbot: Host load too high. Das Bot-Gedächtnis wurde nicht gelöscht.", "agent1"))
        (ctl / "fail").unlink()
        # Again, now with Lena paused and Noah (who never had a VM) at the same time.
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"enabled": False})[0], 200)
        self.assertEqual((r.api("POST", "/api/vm/lena/reset")[0], r.api("POST", "/api/vm/noah/reset")[0]), (200, 200))
        r.wait(lambda: "reset" not in bot("lena") and "reset" not in bot("noah"), "both reset")
        self.assertEqual((bot("lena")["vm"], bot("lena")["enabled"], bot("noah")["vm"], bot("noah")["enabled"]), ("agent1", False, "agent2", True))
        self.assertEqual((sorted(resets()[3:]), (guests / "agent1").is_dir(), (guests / "agent2").is_dir()), (["agent1", "agent2"], True, True))

    def test_vm_reset_forgets_old_browser_goal_and_error(self):
        ctl = pathlib.Path(tempfile.mkdtemp(prefix="magctl-"))
        self.addCleanup(shutil.rmtree, ctl, True)
        r = Room(MAG_ROOM_AGENT_CTL=str(HERE / "fake_agent_ctl"), FAKE_CTL=str(ctl))
        self.addCleanup(r.close)
        state = pathlib.Path(r.env["MAG_ROOM_STATE"])
        goal = state / "goals" / "lena.json"
        r.stop()
        goal.parent.mkdir(exist_ok=True)
        old = {"state": "blocked", "id": "old-browser-goal", "owner": "lena",
               "text": "OLD-BROWSER-TASK", "error": "OLD-BROWSER-500", "progress": "blocked"}
        goal.write_text(json.dumps(old))
        r.start()
        bot = lambda: next(b for b in r.state()["bots"] if b["id"] == "lena")
        r.wait(lambda: bot().get("goal", {}).get("error") == "OLD-BROWSER-500",
               "old goal loaded")
        self.assertEqual(r.api("POST", "/api/vm/lena/reset")[0], 200)
        r.wait(lambda: "reset" not in bot(), "VM reset completed")
        self.assertFalse(bot().get("goal", {}).get("state"))
        self.assertEqual(json.loads(goal.read_text()), {})
        r.stop()
        r.start()
        r.idle()
        self.assertFalse(bot().get("goal", {}).get("state"))

    def test_upgrade_discards_stale_ended_goal_in_fresh_context(self):
        r = Room()
        self.addCleanup(r.close)
        r.idle()
        r.stop()
        state = pathlib.Path(r.env["MAG_ROOM_STATE"])
        goal = state / "goals" / "lena.json"
        goal.parent.mkdir(exist_ok=True)
        goal.write_text(json.dumps({"state": "ended", "id": "old", "text": "OLD",
                                    "error": "OLD-500"}))
        wire = state / "wire" / "lena.json"
        saved = json.loads(wire.read_text())
        saved["messages"] = saved["messages"][:1]
        saved.pop("goal_prompt_stamp", None)
        wire.write_text(json.dumps(saved))
        r.start()
        r.wait(lambda: json.loads(goal.read_text()) == {}, "stale goal migrated")
        self.assertFalse(next(b for b in r.state()["bots"] if b["id"] == "lena")
                         .get("goal", {}).get("state"))

    def test_disk_reserve_rejects_reset_without_erasing_bot_memory(self):
        ctl = pathlib.Path(tempfile.mkdtemp(prefix="mag-reset-reserve-"))
        self.addCleanup(shutil.rmtree, ctl, True)
        r = Room(MAG_ROOM_AGENT_CTL=str(HERE / "fake_agent_ctl"), FAKE_CTL=str(ctl))
        self.addCleanup(r.close)
        script = iter([
            {"content": "", "tool_calls": [{"name": "remember", "arguments": {"fact": "Lena merkt: grünes Buch."}}]},
            "Gemerkt."
        ])
        r.lena.reply = lambda body: next(script, "Verstanden.")
        r.noah.reply = lambda body: "PASS"
        r.say("@Lena merk dir das grüne Buch")
        r.idle()
        mem = lambda who: r.api("GET", "/api/bot/" + who + "/memory")[1]["memory"]
        self.assertEqual([x["text"] for x in mem("lena")], ["Lena merkt: grünes Buch."])
        guests = pathlib.Path(r.env["FAKE_GUEST_HOME"])
        vm_dir = guests / "agent1"
        vm_dir.mkdir(parents=True, exist_ok=True)
        (vm_dir / "original.txt").write_text("nicht gelöscht")
        before_wire = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "lena.json").read_text())
        before_noah = mem("noah")
        erase_before = [a for a, _ in r.lena.slot_actions].count("erase")
        (ctl / "fail_disk").touch()
        self.assertEqual(r.api("POST", "/api/vm/lena/reset")[0], 200)
        r.wait(lambda: any(b["id"] == "lena" and b.get("reset", {}).get("state") == "failed"
                           for b in r.state()["bots"]), "disk reserve reset declined")
        self.assertTrue((vm_dir / "original.txt").exists(), "preflight must keep the existing VM")
        self.assertEqual([x["text"] for x in mem("lena")], ["Lena merkt: grünes Buch."])
        self.assertEqual(mem("noah"), before_noah, "other bot never affected")
        after_wire = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "lena.json").read_text())
        self.assertEqual(after_wire["messages"], before_wire["messages"],
                         "refused reset must not erase the prompt conversation")
        self.assertEqual([a for a, _ in r.lena.slot_actions].count("erase"), erase_before)
        lena = next(b for b in r.state()["bots"] if b["id"] == "lena")
        self.assertEqual(lena["phase"], "idle", "failed reset must not stick on 'liest'")
        self.assertIn("Host disk reserve", lena["reset"]["error"])
        self.assertIn("Gedächtnis wurde nicht gelöscht", lena["reset"]["error"])

    def test_an_answer_a_restart_cut_off_unseen_is_given_after_it(self):
        r = self.room
        state = pathlib.Path(r.env["MAG_ROOM_STATE"])
        calls, go = [], threading.Event()

        def thinking(body):
            calls.append(len(body["messages"]))
            if len(calls) in (1, 3, 5):
                go.wait(20)                  # still thinking when the room goes down
                return "Zu spät."            # nobody reads this: the room that asked is gone
            return "Da bin ich wieder."
        r.lena.reply = thinking
        asked = r.say("@Lena bist du da?")
        r.wait(lambda: len(calls) == 1, "Lena thinking")
        self.assertEqual(json.loads((state / "wire" / "lena.json").read_text())["at_work"]["reason"], "human")
        # A deploy restarts the room while she thinks. Nothing of her answer was to be seen: she answers after it.
        r.stop()
        go.set()
        r.start()
        r.idle(30)
        self.assertEqual([m["text"] for m in r.messages() if m["id"] > asked["id"]], ["Da bin ich wieder."])
        self.assertEqual((calls[1], len(calls)), (calls[0], 2))               # the same question again, once
        self.assertEqual(r.noah.chats(), [])                                    # Chris had asked Lena alone; that still holds
        self.assertNotIn("at_work", json.loads((state / "wire" / "lena.json").read_text()))
        # The same when the room dies without a goodbye.
        go.clear()
        r.say("@Lena und jetzt?")
        r.wait(lambda: len(calls) == 3, "Lena thinking again")
        r.proc.kill()
        r.stop()
        go.set()
        r.start()
        r.idle(30)
        self.assertEqual(([m["text"] for m in r.messages() if m["from"] == "lena"], len(calls)), (["Da bin ich wieder."] * 2, 4))
        # Stopped by Chris is stopped: a later restart does not bring that answer back.
        go.clear()
        r.say("@Lena noch etwas")
        r.wait(lambda: len(calls) == 5, "Lena thinking a third time")
        r.api("POST", "/api/stop", {"bot": "lena"})
        go.set()
        r.idle()
        r.stop()
        r.start()
        r.idle()
        self.assertEqual((len([m for m in r.messages() if m["from"] == "lena"]), len(calls)), (2, 5))
        # An answer that was cut off in view stays as it is, marked as stopped; it is not written a second time.
        r.lena.reply = lambda body: calls.append(0) or "Ein langer Satz, der Stück für Stück erscheint und nie fertig wird. " * 20
        r.lena.delay = 0.05
        r.say("@Lena erzähl")
        r.wait(lambda: [m for m in r.messages() if m["from"] == "lena" and m["state"] == "streaming" and m["text"] == "" and m["parts"]], "an answer in view")
        r.stop()
        r.lena.delay = 0
        r.start()
        r.idle()
        last = [m for m in r.messages() if m["from"] == "lena"][-1]
        self.assertEqual((last["state"], len(calls)), ("stopped", 6))

    def test_the_phone_tools_exist_only_with_its_control_service(self):
        # No control daemon set up (as on the host until Chris starts it): the bots have no phone tools.
        self.room.say("Hi beide")
        self.room.idle()
        self.assertFalse([t for t in self.room.lena.chats()[-1]["tools"] if t["function"]["name"].startswith("s23_")])
        # With the daemon (the real one, on a scripted adb): its token is read from the file it was given in.
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="s23-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        token, port = "phone-token-0123456789", free_port()
        (tmp / "token").write_text(token + "\n")
        (tmp / "adb").write_text("#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$FAKE_ADB_LOG\"\ncase \"$*\" in\n  *screencap*) printf 'PNGDATA' ;;\n"
                                 "  *get-state*) echo device ;;\n  *wm\\ size*) echo 'Physical size: 1080x2340' ;;\nesac\n")
        (tmp / "adb").chmod(0o755)
        daemon = subprocess.Popen([str(BIN.with_name("s23ctl"))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  env=dict(os.environ, MAG_S23_TOKEN=token, MAG_S23_ADB=str(tmp / "adb"), MAG_S23_SERIAL="R5CTEST", MAG_S23_PORT=str(port),
                                           MAG_S23_BIND="127.0.0.1", FAKE_ADB_LOG=str(tmp / "adb.log")))
        self.addCleanup(daemon.kill)
        r = Room(MAG_S23_PORT=port, MAG_S23_TOKEN_FILE=tmp / "token")
        self.addCleanup(r.close)

        def use(*calls):
            script = iter([{"content": "", "tool_calls": [{"name": name, "arguments": args}]} for name, args in calls] + ["Erledigt."])
            r.noah.reply = lambda body: next(script)
            r.say("@Noah bitte")
            r.idle()
            return [(p["title"], p["state"], p["output"]) for m in r.messages() if m["from"] == "noah" for p in m["parts"] if p["k"] == "step"][-len(calls):]
        steps = use(("s23_status", {}), ("s23_tap", {"x": 540, "y": 1200}), ("s23_swipe", {"x1": 500, "y1": 1600, "x2": 500, "y2": 400}),
                    ("s23_text", {"text": "Hallo Welt"}), ("s23_key", {"key": "KEYCODE_HOME"}), ("s23_screenshot", {}))
        self.assertEqual([(title, state) for title, state, _ in steps],
                         [("S23: Zustand", "ok"), ("S23: tippt auf 540, 1200", "ok"), ("S23: wischt", "ok"), ("S23: schreibt Hallo Welt", "ok"),
                          ("S23: Taste KEYCODE_HOME", "ok"), ("S23: Bildschirmfoto", "ok")])
        self.assertIn("1080x2340", steps[0][2])
        self.assertEqual(steps[5][2], "Bildschirmfoto aufgenommen (7 Bytes). Du siehst es nicht.")
        did = (tmp / "adb.log").read_text()
        for what in ("input tap 540 1200", "input swipe 500 1600 500 400 120", "input text Hallo%sWelt", "input keyevent KEYCODE_HOME", "screencap -p"):
            self.assertIn(what, did)
        # Every bot has the tools, also one without a VM; the token reaches no prompt.
        asked = r.noah.chats()[-1]
        self.assertEqual([t["function"]["name"] for t in asked["tools"] if t["function"]["name"].startswith("s23_")],
                         ["s23_status", "s23_tap", "s23_swipe", "s23_text", "s23_key", "s23_screenshot"])
        self.assertNotIn(token, json.dumps(asked))
        # The daemon is gone: the bot is told so, in words.
        daemon.kill()
        daemon.wait()
        self.assertEqual(use(("s23_tap", {"x": 1, "y": 2})), [("S23: tippt auf 1, 2", "error", "Fehler: Die S23-Steuerung ist nicht erreichbar. Chris startet sie selbst.")])

    def test_an_object_named_in_a_photo_is_found_by_the_bot_that_sees(self):
        r = Room(MAG_ROOM_VISION="lena", MAG_ROOM_SIGHT_WAIT_S="30")
        self.addCleanup(r.close)
        r.lena.reply = lambda body: "250, 500" if "Wo ist \"Waschbecken\"?" in text_of(body["messages"][-1]["content"]) else "nein"
        photo = r.api("POST", "/api/upload?name=bad.png", raw=PNG)[1]
        before = len(r.lena.chats())
        code, found = r.api("POST", "/api/3d/select", {"file": photo["file"], "name": "Waschbecken"})
        self.assertEqual((code, len(found["masks"]), r.idol.selected), (200, 1, ["x=0.250000&y=0.500000"]))
        # Lena was asked with the image itself in front of the question, off her log: nothing of it stays.
        asked = r.lena.chats()[before:]
        self.assertEqual(len(asked), 1)
        parts = asked[0]["messages"][-1]["content"]
        self.assertEqual([part["type"] for part in parts], ["image_url", "text"])
        r.idle()
        self.assertNotIn("Waschbecken", json.dumps(r.lena.chats()[-1]["messages"][:-1]))
        self.assertEqual([m["from"] for m in r.messages()], [])
        # What is not in the photo is not guessed, and the worker is not asked.
        self.assertEqual(r.api("POST", "/api/3d/select", {"file": photo["file"], "name": "Elefant"}), (409, {"error": "»Elefant« ist im Foto nicht zu finden. Tippe es an."}))
        self.assertEqual(len(r.idol.selected), 1)

    def test_bots_read_the_whole_chat_images_files_and_3d_jobs(self):
        # Lena's model takes images, Noah's does not.
        r = Room(MAG_ROOM_VISION="lena", MAG_ROOM_SIGHT_WAIT_S="30")
        self.addCleanup(r.close)

        def lena(body):
            text = text_of(body["messages"][-1]["content"])
            if "Beschreibe das Bild" in text:
                return "Eine rote Tasse auf einem Holztisch."
            return "PASS" if "Sonst antworte exakt PASS" in text else "Ich sehe die Tasse."
        r.lena.reply = lena
        png = PNG
        photo = r.api("POST", "/api/upload?name=tasse.png", raw=png)[1]
        note = r.api("POST", "/api/upload?name=notiz.txt", raw="Einkauf: Milch und Brot".encode())[1]
        asked = r.say("Was ist auf dem Bild?", attachments=[photo["file"], note["file"]])
        r.idle()
        # Lena gets the image itself, right where the message names it, and wrote down once what it shows ...
        seen = [c["messages"][-1]["content"] for c in r.lena.chats() if isinstance(c["messages"][-1]["content"], list)]
        self.assertTrue(seen and all((placed(parts, "tasse.png") or "").startswith("data:image/") for parts in seen), seen)
        self.assertTrue(all(parts[0]["type"] == "text" and parts[0]["text"].endswith("[Sprecher Chris] Was ist auf dem Bild?\n") for parts in seen), seen)
        described = [parts for parts in seen if "Beschreibe das Bild tasse.png" in text_of(parts)]
        self.assertEqual(len(described), 1)
        # ... asked with the picture itself right in front of the question, not by its name alone.
        self.assertEqual((described[0][-2]["type"], described[0][-2]["image_url"]["url"]), ("image_url", placed(described[0], "tasse.png")))
        self.assertIn("Beschreibe das Bild tasse.png, das direkt vor dieser Notiz steht, und nur dieses", described[0][-1]["text"])
        answered = [parts for parts in seen if "Beschreibe das Bild" not in text_of(parts)]
        self.assertIn("[Sprecher Chris] Was ist auf dem Bild?", text_of(answered[0]))
        self.assertTrue(text_of(described[0]).startswith(text_of(answered[0])))  # same prefix: only a short branch is evaluated
        # ... which is what Noah, who has no eyes, reads; the small text file is simply part of the chat.
        noah = r.noah.chats()[0]["messages"][-1]["content"]
        self.assertIsInstance(noah, str)
        self.assertIn("[Bild tasse.png (image/png, 1 KB). Zu sehen: Eine rote Tasse auf einem Holztisch.]", noah)
        self.assertIn("Einkauf: Milch und Brot", noah)
        stored = [m for m in r.messages() if m["id"] == asked["id"]][0]["attachments"][0]
        self.assertEqual(stored["description"], "Eine rote Tasse auf einem Holztisch.")
        self.assertEqual(r.texts("lena"), ["Ich sehe die Tasse."])   # the description is not an answer in the chat
        # A 3D job is context as well: the order with its photo and how it ended. It asks the bots nothing.
        before = len(r.lena.chats()) + len(r.noah.chats())
        photo = r.api("POST", "/api/upload?name=figur.png", raw=png)[1]
        out = r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "fast"})[1]
        r.wait(lambda: [m for m in r.messages() if m["id"] == out["model3d"]][0]["state"] == "done", "3D job done")
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), before)
        r.say("@Noah was ist seitdem passiert?")
        r.idle()
        told = [c["messages"][-1]["content"] for c in r.noah.chats() if "seitdem passiert" in text_of(c["messages"][-1]["content"])][0]
        self.assertIn("Auftrag an Foto zu 3D", told)
        self.assertIn("[Bild figur.png (image/png, 1 KB). Zu sehen: Eine rote Tasse auf einem Holztisch.]", told)
        self.assertIn("[Sprecher Foto zu 3D] 3D-Modell aus dem Foto figur.png (Qualität schnell) ist fertig", told)
        # A picture painted from words is an ordinary image for them, with the words it was painted from.
        # It asks the bots nothing, the same as the 3D order above.
        before = len(r.lena.chats()) + len(r.noah.chats())
        images = len(r.idol.images)
        out = r.api("POST", "/api/message", {"text": "ein Fuchs im Schnee", "mode": "image"})[1]
        r.wait(lambda: [m for m in r.messages() if m["id"] == out["model3d"]][0]["state"] == "done", "picture made")
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), before)
        self.assertEqual(len(r.idol.images), images + 1)
        r.say("@Noah und jetzt?")
        r.idle()
        chat = [c["messages"] for c in r.noah.chats() if "und jetzt?" in text_of(c["messages"][-1]["content"])][0]
        told = "\n".join(text_of(m["content"]) for m in chat)
        self.assertIn("Auftrag an Bild erzeugen, kein Auftrag an die Bots", told)
        self.assertIn('Bild erzeugen: Bild, gemalt aus den Worten "ein Fuchs im Schnee" (FLUX.2 klein):\n[Bild bild-', told)
        self.assertNotIn("3D-Modell aus dem Foto  ", told)
        # A video ordered from words asks the bots nothing either.
        before = len(r.lena.chats()) + len(r.noah.chats())
        r.api("POST", "/api/message", {"text": "der Fuchs läuft weiter", "mode": "video"})
        time.sleep(0.5)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), before)
        # A bot that thinks before every answer answers first and describes afterwards
        # (describing would cost it a whole thinking pass per image up front).
        self.assertEqual(r.api("PUT", "/api/bot/lena", {"thinking": True})[0], 200)
        photo = r.api("POST", "/api/upload?name=blume.png", raw=picture(220, 30, 90))[1]   # a picture nobody has described yet
        mark = len(r.lena.chats())
        r.say("@Lena und das hier?", attachments=[photo["file"]])
        r.idle()
        order = ["describe" if "Beschreibe das Bild blume.png" in text_of(c["messages"][-1]["content"]) else "answer"
                 for c in r.lena.chats()[mark:]]
        self.assertEqual(order[:2], ["answer", "describe"], order)

    def test_a_picture_is_seen_where_it_is_said_and_never_mixed_up(self):
        # What went wrong live: a new picture came in a bundle with other
        # messages, stood at the head of that bundle, far from the question
        # about it, and was then asked about by its name alone. The bot
        # described an older picture, and the bot without eyes repeated it.
        r = Room(MAG_ROOM_VISION="lena", MAG_ROOM_SIGHT_WAIT_S="30")
        self.addCleanup(r.close)
        asked_about = []

        def lena(body):
            parts = body["messages"][-1]["content"]
            if isinstance(parts, list) and "Beschreibe das Bild" in parts[-1].get("text", ""):
                name = parts[-1]["text"].split("Beschreibe das Bild ")[1].split(",")[0]
                asked_about.append(name)
                # Where the conversation shows the picture of that name, and what stands in front of the question.
                shown = [placed(m["content"], name) for m in body["messages"] if isinstance(m["content"], list)]
                given = parts[-2]["image_url"]["url"] if parts[-2]["type"] == "image_url" else None
                return "Bild mit dem Inhalt %08x." % zlib.crc32(given.encode()) if given and given in shown else "FALSCHES BILD"
            return "PASS" if "Sonst antworte exakt PASS" in text_of(parts) else "Gesehen."
        r.lena.reply = lena

        def image(message_id, index=0):
            return [m for m in r.messages() if m["id"] == message_id][0]["attachments"][index]
        robot = r.api("POST", "/api/upload?name=roboter.png", raw=picture(250, 120, 0))[1]
        first = r.say("@Lena schau mal", attachments=[robot["file"]])
        r.idle()
        r.wait(lambda: image(first["id"]).get("description"), "first picture described")
        # Lena reads the next two messages only as context; the picture arrives in one bundle with them.
        r.say("@Noah wann kommt die Lieferung?")
        r.idle()
        gnome = r.api("POST", "/api/upload?name=Screenshot.png", raw=picture(20, 40, 160))[1]
        second = r.say("Was seht ihr da", attachments=[gnome["file"]])
        r.idle()
        r.wait(lambda: image(second["id"]).get("description"), "second picture described")
        bundle = [c["messages"][-1]["content"] for c in r.lena.chats() if isinstance(c["messages"][-1]["content"], list)
                  and "Was seht ihr da" in text_of(c["messages"][-1]["content"])][0]
        self.assertIn("[Sprecher Chris an Noah] @Noah wann kommt die Lieferung?", bundle[0]["text"])     # other people's words first ...
        self.assertTrue(bundle[0]["text"].endswith("[Sprecher Chris] Was seht ihr da\n"))                 # ... then the question, the picture,
        self.assertEqual(bundle[1]["type"], "image_url")                                        # and its own line
        self.assertTrue(bundle[2]["text"].startswith("[Bild Screenshot.png ("))
        self.assertNotIn("FALSCH", image(first["id"])["description"] + image(second["id"])["description"])
        self.assertNotEqual(image(first["id"])["description"], image(second["id"])["description"])
        # Noah, who has no eyes, reads what this very picture shows.
        noah = [text_of(c["messages"][-1]["content"]) for c in r.noah.chats() if "Was seht ihr da" in text_of(c["messages"][-1]["content"])][0]
        self.assertIn("[Bild Screenshot.png (image/png, 1 KB). Zu sehen: " + image(second["id"])["description"] + "]", noah)
        # Two pictures in one message: each stands in front of its own line.
        a = r.api("POST", "/api/upload?name=a.png", raw=picture(200, 0, 0))[1]
        b = r.api("POST", "/api/upload?name=b.png", raw=picture(0, 200, 0))[1]
        pair = r.say("@Lena und diese zwei?", attachments=[a["file"], b["file"]])
        r.idle()
        r.wait(lambda: image(pair["id"], 0).get("description") and image(pair["id"], 1).get("description"), "both pictures described")
        both = [c["messages"][-1]["content"] for c in r.lena.chats() if isinstance(c["messages"][-1]["content"], list)
                and "und diese zwei?" in text_of(c["messages"][-1]["content"])][0]
        self.assertTrue(placed(both, "a.png") and placed(both, "b.png") and placed(both, "a.png") != placed(both, "b.png"))
        self.assertNotEqual(image(pair["id"], 0)["description"], image(pair["id"], 1)["description"])
        # The same picture posted again under another name is the same picture: nobody is asked twice.
        calls = len(asked_about)
        copy = r.api("POST", "/api/upload?name=nochmal.png", raw=picture(20, 40, 160))[1]
        third = r.say("@Lena und das?", attachments=[copy["file"]])
        r.idle()
        r.wait(lambda: image(third["id"]).get("description"), "copy described")
        self.assertEqual((image(third["id"])["description"], len(asked_about)), (image(second["id"])["description"], calls))
        # A description that is wrong can be made anew: for every copy of the picture, by looking at it again ...
        r.lena.reply = lambda body: "Ein Wichtel mit Laterne im Schnee." if "Beschreibe das Bild" in text_of(body["messages"][-1]["content"]) else "PASS"
        self.assertEqual(r.api("POST", "/api/file/" + gnome["file"] + "/describe")[0], 200)
        r.wait(lambda: image(second["id"]).get("description") == "Ein Wichtel mit Laterne im Schnee."
               and image(third["id"]).get("description") == "Ein Wichtel mit Laterne im Schnee.", "described anew")
        self.assertNotEqual(image(first["id"])["description"], "Ein Wichtel mit Laterne im Schnee.")   # other pictures stay as they were
        # ... and the bot that only read about it is told.
        r.noah.reply = None
        r.say("@Noah und was war auf dem Screenshot?")
        r.idle()
        told = text_of(r.noah.chats()[-1]["messages"][-1]["content"])
        self.assertIn("(Nachtrag zum Bild Screenshot.png. Zu sehen: Ein Wichtel mit Laterne im Schnee.)", told)
        self.assertEqual(r.api("POST", "/api/file/gibt-es-nicht.png/describe")[0], 404)

    def test_a_picture_that_ends_in_its_middle_is_not_taken_as_it_is(self):
        r = self.room
        cut = JPEG[:len(JPEG) // 2]
        # Whole pictures pass, also a JPEG with data after its end mark (phones append their own).
        for name, data in (("ganz.jpg", JPEG), ("anhang.jpg", JPEG + b"\x00\x01\x00\x00"), ("ganz.png", PNG), ("text.txt", b"kein Bild")):
            code, out = r.api("POST", "/api/upload?name=" + name, raw=data)
            self.assertEqual((code, out.get("cut")), (201, None), name)
        # One that ends in its picture data does not: the page reads the file again ...
        for name, data in (("halb.jpg", cut), ("kopf.jpg", JPEG[:12]), ("halb.png", PNG[:40])):
            code, out = r.api("POST", "/api/upload?name=" + name, raw=data)
            self.assertEqual((code, out.get("code")), (422, "truncated"), name)
            self.assertIn("endet mitten im Bild", out["error"])
        # ... and only when it insists is the file taken, marked as what it is.
        code, out = r.api("POST", "/api/upload?name=halb.jpg&force=1", raw=cut)
        self.assertEqual((code, out["cut"], out["size"]), (201, True, len(cut)))

    def test_bots_read_what_a_document_says(self):
        """A Word file and a PDF are read for the bots: their text is part of
        the message, not just their name. (The PDF part needs pdftotext.)"""
        import io
        import zipfile
        r = self.room

        def upload(name, data):
            code, att = r.api("POST", "/api/upload?name=" + name, raw=data)
            self.assertEqual(code, 201, att)
            return att["file"]

        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Angebot Nr. 4711</w:t></w:r></w:p>"
                       "<w:p><w:r><w:t>Summe: 1.234,56 &amp; Skonto</w:t></w:r></w:p></w:body></w:document>")
        files = [upload("angebot.docx", docx.getvalue())]
        if shutil.which("pdftotext"):
            stream = b"BT /F1 12 Tf 72 720 Td (Der Liefertermin ist der 12. Dezember.) Tj ET"
            objects = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
                       b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
                       b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream", b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
            pdf, offsets = b"%PDF-1.4\n", []
            for i, body in enumerate(objects, 1):
                offsets.append(len(pdf))
                pdf += b"%d 0 obj" % i + body + b"endobj\n"
            xref = len(pdf)
            pdf += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
            pdf += b"trailer<</Root 1 0 R/Size 6>>\nstartxref\n%d\n%%%%EOF\n" % xref
            files.append(upload("brief.pdf", pdf))
        r.say("@Noah was steht in den Dateien?", attachments=files)
        r.idle()
        sent = json.dumps(r.noah.chats()[0]["messages"], ensure_ascii=False)
        self.assertIn("[Dokument angebot.docx", sent)
        self.assertIn("Angebot Nr. 4711\\nSumme: 1.234,56 & Skonto", sent)
        if len(files) > 1:
            self.assertIn("Der Liefertermin ist der 12. Dezember.", sent)
        # Deleting the message takes the extracted text along with the file.
        texts = r.tmp / "state" / "text"
        self.assertEqual(len(list(texts.iterdir())), len(files))
        self.assertEqual(r.api("DELETE", "/api/message/" + r.messages()[0]["id"])[0], 200)
        self.assertEqual(list(texts.iterdir()), [])

    def test_deleting_a_message_removes_it_for_the_bots_too(self):
        r = self.room
        secret = r.say("@Lena merk dir Kennwort Zitrone")
        r.idle()
        answer = [m for m in r.messages() if m["from"] == "lena"][0]
        self.assertEqual(r.api("DELETE", "/api/message/" + secret["id"])[0], 200)
        self.assertEqual(r.api("DELETE", "/api/message/" + answer["id"])[0], 200)
        before = len(r.lena.chats())
        r.say("@Lena und jetzt?")
        r.idle()
        sent = json.dumps(r.lena.chats()[before]["messages"], ensure_ascii=False)
        self.assertNotIn("Zitrone", sent)
        self.assertNotIn("Hallo von Lena.", sent)

    def test_mistral_small4_chat_roles_and_effort_without_touching_gemma(self):
        r = self.room
        # A live modeld status identifies the current model; bot IDs are NOT
        # model families. MAG's persisted append-only wire is left untouched.
        status = {"updated": time.time(), "bots": {
            "lena": {"state": "ready", "file": "gemma-4-26B", "since": 1},
            "noah": {"state": "ready", "file": "Mistral-Small-4-119B-2603-UD-Q4_K_M-00001-of-00003", "since": 1},
        }}
        pathlib.Path(r.env["MAG_MODELS_STATUS"]).write_text(json.dumps(status))

        def strict_mistral(body):
            roles = [msg["role"] for msg in body["messages"] if msg["role"] != "system"]
            if any(a == b == "user" for a, b in zip(roles, roles[1:])):
                return {"http_error": "Mistral template: user roles must alternate"}
            return "Mistral OK"

        r.noah.reply = strict_mistral
        r.say("@Noah erster Test")
        r.idle()
        req = r.noah.chats()[-1]
        self.assertEqual(req["chat_template_kwargs"]["reasoning_effort"], "none")
        self.assertEqual([m["role"] for m in req["messages"]][:2], ["system", "user"])
        self.assertTrue(all(not (a["role"] == b["role"] == "user")
                            for a, b in zip(req["messages"], req["messages"][1:])))
        self.assertIn("Mistral OK", [m.get("text", "") for m in r.messages() if m["from"] == "noah"])

        # Changing the model family must not alter Blake/Lena's Gemma requests.
        r.say("@Lena kontrolliere Gemma")
        r.idle()
        gemma = r.lena.chats()[-1]
        self.assertNotIn("reasoning_effort", gemma["chat_template_kwargs"])

        # A second message also has alternating roles, including the private
        # orientation cue that the original MAG wire intentionally keeps.
        r.say("@Noah zweiter Test")
        r.idle()
        req2 = r.noah.chats()[-1]
        self.assertTrue(all(not (a["role"] == b["role"] == "user")
                            for a, b in zip(req2["messages"], req2["messages"][1:])))

    def test_mistral_autonomous_tool_results_and_user_cues_alternate(self):
        r = Room(connect_events=False, MAG_ROOM_AUTO_CHAT_INTERVAL_S="1200")
        self.addCleanup(r.close)
        status = {"updated": time.time(), "bots": {
            "lena": {"state": "ready", "file": "gemma-4-26B", "since": 1},
            "noah": {"state": "ready", "file": "mistral-small-4-119b-2603.gguf", "since": 1},
        }}
        pathlib.Path(r.env["MAG_MODELS_STATUS"]).write_text(json.dumps(status))
        r.lena.reply = lambda body: "Ich würde gerne über etwas Neues sprechen."
        attempts = []

        def strict_mistral(body):
            # Match the actual Mistral template: ignore tool calls and results
            # before checking the remaining user/plain-assistant alternation.
            roles = [m["role"] for m in body["messages"] if m["role"] == "user" or
                     (m["role"] == "assistant" and not m.get("tool_calls"))]
            if roles != ["user" if i % 2 == 0 else "assistant" for i in range(len(roles))]:
                return {"http_error": "After the optional system message, conversation roles must alternate user and assistant roles except for tool calls and results."}
            attempts.append(body)
            if len(attempts) == 1:
                return {"content": "", "tool_calls": [{"name": "will_set", "arguments": {
                    "text": "Ein eigenes Thema", "done_when": "Eine neue Idee"}}]}
            return "Meine Idee ist ein gemeinsames Gedankenexperiment."

        r.noah.reply = strict_mistral
        self.assertEqual(r.api("PUT", "/api/settings", {
            "auto_chat": True, "bot_rounds": 1, "pass_enabled": False, "web": False})[0], 200)
        r.wait(lambda: any(m.get("from") == "noah" and m.get("state") == "done"
                           for m in r.messages()), "Mistral completes autonomous native tool follow-up", 35)
        r.idle()
        self.assertEqual(r.api("PUT", "/api/settings", {"auto_chat": False})[0], 200)
        self.assertGreaterEqual(len(attempts), 2)
        messages = attempts[1]["messages"]
        call_index = next(i for i, m in enumerate(messages) if m.get("tool_calls"))
        result_index = next(i for i, m in enumerate(messages) if m["role"] == "tool")
        self.assertLess(call_index, result_index)
        self.assertEqual(messages[call_index]["tool_calls"][0]["id"],
                         messages[result_index]["tool_call_id"])
        self.assertIn("Technischer Nachrichtenübergang", json.dumps(messages, ensure_ascii=False))
        wire = (r.tmp / "state" / "wire" / "noah.json").read_text()
        self.assertNotIn("Technischer Nachrichtenübergang", wire)

    def test_state_survives_a_restart(self):
        r = self.room
        r.say("@Noah hallo")
        r.idle()
        r.stop()
        r.start()
        self.assertEqual([m["from"] for m in r.messages()], ["chris", "noah"])
        before = len(r.noah.chats())
        r.say("@Noah weiter")
        r.idle()
        roles = [m["role"] for m in r.noah.chats()[before]["messages"]
                 if "PRIVATE SELBSTORIENTIERUNG" not in str(m.get("content", ""))]
        self.assertEqual(roles[:4], ["system", "user", "assistant", "user"])  # the prompt log is append-only

    def test_dictation_shows_text_while_it_is_spoken(self):
        """The page sends audio chunk by chunk; every answer carries the text
        so far, a sentence followed by a pause is final, and nothing is heard
        twice. mag-stt runs its real protocol here on its fake engine, whose
        "speech" spells its own transcript (see mag_stt.cpp)."""
        daemon = ROOT / "build/mag-stt"
        if not daemon.exists():
            self.skipTest("mag-stt is not built")
        port = free_port()
        stt = subprocess.Popen([str(daemon)], env=dict(os.environ, MAG_STT_FAKE="1", MAG_STT_PORT=str(port)), stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (stt.terminate(), stt.wait(5)))
        r = Room(MAG_STT_URL="http://127.0.0.1:%d" % port)
        self.addCleanup(r.close)
        r.wait(lambda: r.state()["features"]["stt"], "speech service")

        def slot(word):  # 400 ms at 16 kHz: a level of word * 100, or silence
            return (word * 100).to_bytes(2, "little", signed=True) * 6400

        def chunk(sid, seq, data, end=False):
            code, out = r.api("POST", "/api/stt/live?sid=%s&seq=%d%s" % (sid, seq, "&end=1" if end else ""), raw=data)
            self.assertEqual(code, 200, out)
            return out

        audio = slot(1) + slot(2) + slot(3) + slot(0) * 5 + slot(4) + slot(5)
        sid, seen = "a1b2c3d4e5f60718", []
        pieces = [audio[i:i + 6400] for i in range(0, len(audio), 6400)]  # 200 ms each
        for seq, data in enumerate(pieces):
            seen.append(chunk(sid, seq, data))
            if seq == 3:  # an answer got lost and the page asks again: not heard twice
                seen.append(chunk(sid, seq, data))
        self.assertTrue(any(o["partial"].startswith("w1") for o in seen), seen)  # text while it is spoken
        self.assertTrue(any(o["committed"] == "w1 w2 w3." for o in seen), seen)  # the sentence before the pause is final
        final = chunk(sid, len(pieces), b"", end=True)
        self.assertEqual((final["committed"], final["partial"], final["final"]), ("w1 w2 w3. w4 w5", "", True))
        # The dictation is over; one that the service does not know is refused, not invented.
        self.assertEqual(r.api("POST", "/api/stt/live?sid=%s&seq=7" % sid, raw=b"\0\0")[0], 410)
        self.assertEqual(r.api("POST", "/api/stt/live?sid=x&seq=0", raw=b"\0\0")[0], 400)
        if shutil.which("ffmpeg"):  # a finished recording in a container format
            wav = b"RIFF" + (36 + len(audio)).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + \
                (1).to_bytes(2, "little") + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + \
                (16).to_bytes(2, "little") + b"data" + len(audio).to_bytes(4, "little") + audio
            code, out = r.api("POST", "/api/stt", raw=wav)
            self.assertEqual((code, out.get("text")), (200, "w1 w2 w3. w4 w5"), out)

    def test_pending_browser_goal_and_correction_survive_room_restart(self):
        r = Room()
        self.addCleanup(r.close)
        r.lena.reply = lambda body: "Ich scrolle nach unten."
        r.noah.reply = lambda body: "PASS"
        r.stop()
        pending_path = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json.pending"
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(json.dumps({
            "kind": "begin", "id": "queued-from-before-restart",
            "text": "@Lena Suche 2 Schrumpfschläuche auf Amazon.de für 18650",
            "corrections": ["transparent, 2S nebeneinander"]}))
        r.start()
        queued = next(b for b in r.state()["bots"] if b["id"] == "lena")
        self.assertEqual(queued["goal"]["state"], "pending")
        before = len(r.lena.chats())
        r.say("Und?")
        r.idle()
        calls = r.lena.chats()[before:]
        self.assertTrue(calls)
        self.assertTrue(all(c.get("tool_choice") == "required" for c in calls))
        goal = json.loads((pending_path.parent / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        self.assertIn("transparent, 2S", goal["text"])
        self.assertEqual(len(goal["corrections"]), 1)
        self.assertFalse(pending_path.exists())
        # The next room restart must not reapply the same correction.
        r.stop()
        r.start()
        goal = json.loads((pending_path.parent / "lena.json").read_text())
        self.assertEqual(len(goal["corrections"]), 1)

    def test_ended_goal_never_replays_a_queued_begin(self):
        r = Room()
        self.addCleanup(r.close)
        r.stop()
        goals = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals"
        goals.mkdir(parents=True, exist_ok=True)
        (goals / "lena.json.pending").write_text(json.dumps({
            "kind": "begin", "id": "stopped-start", "text": "@Lena Suche Amazon.de",
            "corrections": []}))
        (goals / "lena.json.end").write_text("end\n")
        (goals / "lena.json").write_text(json.dumps({
            "id": "previous-goal", "owner": "lena", "state": "active",
            "text": "@Lena Suche Amazon.de", "corrections": []}))
        r.start()
        r.lena.reply = lambda body: "Hallo."
        r.say("@Lena hallo")
        r.idle()
        self.assertFalse((goals / "lena.json.pending").exists())
        self.assertEqual(json.loads((goals / "lena.json").read_text())["state"], "ended")
        self.assertTrue((goals / "lena.json.end").exists())
        self.assertNotEqual(next(b for b in r.state()["bots"] if b["id"] == "lena").get("goal", {}).get("state"), "active")
        self.assertNotIn("required", [x.get("tool_choice") for x in r.lena.chats()])

    def test_browser_goal_cpu_context_compacts_before_general_budget_and_tracks_calls(self):
        """A long browser trace must not grow to the normal 16k ctx budget
        before reducing old page dumps. Real output remains in the goal ledger."""
        browser = FakeBrowser()
        browser.url = "https://example.org/report"
        browser.title = "Bericht"
        browser.text = "Beobachteter Seiteninhalt " + ("Details für die Recherche. " * 240)
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port,
                 MAG_ROOM_CTX_TOKENS="16000",
                 MAG_ROOM_GOAL_COMPACT_TOKENS="1200",
                 MAG_ROOM_GOAL_COMPACT_TRIGGER_TOKENS="3200")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        requests = []

        def reply(body):
            requests.append(body)
            if len(requests) <= 7:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return "Die Prüfung ist noch nicht abgeschlossen."

        r.lena.reply = reply
        r.say("@Lena Prüfe auf https://example.org/report die Inhalte dieser Seite")
        r.idle()
        self.assertGreaterEqual(len(requests), 8)
        self.assertTrue(all(body.get("tool_choice") == "required" for body in requests))
        self.assertEqual(requests[0]["messages"], requests[1]["messages"][:len(requests[0]["messages"])])
        log = (r.tmp / "magroom.log").read_text()
        self.assertIn("context compacted for the open goal", log)
        self.assertIn("elapsed_ms=", log)
        self.assertIn("gen_ms=", log)
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        perf = goal["llm_perf"]
        self.assertEqual(perf["calls"], len(requests))
        self.assertGreaterEqual(perf["elapsed_ms"], 0)
        self.assertEqual(perf["last_prompt_tokens"], 105)
        self.assertIn("last_cached_tokens", perf)
        self.assertTrue(goal["evidence_pages"])  # never delete observed provenance
        self.assertEqual(goal["evidence_pages"][-1]["url"], browser.url)

    def test_a_browser_goal_survives_a_lost_click_and_a_compact(self):
        """The 15.11 trace: a click written into the thoughts is done once, the
        goal and the page survive a 21k-token compact, and a plan is not a result."""
        browser = FakeBrowser()
        browser.text = "Gefundene Artikel und ihre Eigenschaften"
        browser.scroll_texts = ["OLDDUMPzzzz " + ("alt " * 800), "MIDPAGE " + ("mitte " * 200), "LASTPAGEZZ " + ("neu " * 200)]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port, MAG_ROOM_VISION="lena")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        r.lena.reply = lambda body: "Hallo Chris."
        r.say("@Lena hallo")
        r.idle()
        self.assertNotIn("tool_choice", r.lena.chats()[-1])
        seen = []
        fix = {"go": False}

        def reply(body):
            i = len(seen)
            if i == 3:
                end = time.time() + 10
                while not fix["go"] and time.time() < end:
                    time.sleep(0.02)
            seen.append(body)
            if i == 0:
                return {"content": "", "tool_calls": [{"name": "browser_open", "arguments": {"url": "https://www.amazon.de/s?k=schrumpfschlauch"}}]}
            if i == 1:
                return {"content": "", "reasoning": "<|tool_call>call:browser_click{n:28}<tool_call|>"}
            if i < 5:
                out = {"content": "", "tool_calls": [{"name": "browser_scroll", "arguments": {"dy": 700}}]}
                if i == 4:
                    out["prompt_tokens"] = 21000
                return out
            if i == 5:
                return "Ich tippe den Suchbegriff ein."
            if i == 6:
                return {"content": "", "tool_calls": [{"name": "browser_type", "arguments": {"text": "schrumpfschlauch 2s", "n": 1}}]}
            if i == 7:
                return "Step 1: Scroll down to the results.\nStep 2: Identify the transparent tube."
            if i == 8:
                return "Step 1: Scroll down to the results.\nStep 2: Identify the transparent tube."
            seen.append("extra")
            return "Das darf nicht das Ergebnis sein."

        r.lena.reply = reply
        r.say("@Lena Nenne fuer mich zwei Schrumpfschlauch transparent auf Amazon.de 18650")
        r.wait(lambda: len(seen) >= 3, "opened and clicked")
        r.say("Nicht 2 Stueck sondern 2s")
        fix["go"] = True
        r.wait(lambda: len(seen) >= 6, "compacted request")
        compact = seen[5]
        blob = json.dumps(compact["messages"], ensure_ascii=False)
        self.assertEqual(compact.get("tool_choice"), "required")
        self.assertIs(compact.get("parallel_tool_calls"), False)
        self.assertAlmostEqual(compact.get("temperature"), 0.3, places=2)
        self.assertAlmostEqual(compact.get("top_p"), 0.9, places=2)
        self.assertEqual(compact.get("top_k"), 40)
        self.assertIn("Schrumpfschlauch", blob)
        self.assertIn("Nicht 2 Stueck sondern 2s", blob)
        self.assertIn("amazon.de", blob.lower())
        self.assertIn("LASTPAGEZZ", blob)
        self.assertNotIn("OLDDUMPzzzz", blob)
        self.assertGreater(len(compact["messages"]), 2)
        self.assertNotIn("image_url", json.dumps(seen[1]))  # dual Xeon CPU: vision off by default
        self.assertNotIn("image_url", json.dumps(seen[3]))
        self.assertEqual(seen[0]["messages"], seen[1]["messages"][:len(seen[0]["messages"])])  # KV prefix stable
        r.idle()
        self.assertEqual(browser.clicks, [28])
        self.assertLess(len(seen), 12)  # the repeated plan stalled instead of looping
        said = " ".join(m.get("text", "") for m in r.messages() if m["from"] == "lena")
        self.assertIn("Abschluss noch nicht verifiziert", said)
        self.assertEqual(r.noah.chats(), [])
        goal_path = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json"
        goal = json.loads(goal_path.read_text())
        self.assertEqual(goal["state"], "active")
        self.assertIn("Amazon.de", goal["text"])
        self.assertIn("2s", " ".join(goal["corrections"]))
        # The goal is not part of the prompt log: a restart of the room keeps it.
        r.lena.reply = lambda body: "PASS"
        r.noah.reply = lambda body: "PASS"
        r.stop()
        r.start()
        goal = json.loads(goal_path.read_text())
        self.assertEqual(goal["state"], "active")
        self.assertIn("Schrumpfschlauch", goal["text"])
        # A status question does not replace the goal. An unseen link is refused; a seen one finishes it.
        fin = {"n": 0}
        proof = [{"id": "result", "url": "https://www.amazon.de/s?k=schrumpfschlauch", "quote": "Suche Schrumpfschlauch"}]

        def finish(body):
            fin["n"] += 1
            fin["last"] = body
            if fin["n"] == 1:
                return {"content": "", "tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "result", "need": "Zwei verschiedene Artikel belegen"}]}}]}
            if fin["n"] == 2:
                return {"content": "", "tool_calls": [{"name": "goal_finish", "arguments": {
                    "summary": "Zwei Stück.", "proofs": proof,
                    "items": [{"name": "Schrumpfschlauch", "url": "https://www.amazon.de/dp/B00SEEN"}]}}]}
            if fin["n"] == 3:
                return {"content": "", "tool_calls": [{"name": "goal_finish", "arguments": {
                    "summary": "Zwei transparente Schrumpfschläuche.", "proofs": proof,
                    "items": [{"name": "Schrumpfschlauch transparent 2s", "url": "https://www.amazon.de/dp/B00SEEN", "price": "4,20 €"},
                              {"name": "Fake", "url": "https://www.amazon.de/dp/B00FAKE"}]}}]}
            if fin["n"] == 4:
                return {"content": "", "tool_calls": [{"name": "goal_finish", "arguments": {
                    "summary": "Zwei transparente Schrumpfschläuche.", "proofs": proof,
                    "items": [{"name": "Schrumpfschlauch transparent 2s", "url": "https://www.amazon.de/dp/B00SEEN", "price": "4,20 €"},
                              {"name": "PVC transparent", "url": "https://www.amazon.de/dp/B00SEEN2", "price": "5,40 €"}]}}]}
            return "Ich habe zwei transparente Schrumpfschläuche 2s gefunden."

        r.lena.reply = finish
        r.say("Done?")
        r.idle()
        self.assertEqual(fin["last"].get("tool_choice"), "required")  # no redundant closing LLM call
        self.assertIn("required", [body.get("tool_choice") for body in r.lena.chats()[-4:-1]])
        steps = [p for m in r.messages() if m["from"] == "lena" for p in m["parts"] if p["k"] == "step" and p["tool"] == "goal_finish"]
        self.assertEqual([s["state"] for s in steps], ["error", "error", "ok"])
        self.assertIn("Mindestens 2", steps[0]["output"])
        self.assertIn("gesehenen Seite", steps[1]["output"])
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertIn("Amazon.de", goal["text"])
        final_text = " ".join(m.get("text", "") for m in r.messages() if m["from"] == "lena")
        self.assertIn("Schrumpfschläuche", final_text)
        self.assertIn("https://www.amazon.de/dp/B00SEEN", final_text)
        self.assertIn("https://www.amazon.de/dp/B00SEEN2", final_text)
        self.assertEqual([x["url"] for x in goal["results"]],
                         ["https://www.amazon.de/dp/B00SEEN", "https://www.amazon.de/dp/B00SEEN2"])
        # A plain chat afterwards does not force a tool.
        r.lena.reply = lambda body: "Gern."
        r.say("@Lena danke")
        r.idle()
        self.assertNotIn("tool_choice", r.lena.chats()[-1])

    def test_dom_links_below_viewport_are_visible_and_prove_item_urls(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/results"
        browser.title = "Ergebnisse"
        browser.text = "Hier sind die Ergebnisse. Einzelheiten stehen im Dokument."
        browser.elements = [{"n": 1, "kind": "search", "label": "Suche",
                             "x": 350, "y": 30}]
        browser.links = [
            {"name": "Passendes Material, Flachmaß 65 mm", "url": "https://example.org/item/65",
             "price": "7,89 EUR"},
            {"name": "Anderes Material, Flachmaß 60 mm", "url": "https://example.org/item/60",
             "price": "6,99 EUR"}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        captured = []

        def reply(body):
            captured.append(body)
            if len(captured) == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "item", "need": "Konkretes Material mit Preis und Link belegen"}]}}]}
            if len(captured) == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Passendes Material, 65 mm, für 7,89 EUR.",
                "proofs": [{"id": "item", "url": browser.url, "quote": "Passendes Material, Flachmaß 65 mm"}],
                "items": [{"name": "Passendes Material, Flachmaß 65 mm",
                           "url": "https://example.org/item/65", "price": "7,89 EUR"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Suche auf https://example.org/results ein Material mit Preis und Link")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(goal["results"][0]["url"], "https://example.org/item/65")
        self.assertIn("https://example.org/item/65", json.dumps(captured[2]["messages"]))
        self.assertEqual(goal["evidence_pages"][-1]["links"][0]["url"], "https://example.org/item/65")
        self.assertEqual(len(captured), 3)

    def test_opt_in_mmproj_browser_look_and_scroll_reach_model_as_images(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/status"
        browser.title = "Status"
        browser.text = "Der Seitenstatus ist erfolgreich bestätigt."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port,
                 MAG_ROOM_VISION="lena", MAG_ROOM_GOAL_EYES="1")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        captured = []

        def reply(body):
            captured.append(body)
            if len(captured) == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "status", "need": "Seitenstatus nachweisen"}]}}]}
            if len(captured) == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if len(captured) == 3:
                return {"tool_calls": [{"name": "browser_scroll", "arguments": {"dy": 300}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Die Statusseite wurde überprüft.",
                "proofs": [{"id": "status", "url": browser.url,
                            "quote": "Seitenstatus ist erfolgreich bestätigt"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/status und prüfe den Seitenstatus")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(captured), 4)
        def has_image(request):
            return any(isinstance(m.get("content"), list) and
                       any(part.get("type") == "image_url" for part in m["content"])
                       for m in request["messages"])
        self.assertFalse(has_image(captured[1]))
        self.assertTrue(has_image(captured[2]))  # browser_look screenshot
        self.assertTrue(has_image(captured[3]))  # scroll screenshot

    def test_prefix_rewarms_before_first_goal_after_deploy_prompt_change(self):
        """Room-only deploy with a live llama-server may change tool schemas.
        Detect that even on a nonempty conversation with unchanged persona."""
        r = Room()
        self.addCleanup(r.close)
        r.lena.reply = lambda body: "Hallo."
        r.say("@Lena hallo")
        r.idle()
        r.stop()
        wire = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "lena.json"
        stored = json.loads(wire.read_text())
        self.assertGreater(len(stored["messages"]), 1)
        # Simulate old prefix hash with identical settings signature.
        stored["prefix"] = "previous-release-tools-changed"
        wire.write_text(json.dumps(stored))
        # The real deploy's new prefix filename has no saved slot. The fake
        # llama does not distinguish filenames when restoring; invalidate its
        # simulated checkpoint explicitly so restore fails and Save is tested.
        r.lena.saved.clear()
        r.lena.slot_actions.clear()
        start = len(r.lena.requests)
        r.start()
        r.wait(lambda: any(a == "save" for a, _ in r.lena.slot_actions), "rebuilt tool prefix")
        r.idle()
        warm = r.lena.requests[start:]
        self.assertTrue(warm and warm[0]["max_tokens"] == 1)
        actions = [a for a, _ in r.lena.slot_actions]
        self.assertIn("save", actions)
        log = (r.tmp / "magroom.log").read_text()
        self.assertIn("system/tool prefix changed; new checkpoint required", log)
        self.assertIn("prewarming before queued work (changed prompt/tools)", log)

    def test_missing_slot_file_is_not_mislabeled_corrupt_checkpoint(self):
        """A real llama.cpp slot dir exposes absent saves before HTTP 400."""
        r = Room()
        self.addCleanup(r.close)
        r.stop()
        slot_dir = r.tmp / "llama-slots" / "lena"
        slot_dir.mkdir(parents=True)
        status = {
            "updated": time.time(),
            "bots": {
                "lena": {"state": "ready", "since": 45, "slot_dir": str(slot_dir)},
                "noah": {"state": "ready", "since": 45},
            }
        }
        pathlib.Path(r.env["MAG_MODELS_STATUS"]).write_text(json.dumps(status))
        r.lena.slot_actions.clear()
        r.start()
        r.wait(lambda: any(a == "save" for a, _ in r.lena.slot_actions), "prefix built from absent slot")
        self.assertNotIn("restore", [a for a, _ in r.lena.slot_actions])
        self.assertIn("prefix checkpoint absent:", (r.tmp / "magroom.log").read_text())

    def test_image_caption_preserves_kv_snapshot_and_restores_it(self):
        """Auxiliary image caption has different prompt/tools and must restore
        the previous full chat KV, not just its system prefix."""
        r = Room(MAG_ROOM_VISION="lena")
        self.addCleanup(r.close)
        r.lena.slot_actions.clear()

        def reply(body):
            return ("Rote Tasse: steht auf einem Tisch."
                    if "Beschreibe das Bild" in text_of(body["messages"][-1].get("content"))
                    else "Das ist die Aufnahme.")
        r.lena.reply = reply
        photo = r.api("POST", "/api/upload?name=aufnahme.png", raw=PNG)[1]
        r.say("@Lena was ist auf dem Bild?", attachments=[photo["file"]])
        r.idle()
        auxiliary = [(a, fn) for a, fn in r.lena.slot_actions
                     if fn == "mag-aux-image-lena.bin"]
        self.assertEqual([a for a, _ in auxiliary], ["save", "restore"])
        self.assertEqual(len(r.lena.chats()), 2)
        self.assertIn("Rote Tasse", json.dumps(r.messages(), ensure_ascii=False))

    def test_remember_appends_memory_without_rehashing_cpu_system_prefix(self):
        """Remember must preserve the 11k-token warm prefix. New memories are
        appended as data, never inserted into the beginning of the prompt."""
        r = Room()
        self.addCleanup(r.close)
        calls = []
        def reply(body):
            calls.append(body)
            if len(calls) == 1:
                return {"tool_calls": [{"name": "remember", "arguments": {
                    "fact": "Das bevorzugte Arbeitsformat ist Markdown"}}]}
            if len(calls) == 2:
                return "Gemerkt."
            return "Ich erinnere mich daran."

        r.lena.reply = reply
        before = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "lena.json").read_text())
        before_prefix = before["prefix"]
        slot_calls = len(r.lena.slot_actions)
        r.say("@Lena Merke dir: Das bevorzugte Arbeitsformat ist Markdown")
        r.idle()
        after_first = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "lena.json").read_text())
        self.assertEqual(after_first["prefix"], before_prefix)
        self.assertNotIn("Das bevorzugte Arbeitsformat ist Markdown", calls[0]["messages"][0]["content"])
        r.say("@Lena Was ist mein bevorzugtes Arbeitsformat?")
        r.idle()
        after = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "lena.json").read_text())
        self.assertEqual(after["prefix"], before_prefix)
        self.assertEqual(calls[0]["messages"][0], calls[-1]["messages"][0])
        memory_msgs = [m for m in calls[-1]["messages"] if
                       "Gedächtnis-Nachtrag des Raums" in text_of(m.get("content"))]
        self.assertEqual(len(memory_msgs), 1)
        self.assertIn("bevorzugte Arbeitsformat", memory_msgs[0]["content"])
        self.assertEqual(after["memory_items"][0]["text"], "Das bevorzugte Arbeitsformat ist Markdown")
        self.assertNotIn("save", [a for a, _ in r.lena.slot_actions[slot_calls:]])
        self.assertNotIn("restore", [a for a, _ in r.lena.slot_actions[slot_calls:]])

    def test_length_cut_native_partial_tool_does_not_execute_incomplete_args(self):
        """Partial native function arguments must NOT be mended into an empty
        valid call. Retry at higher budget without exposing partial thoughts."""
        browser = FakeBrowser()
        browser.url = "https://example.org/initial"
        browser.text = "Die Statusinformation ist bestätigt."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        calls = []
        first_url = []

        def reply(body):
            calls.append(body)
            n = len(calls)
            if n == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "status", "need": "Statusseite prüfen"}]}}]}
            if n == 2:
                return {"reasoning": "UNVOLLSTÄNDIGER_GEDANKE",
                        "raw_tool_calls": [{"name": "browser_open", "arguments":
                                            '{"url":"https://example.org/trunca'}],
                        "finish_reason": "length", "completion_tokens": 1536}
            if n == 3:
                first_url.append(browser.url)
                return {"tool_calls": [{"name": "browser_open", "arguments": {
                    "url": "https://example.org/verified"}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Status bestätigt.",
                "proofs": [{"id": "status", "url": browser.url,
                            "quote": "Statusinformation ist bestätigt"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/verified und prüfe die Statusinformation")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(calls), 4)
        self.assertEqual(first_url, ["https://example.org/initial"])
        self.assertEqual([int(x["max_tokens"]) for x in calls], [1536, 1536, 3072, 3072])
        self.assertTrue(all(x["tools"] == calls[0]["tools"] for x in calls))
        self.assertNotIn("UNVOLLSTÄNDIGER_GEDANKE",
                         " ".join(m.get("reasoning", "") for m in r.messages() if m.get("from") == "lena"))
        self.assertIn("discarded incomplete native tool batch after length cap",
                      (r.tmp / "magroom.log").read_text())
        steps = [p["tool"] for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts") or [] if p.get("k") == "step"]
        self.assertEqual(steps, ["goal_requirements", "browser_open", "goal_finish"])

    def test_cpu_prefix_preflight_has_no_300_second_stall_limit(self):
        # A 300+ s silent prefill must not be killed. Fake models are instant,
        # so verify the explicit API budget; normal integration tests cover
        # that a prefix is in fact saved and restored.
        source = (ROOT / "mag-room" / "native" / "engine.hpp").read_text()
        prefix = source[source.index("void warm_prefix("):source.index("void set_kv(", source.index("void warm_prefix("))]
        self.assertIn("llm::chat(b.backend, body, cancel ? cancel.get() : &never, {}, 0)", prefix)
        self.assertNotIn("{}, 900", prefix)

    def test_goal_length_before_tool_call_gets_extra_tokens_without_pausing(self):
        """A model that spends its 512-token envelope thinking can still act.
        finish_reason=length is distinct from an unproductive stop."""
        browser = FakeBrowser()
        browser.url = "https://example.org/status"
        browser.text = "Die Seite zeigt den bestätigten Status."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        requests = []

        def reply(body):
            requests.append(body)
            if len(requests) == 1:
                return {"reasoning": "Die aktuelle Seite ist noch nicht geprüft; ich brauche einen Browseraufruf.",
                        "finish_reason": "length", "completion_tokens": 1536}
            if len(requests) == 2:
                return {"tool_calls": [{"name": "browser_open", "arguments": {"url": browser.url}}]}
            if len(requests) == 3:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "status", "need": "Seitenstatus belegen"}]}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Der Seitenstatus ist bestätigt.",
                "proofs": [{"id": "status", "url": browser.url,
                            "quote": "Seite zeigt den bestätigten Status"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/status und prüfe den Seitenstatus")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(requests), 4)
        self.assertEqual([int(x["max_tokens"]) for x in requests], [1536, 3072, 3072, 3072])
        self.assertTrue(all(x["tools"] == requests[0]["tools"] for x in requests))
        log = (r.tmp / "magroom.log").read_text()
        self.assertIn("finish_reason=length; raising max_tokens 1536 -> 3072", log)
        self.assertNotIn("goal paused after 3 consecutive missing tool calls", log)

    def test_failed_prefix_restore_rebuilds_even_with_saved_conversation(self):
        r = Room()
        self.addCleanup(r.close)
        # First ever warm-up: no checkpoint, then rebuild and save.
        actions = list(r.lena.slot_actions)
        self.assertIn("restore", [a for a, _ in actions])
        self.assertIn("save", [a for a, _ in actions])
        self.assertIn("prefix restore failed", (r.tmp / "magroom.log").read_text())
        r.lena.reply = lambda body: "Hallo."
        r.say("@Lena Hallo")
        r.idle()
        r.stop()
        wire = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "wire" / "lena.json"
        data = json.loads(wire.read_text())
        self.assertGreater(len(data["messages"]), 1)
        # Model runtime changed: force a cold slot and simulated incompatible
        # saved checkpoint. It must warm the stable prefix despite the history.
        data["model_since"] = -99999
        wire.write_text(json.dumps(data))
        r.lena.saved.clear()
        start = len(r.lena.slot_actions)
        r.start()
        r.idle()
        actions = [a for a, _ in r.lena.slot_actions[start:]]
        self.assertIn("restore", actions)
        self.assertIn("erase", actions)
        self.assertIn("save", actions)
        self.assertLess(actions.index("restore"), actions.index("save"))
        self.assertIn("prefix rebuilt and saved", (r.tmp / "magroom.log").read_text())

    def test_goal_slot_does_not_describe_unrelated_image_until_goal_ends(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/report"
        browser.text = "Bisher kein bestätigtes Ergebnis"
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port,
                 MAG_ROOM_VISION="lena")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        photo = r.api("POST", "/api/upload?name=test.png", raw=PNG)[1]
        seen = []

        def reply(body):
            last = text_of(body["messages"][-1].get("content"))
            seen.append(last)
            if len(seen) == 1:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if "Beschreibe das Bild" in last:
                return "Eine unwichtige Bildbeschreibung."
            return "Ich muss die Seite noch prüfen."

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/report und prüfe den Status",
              attachments=[photo["file"]])
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        self.assertEqual(goal["progress"], "paused")
        self.assertFalse(any("Beschreibe das Bild" in msg for msg in seen), seen)
        self.assertEqual(r.lena.chats()[0]["max_tokens"], 1536)
        self.assertEqual(len([m for m in r.messages() if m.get("from") == "lena"]), 1)

    def test_browser_goal_uses_short_model_view_but_preserves_full_page_evidence(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/large"
        browser.title = "Lang"
        browser.text = "Sehr langer Seitentext. " * 500
        browser.elements = [{"n": i, "kind": "link", "label": "Navigationspunkt %d" % i,
                             "href": "https://example.org/nav/%d" % i, "x": 100, "y": i * 9}
                            for i in range(1, 48)]
        browser.links = [{"name": "Dokument %d" % i, "url": "https://example.org/doc/%d" % i}
                         for i in range(1, 24)]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        requests = []

        def reply(body):
            requests.append(body)
            if len(requests) == 1:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return "Ich prüfe noch."

        r.lena.reply = reply
        r.say("@Lena Untersuche https://example.org/large und prüfe die Dokumente")
        # A newly queued browser goal can be momentarily idle before the
        # worker claims it. Require the actual second inference request that
        # carries the browser observation, not just a transient idle status.
        r.wait(lambda: len(requests) >= 2, "browser observation delivered to model", 25)
        r.idle()
        observation = next(m["content"] for m in requests[1]["messages"] if m["role"] == "tool")
        self.assertLess(len(observation), 3900)
        self.assertIn("Weitere echte Seitenlinks", observation)
        self.assertIn("browser_read", observation)
        self.assertNotIn(" (100,", observation)  # index is enough; no redundant pixel coords
        self.assertLessEqual(observation.count("\n- Dokument"), 2)  # no irrelevant link flood
        self.assertEqual(browser.screenshot_requests, 0)  # text-only CPU goal
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        page = goal["evidence_pages"][-1]
        self.assertGreaterEqual(len(page["links"]), 20)
        self.assertIn("Sehr langer Seitentext", page["text"])

    def test_browser_goal_step_screenshot_opt_in_still_captures_jpeg(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/article"
        browser.text = "Dokument ist sichtbar."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port,
                 MAG_ROOM_BROWSER_STEP_SHOTS="1")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)

        def reply(body):
            if len(r.lena.chats()) == 1:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return "Ich benötige noch einen Beleg."

        r.lena.reply = reply
        r.say("@Lena Prüfe https://example.org/article")
        r.idle()
        self.assertGreaterEqual(browser.screenshot_requests, 1)
        parts = [p for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts") or [] if p["k"] == "step"]
        self.assertTrue(parts and parts[0].get("shot"))

    def test_browser_script_error_keeps_description_before_metadata_limit(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/items"
        browser.title = "Katalog"
        browser.elements = [{"n": 1, "kind": "link", "label": "Artikel", "x": 110, "y": 40}]
        browser.click_error = "Browser script: " + json.dumps({
            "exceptionId": 1, "text": "Uncaught", "lineNumber": 0, "columnNumber": 74,
            "scriptId": "393", "exception": {"type": "object", "subtype": "error",
                "className": "Error",
                "description": "Error: Element 1 is not there any more; look at the page again\n" + "stack " * 100}})
        browser.click_error_replacement = [{"n": 27, "kind": "link", "label": "Artikel (neu gerendert)", "x": 120, "y": 50}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        calls = iter([
            {"tool_calls": [{"name": "browser_look", "arguments": {}}]},
            {"tool_calls": [{"name": "browser_click", "arguments": {"n": 1}}]},
            {"tool_calls": [{"name": "browser_click", "arguments": {"n": 27}}]},
        ])
        r.lena.reply = lambda body: next(calls, "Das Element ist nicht mehr vorhanden.")
        r.say("@Lena Öffne den Artikel im Browser auf https://example.org/items")
        r.idle()
        steps = [p for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts", []) if p.get("k") == "step" and p.get("tool") == "browser_click"]
        self.assertEqual([p["state"] for p in steps], ["error", "ok"])
        self.assertIn("Error: Element 1 is not there any more", steps[0]["output"])
        self.assertNotIn("exceptionId", steps[0]["output"])
        self.assertIn("27", steps[0]["output"])
        self.assertEqual(browser.clicks, [1, 27], "new DOM index must reach the controller, old index is not replayed")

    def test_browser_type_unverified_input_is_error_then_observed_value_and_enter(self):
        """HTTP 200 alone must not turn an unfocused CDP keystroke green.
        DOM-only agents must read actual input value and know Enter was not
        pressed; this does not use screenshots or an mmproj."""
        browser = FakeBrowser()
        browser.url = "https://example.org/items"
        browser.title = "Katalog"
        browser.text = "Bestätigter Inhalt ist sichtbar."
        browser.elements = [{"n": 1, "kind": "search", "label": "Suchen", "x": 110, "y": 40}]
        browser.type_failures = 1
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port,
                 MAG_ROOM_GOAL_EYES="0")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        requests = []

        def reply(body):
            requests.append(body)
            i = len(requests)
            if i == 1: return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if i in (2, 3):
                return {"tool_calls": [{"name": "browser_type", "arguments": {
                    "n": 1, "text": "Beispielbegriff", "enter": False}}]}
            if i == 4: return {"tool_calls": [{"name": "browser_key", "arguments": {"key": "Enter"}}]}
            if i == 5: return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                "requirements": [{"id": "status", "need": "Bestätigung von gesehener Seite"}]}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Seitenstatus anhand der aktuellen Seite bestätigt.",
                "proofs": [{"id": "status", "url": browser.url,
                            "quote": "Bestätigter Inhalt ist sichtbar"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Prüfe auf https://example.org/items den Seitenstatus")
        r.idle()
        pages = [p for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts") or [] if p["k"] == "step"]
        typed = [p for p in pages if p["tool"] == "browser_type"]
        self.assertEqual([p["state"] for p in typed], ["error", "ok"])
        self.assertIn("nicht bestätigt", typed[0]["output"])
        self.assertIn("nicht gedrückt", typed[1]["output"].lower())
        self.assertEqual([c["enter"] for c in browser.typed], [False, False])
        self.assertIn('Wert: \\"Beispielbegriff\\"', json.dumps(requests[3]["messages"], ensure_ascii=False))
        self.assertNotIn("image_url", json.dumps(requests[3]))
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")

    def test_duplicate_requirements_read_is_idempotent_and_invalid_finish_is_repaired(self):
        """A new page does not invalidate criteria. Broken native JSON never
        finishes a goal; the real model gets the immutable IDs and must retry."""
        browser = FakeBrowser()
        browser.url = "https://example.org/verified"
        browser.title = "Ergebnis"
        browser.text = "Der Status ist nachweislich bestätigt."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        requests = []
        proof = {"id": "status", "url": browser.url, "quote": "Status ist nachweislich bestätigt"}

        def reply(body):
            requests.append(body)
            i = len(requests)
            if i == 1: return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                "requirements": [{"id": "status", "need": "Status durch Seitenzitat belegen"}]}}]}
            if i == 2: return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if i == 3: return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                "requirements": [{"id": "overwrite", "need": "Soll nicht ersetzen"}]}}]}
            if i == 4: return {"raw_tool_calls": [{"name": "goal_finish", "arguments":
                '{"summary":"unvollständiger Aufruf","proofs":['}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Der beobachtete Status ist belegt.", "proofs": [proof]}}]}

        r.lena.reply = reply
        r.say("@Lena Prüfe auf https://example.org/verified den Status")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual([x["id"] for x in goal["requirements"]], ["status"])
        steps = [p for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts") or [] if p["k"] == "step"]
        duplicates = [p for p in steps if p["tool"] == "goal_requirements"]
        self.assertEqual([p["state"] for p in duplicates], ["ok", "ok"])
        self.assertIn("unverändert", duplicates[-1]["output"])
        finish_steps = [p for p in steps if p["tool"] == "goal_finish"]
        self.assertEqual([p["state"] for p in finish_steps], ["error", "ok"])
        self.assertIn("kein gültiges JSON", finish_steps[0]["output"])
        self.assertIn("goal_requirements NICHT wiederholen",
                      json.dumps(requests[-1]["messages"], ensure_ascii=False))

    def test_goal_search_then_missing_calls_still_opens_browser_without_user_retry(self):
        """Regression: external search is not a page snapshot. Text-only
        decisions after it must not strand the task before opening Chrome."""
        browser = FakeBrowser()
        browser.title = "Bericht"
        browser.text = "Der Status ist auf der Seite bestätigt."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        requests = []

        def reply(body):
            requests.append(body)
            n = len(requests)
            if n == 1:
                return {"tool_calls": [{"name": "web_search", "arguments": {"query": "status zu beispielbericht"}}]}
            if n in (2, 3):
                return "Ich recherchiere und werde die Seite dann öffnen."
            if n == 4:
                return {"tool_calls": [{"name": "browser_open", "arguments": {"url": "https://example.org/report"}}]}
            if n == 5:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "status", "need": "Bestätigung der Seite nachweisen"}]}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Der Bericht wurde geöffnet und seine Bestätigung geprüft.",
                "proofs": [{"id": "status", "url": browser.url,
                            "quote": "Status ist auf der Seite bestätigt"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/report und prüfe die Statusbestätigung")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(requests), 6)
        self.assertEqual(browser.url, "https://example.org/report")
        self.assertEqual(goal["evidence_pages"][-1]["url"], browser.url)
        self.assertTrue(all(x.get("tool_choice") == "required" for x in requests))
        # No schema switch before and after missing tool calls: warm KV prefix.
        self.assertTrue(all(x["tools"] == requests[0]["tools"] for x in requests))
        self.assertIn("keine Browserbeobachtung", json.dumps(requests[1]["messages"], ensure_ascii=False))
        self.assertIn("browser_open", json.dumps(requests[3]["messages"], ensure_ascii=False))
        self.assertNotIn("Automatischer Abschluss ohne gültigen Werkzeugaufruf",
                         " ".join(m.get("text", "") for m in r.messages() if m.get("from") == "lena"))

    def test_goal_no_browser_evidence_eventually_pauses_without_fabricated_url(self):
        r = Room()
        self.addCleanup(r.close)
        requests = []

        def reply(body):
            requests.append(body)
            if len(requests) == 1:
                return {"tool_calls": [{"name": "web_search", "arguments": {"query": "information im web"}}]}
            return "Ich werde die Ergebnisse prüfen."

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/info und überprüfe den Status")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        self.assertEqual(goal["progress"], "paused")
        self.assertEqual(len(requests), 4)  # tool + three missing calls, not an infinite loop
        self.assertFalse(goal.get("evidence_pages"))
        self.assertIn("zwei automatischen Korrekturversuchen", goal["error"])
        self.assertTrue(all(x["tools"] == requests[0]["tools"] for x in requests))
        self.assertNotIn("results", goal)

    def test_standalone_json_navigation_requires_native_tool_call(self):
        """Text-JSON must never open URLs or run searches on its own."""
        browser = FakeBrowser()
        browser.text = "Der Status wurde bestätigt."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        calls = []
        before = []

        def reply(body):
            calls.append(body)
            if len(calls) == 1:
                return '{"action":"browser_open","url":"https://example.org/result"}'
            if len(calls) == 2:
                before.append(browser.url)
                return {"tool_calls": [{"name": "browser_open", "arguments": {
                    "url": "https://example.org/result"}}]}
            if len(calls) == 3:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "state", "need": "Status aus Seite belegen"}]}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Seitenstatus geprüft.",
                "proofs": [{"id": "state", "url": browser.url,
                            "quote": "Status wurde bestätigt"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/result und prüfe den Status")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(calls), 4)
        self.assertEqual(before, ["about:blank"])
        self.assertEqual(browser.url, "https://example.org/result")
        steps = [p for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts") or [] if p["k"] == "step"]
        self.assertEqual([p["tool"] for p in steps], ["browser_open", "goal_requirements", "goal_finish"])
        self.assertNotIn('{"action":"browser_open"', " ".join(m.get("text", "") for m in r.messages()))

    def test_tool_recovery_keeps_browser_type_after_existing_page_observation(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/page"
        browser.text = "Der Bericht wurde beobachtet und dokumentiert."
        browser.elements = [{"n": 1, "kind": "text", "label": "Suche", "x": 100, "y": 50}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        calls = []

        def reply(body):
            calls.append(body)
            if len(calls) == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "result", "need": "Die Berichtsaussage belegen"}]}}]}
            if len(calls) == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if len(calls) == 3:
                return "Ich werde erst etwas eintippen."
            if len(calls) == 4:
                return {"tool_calls": [{"name": "browser_type", "arguments": {"text": "Status", "n": 1}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Die Aussage des Berichts wurde geprüft.",
                "proofs": [{"id": "result", "url": browser.url,
                            "quote": "Bericht wurde beobachtet und dokumentiert"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Öffne https://example.org/page und prüfe den Bericht")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(x["tools"] == calls[0]["tools"] for x in calls))
        self.assertIn("browser_type", [x["function"]["name"] for x in calls[3]["tools"]])
        steps = [p["tool"] for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts") or [] if p["k"] == "step"]
        self.assertIn("browser_type", steps)

    def test_goal_finishes_after_plain_text_via_grounded_narrow_tool_recovery(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/results"
        browser.title = "Ergebnisübersicht"
        browser.text = "Kandidat A, Breite 60 mm, Preis 6,99 EUR."
        browser.elements = [{"n": 3, "kind": "link", "label": "Kandidat A, Breite 60 mm",
                             "href": "https://example.org/item/3", "price": "6,99 EUR",
                             "x": 100, "y": 150}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        seen = []

        def reply(body):
            i = len(seen)
            seen.append(body)
            if i == 0:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "choice", "need": "Breite und Preis belegen"}]}}]}
            if i == 1:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if i == 2:
                return "Ich habe einen passenden Artikel mit den verlangten Angaben gesehen."
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Kandidat A hat 60 mm Breite und kostet 6,99 EUR.",
                "proofs": [{"id": "choice", "url": browser.url,
                            "quote": "Breite 60 mm, Preis 6,99 EUR"}],
                "items": [{"name": "Kandidat A, Breite 60 mm", "url": "https://example.org/item/3",
                           "price": "6,99 EUR"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Suche auf https://example.org/results eine passende Option mit Breite, Preis und Link")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(seen), 4)
        offered_before = {x["function"]["name"] for x in seen[2]["tools"]}
        offered_after = {x["function"]["name"] for x in seen[3]["tools"]}
        self.assertEqual(offered_after, offered_before)  # cacheable identical tool prefix during recovery
        self.assertIn("goal_finish", offered_after)
        self.assertIn("browser_type", offered_after)
        self.assertIn("browser_flow", offered_after)
        self.assertIn("web_search", offered_after)
        self.assertIn("https://example.org/item/3", json.dumps(seen[3]["messages"], ensure_ascii=False))
        self.assertEqual(goal["results"][0]["url"], "https://example.org/item/3")

    def test_goal_non_tool_completion_pauses_with_witnessed_links_not_stalled(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/results"
        browser.title = "Ergebnisübersicht"
        browser.text = "Der verfügbare Kandidat hat einen sichtbaren Preis."
        browser.elements = [{"n": 3, "kind": "link", "label": "Kandidat A",
                             "href": "https://example.org/item/3", "price": "6,99 EUR",
                             "x": 100, "y": 150}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        i = {"n": 0}

        def reply(body):
            i["n"] += 1
            if i["n"] == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "result", "need": "Die gefundenen Details prüfen"}]}}]}
            if i["n"] == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return "Ich arbeite an der Auswahl."

        r.lena.reply = reply
        r.say("@Lena Suche auf https://example.org/results eine Option mit Preis und Link")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        self.assertEqual(goal["progress"], "paused")
        self.assertEqual(i["n"], 5)  # two corrective retries before the bounded pause
        said = " ".join(m.get("text", "") for m in r.messages() if m.get("from") == "lena")
        self.assertIn("Abschluss noch nicht verifiziert", said)
        self.assertIn("https://example.org/item/3", said)
        self.assertNotIn("Ich hänge", said)

    def test_witnessed_recovery_selects_best_grounded_offer_not_ads_or_wrong_size(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/results"
        browser.title = "Suchtreffer"
        browser.text = "Gefundene Angebote für transparente PVC-Schrumpfschläuche"
        browser.elements = [{"n": 1, "kind": "search", "label": "Suche", "x": 200, "y": 40}]
        browser.links = [
            {"name": "", "url": "https://example.org/sspa/click?x=7", "price": "1,00 EUR"},
            {"name": "Werbung: PVC Schrumpfschlauch 60 mm Flachmaß",
             "url": "https://example.org/redirect?to=/item/advert", "price": "1,49 EUR"},
            {"name": "PVC Schrumpfschlauch transparent 50 mm Flachmaß",
             "url": "https://example.org/item/wrong", "price": "3,99 EUR"},
            {"name": "ISOLATECH PVC Schrumpfschlauch transparent 60 mm Flachmaß",
             "url": "https://example.org/item/best", "price": "7,49 EUR"},
            {"name": "PVC Schrumpfschlauch transparent 65 mm Flachmaß",
             "url": "https://example.org/item/other", "price": "9,79 EUR"}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        requests = []

        def reply(body):
            requests.append(body)
            if len(requests) == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "choice", "need": "Maßbereich und Preis sowie Link belegen"}]}}]}
            if len(requests) == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return "Ich vergleiche die sichtbaren Angebote."

        r.lena.reply = reply
        r.say("@Lena Suche auf https://example.org/results PVC Schrumpfschlauch transparent, 60–65 mm Flachmaß, mit Preis und Link")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(goal["progress"], "done")
        self.assertEqual(len(requests), 5)  # full palette, look, 2 retries, grounded completion
        self.assertEqual(goal["results"][0]["url"], "https://example.org/item/best")
        self.assertEqual(goal["results"][0]["price"], "7,49 EUR")
        seen_urls = [link["url"] for page in goal["evidence_pages"] for link in page["links"]]
        self.assertNotIn("https://example.org/sspa/click?x=7", seen_urls)
        self.assertNotIn("https://example.org/redirect?to=/item/advert", seen_urls)
        self.assertEqual(goal["proofs"][0]["url"], browser.url)
        self.assertIn("60 mm Flachmaß", goal["proofs"][0]["quote"])
        said = " ".join(m.get("text", "") for m in r.messages() if m["from"] == "lena")
        self.assertIn("Beste belegte Option", said)
        self.assertIn("https://example.org/item/best", said)
        self.assertIn("7,49 EUR", said)
        self.assertNotIn("redirect?to=", said)
        self.assertNotIn("sspa/click", said)
        self.assertNotIn("Abschluss noch nicht verifiziert", said)
        self.assertEqual(r.noah.chats(), [])

    def test_witnessed_recovery_wont_invent_price_or_override_requested_measure(self):
        for label, price in [
            ("PVC Schrumpfschlauch transparent 60 mm Flachmaß", ""),
            ("PVC Schrumpfschlauch transparent 70 mm Flachmaß", "7,49 EUR"),
            ("PVC Schrumpfschlauch transparent Durchmesser 60 mm", "7,49 EUR"),
        ]:
            with self.subTest(label=label, price=price):
                browser = FakeBrowser()
                browser.url = "https://example.org/results"
                browser.text = "Angebotsübersicht"
                browser.links = [{"name": label, "url": "https://example.org/item/one",
                                  **({"price": price} if price else {})}]
                r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
                try:
                    seen = {"n": 0}

                    def reply(body):
                        seen["n"] += 1
                        if seen["n"] == 1:
                            return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                                "requirements": [{"id": "details", "need": "Breite und Preis belegen"}]}}]}
                        if seen["n"] == 2:
                            return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
                        return "Die Auswahl ist noch offen."

                    r.lena.reply = reply
                    r.say("@Lena Suche auf https://example.org/results PVC Schrumpfschlauch transparent 60-65 mm Flachmaß mit Preis und Link")
                    r.idle()
                    goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
                    self.assertEqual(goal["state"], "active")
                    self.assertEqual(goal["progress"], "paused")
                    self.assertNotIn("results", goal)
                finally:
                    r.close()
                    browser.close()

    def test_witnessed_recovery_does_not_claim_purchase_from_search_page(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/results"
        browser.text = "Produktangebote"
        browser.links = [{"name": "Transparenter PVC Schrumpfschlauch 60 mm Flachmaß",
                          "url": "https://example.org/item/a", "price": "7,49 EUR"}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        turn = {"n": 0}

        def reply(body):
            turn["n"] += 1
            if turn["n"] == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "buy", "need": "Bestellung tatsächlich abschließen"}]}}]}
            if turn["n"] == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return "Ich habe den Artikel gefunden."

        r.lena.reply = reply
        r.say("@Lena Bestelle auf https://example.org/results transparenten PVC Schrumpfschlauch 60 mm Flachmaß, mit Preis")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        self.assertEqual(goal["progress"], "paused")
        self.assertNotIn("results", goal)

    def test_witnessed_recovery_respects_unproven_non_product_requirement(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/results"
        browser.text = "Ergebnisübersicht"
        browser.links = [{"name": "Transparenter PVC Schrumpfschlauch 60 mm Flachmaß",
                          "url": "https://example.org/item/a", "price": "7,49 EUR"}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        n = {"count": 0}

        def reply(body):
            n["count"] += 1
            if n["count"] == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [
                        {"id": "item", "need": "PVC Material mit Preis belegen"},
                        {"id": "delivery", "need": "Lieferung bis morgen verbindlich bestätigen"}]}}]}
            if n["count"] == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return "Hier ist ein Angebot."

        r.lena.reply = reply
        r.say("@Lena Suche auf https://example.org/results PVC Schrumpfschlauch 60 mm Flachmaß mit Preis und Lieferung morgen")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        self.assertEqual(goal["progress"], "paused")
        self.assertNotIn("results", goal)

    def test_goal_finish_rejects_redirect_as_item_even_if_observed(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/results"
        browser.text = "Material wird angeboten."
        browser.links = [
            {"name": "PVC Schrumpfschlauch 60 mm Flachmaß",
             "url": "https://example.org/sspa/click?target=item", "price": "7,49 EUR"},
            {"name": "PVC Schrumpfschlauch 60 mm Flachmaß",
             "url": "https://example.org/item/direct", "price": "7,49 EUR"}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        n = {"count": 0}

        def reply(body):
            n["count"] += 1
            if n["count"] == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "item", "need": "Einen konkreten Artikel belegen"}]}}]}
            if n["count"] == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            url = "https://example.org/sspa/click?target=item" if n["count"] == 3 else "https://example.org/item/direct"
            return {"tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "PVC Schrumpfschlauch mit 60 mm Flachmaß.",
                "proofs": [{"id": "item", "url": browser.url, "quote": "PVC Schrumpfschlauch 60 mm Flachmaß"}],
                "items": [{"name": "PVC Schrumpfschlauch 60 mm Flachmaß", "url": url,
                           "price": "7,49 EUR"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Suche auf https://example.org/results PVC Schrumpfschlauch 60 mm Flachmaß")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(goal["results"][0]["url"], "https://example.org/item/direct")
        steps = [p for m in r.messages() if m.get("from") == "lena"
                 for p in m.get("parts") or [] if p["k"] == "step" and p["tool"] == "goal_finish"]
        self.assertEqual([p["state"] for p in steps], ["error", "ok"])
        self.assertIn("Weiterleitungslink", steps[0]["output"])

    def test_standalone_json_goal_finish_executes_once_through_verification(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/overview"
        browser.title = "Übersicht"
        browser.text = "Das Dokument wurde sichtbar geprüft."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        i = {"n": 0}

        def reply(body):
            i["n"] += 1
            if i["n"] == 1:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "status", "need": "Prüfstatus nachweisen"}]}}]}
            if i["n"] == 2:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return json.dumps({"action": "goal_finish", "summary": "Das Dokument ist geprüft.",
                               "proofs": [{"id": "status", "url": browser.url,
                                           "quote": "Dokument wurde sichtbar geprüft"}]})

        r.lena.reply = reply
        r.say("@Lena Prüfe https://example.org/overview")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(i["n"], 3)
        said = " ".join(m.get("text", "") for m in r.messages() if m.get("from") == "lena")
        self.assertIn("Das Dokument ist geprüft.", said)
        self.assertNotIn('{"action": "goal_finish"', said)

    def test_goal_evidence_must_quote_same_seen_page_for_every_requirement(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/report"
        browser.text = "Alpha wurde im Bericht bestätigt; Beta ist noch offen."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        calls = []

        def reply(body):
            i = len(calls)
            calls.append(body)
            if i == 0:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {"requirements": [
                    {"id": "a", "need": "Alpha bestätigen"}, {"id": "b", "need": "Beta prüfen"}]}}]}
            if i == 1:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if i == 2:
                return {"tool_calls": [{"name": "goal_finish", "arguments": {"summary": "Alles geprüft", "proofs": [
                    {"id": "a", "url": browser.url, "quote": "Alpha wurde"}]}}]}
            if i == 3:
                return {"tool_calls": [{"name": "goal_finish", "arguments": {"summary": "Alles geprüft", "proofs": [
                    {"id": "a", "url": browser.url, "quote": "Alpha wurde"},
                    {"id": "b", "url": "https://example.org/other", "quote": "Beta ist noch offen"}]}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {"summary": "Beide Aussagen geprüft", "proofs": [
                {"id": "a", "url": browser.url, "quote": "Alpha wurde"},
                {"id": "b", "url": browser.url, "quote": "Beta ist noch offen"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Prüfe die Aussagen auf https://example.org/report")
        r.idle()
        path = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json"
        goal = json.loads(path.read_text())
        self.assertEqual(goal["state"], "done")
        self.assertEqual(len(goal["requirements"]), 2)
        self.assertEqual(len(goal["proofs"]), 2)
        steps = [p for m in r.messages() if m["from"] == "lena" for p in m.get("parts") or []
                 if p["k"] == "step" and p["tool"] == "goal_finish"]
        self.assertEqual([p["state"] for p in steps], ["error", "error", "ok"])

    def test_correction_after_completed_goal_creates_new_generic_goal(self):
        browser = FakeBrowser()
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port,
                 MAG_ROOM_GOAL_MAX_TOKENS="512", MAG_ROOM_GOAL_CALL_BUDGET_S="180")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        r.stop()
        goals = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals"
        goals.mkdir(parents=True, exist_ok=True)
        (goals / "lena.json").write_text(json.dumps({
            "id": "earlier-completed", "owner": "lena", "state": "done", "progress": "done",
            "text": "@Lena Öffne https://example.org und fasse die Seite zusammen",
            "version": 3, "corrections": [], "summary": "Erster Bericht"}))
        browser.url = "https://example.org/info"
        browser.title = "Info-Seite"
        browser.text = "Überblick und Kontaktinformationen"
        r.start()
        calls = []

        def reply(body):
            calls.append(body)
            if len(calls) == 1:
                return {"content": "", "tool_calls": [{"name": "goal_requirements", "arguments": {
                    "requirements": [{"id": "contact", "need": "Kontaktangaben auf der Seite belegen"}]}}]}
            if len(calls) == 2:
                return {"content": "", "tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return {"content": "", "tool_calls": [{"name": "goal_finish", "arguments": {
                "summary": "Die Seite enthält Überblick und Kontaktinformationen.",
                "proofs": [{"id": "contact", "url": "https://example.org/info", "quote": "Kontaktinformationen"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Korrektur: Bitte zusätzlich die Kontaktinformationen prüfen.")
        r.idle()
        goal = json.loads((goals / "lena.json").read_text())
        self.assertEqual(goal["state"], "done")
        self.assertNotEqual(goal["id"], "earlier-completed")
        self.assertEqual(goal["supersedes"], "earlier-completed")
        self.assertEqual(goal["corrections"], ["@Lena Korrektur: Bitte zusätzlich die Kontaktinformationen prüfen."])
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c.get("tool_choice") == "required" for c in calls))
        self.assertTrue(all(c.get("max_tokens") == 512 for c in calls))
        self.assertEqual(r.noah.chats(), [])

    def test_failed_goal_request_stalls_instead_of_finishing_with_empty_reply(self):
        r = Room(MAG_ROOM_GOAL_MAX_TOKENS="512")
        self.addCleanup(r.close)
        r.lena.reply = lambda body: {"http_error": "simulated inference failure"}
        r.say("@Lena Öffne https://example.org und prüfe die Seite")
        r.idle()
        goal_file = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json"
        goal = json.loads(goal_file.read_text())
        self.assertEqual(goal["state"], "active")
        self.assertEqual(goal["progress"], "stalled")
        self.assertIn("simulated inference failure", goal["error"])
        self.assertEqual(r.lena.chats()[-1].get("tool_choice"), "required")
        self.assertEqual(r.lena.chats()[-1].get("max_tokens"), 512)
        out = " ".join(m.get("text", "") for m in r.messages() if m.get("from") == "lena")
        self.assertIn("Browser-Auftrag pausiert", out)

    def test_generic_browser_flow_is_one_roundtrip_and_rejects_buttons(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/start"
        browser.title = "Dokument"
        browser.text = "Status des Dokuments ist geprüft."
        browser.elements = [{"n": 7, "kind": "button", "label": "Anmeldung absenden",
                             "href": "https://example.org/submit", "x": 100, "y": 100}]
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        calls = []
        url_before_valid_flow = []

        def reply(body):
            i = len(calls)
            calls.append(body)
            if i == 0:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if i == 1:
                return {"tool_calls": [{"name": "browser_flow", "arguments": {"steps": [
                    {"op": "link_click", "n": 7}, {"op": "scroll", "dy": 300}]}}]}
            if i == 2:
                # The first operation must NOT happen: a later operation is invalid.
                return {"tool_calls": [{"name": "browser_flow", "arguments": {"steps": [
                    {"op": "open", "url": "https://example.org/unintended-navigation"},
                    {"op": "type", "text": "unsafe"}]}}]}
            if i == 3:
                url_before_valid_flow.append(browser.url)
                return {"tool_calls": [{"name": "browser_flow", "arguments": {"steps": [
                    {"op": "open", "url": "https://example.org/start"},
                    {"op": "scroll", "dy": 300}, {"op": "wait", "seconds": 0.1}]}}]}
            if i == 4:
                return {"tool_calls": [{"name": "goal_requirements", "arguments": {"requirements": [
                    {"id": "status", "need": "Den angezeigten Dokumentstatus belegen"}]}}]}
            return {"tool_calls": [{"name": "goal_finish", "arguments": {"summary": "Status nachgewiesen.",
                "proofs": [{"id": "status", "url": "https://example.org/start", "quote": "Dokuments ist geprüft"}]}}]}

        r.lena.reply = reply
        r.say("@Lena Prüfe https://example.org/start")
        r.idle()
        steps = [p for m in r.messages() if m.get("from") == "lena" for p in m.get("parts") or []
                 if p.get("k") == "step" and p.get("tool") == "browser_flow"]
        self.assertEqual([p["state"] for p in steps], ["error", "error", "ok"])
        self.assertEqual(url_before_valid_flow, ["https://example.org/start"])
        self.assertEqual(browser.clicks, [])
        self.assertEqual(browser.scroll, 300)
        self.assertEqual(json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())["state"], "done")
        self.assertEqual(len(calls), 6)  # open, scroll and wait were ONE model decision
    def test_generic_plan_progress_is_durable_and_requires_seen_evidence(self):
        browser = FakeBrowser()
        browser.url = "https://example.org/page"
        browser.text = "Die Überschrift ist sichtbar, mit weiteren Details."
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port)
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        i = {"n": 0}

        def reply(body):
            i["n"] += 1
            if i["n"] == 1:
                return {"tool_calls": [{"name": "goal_plan", "arguments": {"steps": [
                    {"id": "s1", "action": "Seite lesen", "done_when": "Titel sichtbar"}]}}]}
            if i["n"] == 2:
                return {"tool_calls": [{"name": "goal_step", "arguments": {"id": "s1", "state": "done",
                    "url": browser.url, "quote": "Die Überschrift"}}]}
            if i["n"] == 3:
                return {"tool_calls": [{"name": "browser_look", "arguments": {}}]}
            if i["n"] == 4:
                return {"tool_calls": [{"name": "goal_step", "arguments": {"id": "s1", "state": "done",
                    "url": browser.url, "quote": "Die Überschrift"}}]}
            return "Nächster Schritt folgt."

        r.lena.reply = reply
        r.say("@Lena Prüfe https://example.org/page")
        r.idle()
        path = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json"
        before = json.loads(path.read_text())
        self.assertEqual(before["plan"][0]["state"], "done")
        steps = [p for m in r.messages() if m["from"] == "lena" for p in m.get("parts") or []
                 if p["k"] == "step" and p["tool"] == "goal_step"]
        self.assertEqual([p["state"] for p in steps], ["error", "ok"])
        r.stop()
        r.start()
        self.assertEqual(json.loads(path.read_text())["plan"][0]["state"], "done")
    def test_independent_peer_review_is_read_only_and_persisted(self):
        r = Room(MAG_ROOM_PEER_REVIEW_SECONDS="40")
        self.addCleanup(r.close)
        r.noah.reply = lambda body: "Einzelne Anforderungen sind noch nicht belegt. Bitte die Quelle prüfen."
        seen = {"calls": 0}

        def lena(body):
            seen["calls"] += 1
            if seen["calls"] == 1:
                return {"tool_calls": [{"name": "goal_peer_review", "arguments": {
                    "summary": "Der Auftrag ist erledigt.",
                    "question": "Welche Anforderungen sind unbelegt?"}}]}
            return "Ich prüfe die verbleibenden Anforderungen."

        r.lena.reply = lena
        r.say("@Lena Prüfe https://example.org/seite")
        r.idle()
        reviews = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(reviews["state"], "active")
        self.assertEqual(reviews["peer_reviews"][0]["by"], "noah")
        self.assertIn("nicht belegt", reviews["peer_reviews"][0]["review"])
        calls = r.noah.chats()
        self.assertEqual(len(calls), 1)
        self.assertNotIn("tools", calls[0])
        self.assertIn("Welche Anforderungen sind unbelegt", calls[0]["messages"][1]["content"])
        self.assertEqual(calls[0]["messages"][0]["role"], "system")
        self.assertFalse(any(m["from"] == "noah" for m in r.messages()))



    def test_a_browser_goal_blocks_for_a_login_and_leaves_the_other_bot_out(self):
        browser = FakeBrowser()
        r = Room(MAG_ROOM_BROWSER_ADDR="127.0.0.1:%d" % browser.port, MAG_ROOM_VISION="lena")
        self.addCleanup(r.close)
        self.addCleanup(browser.close)
        n = {"i": 0}

        def work(body):
            i = n["i"]
            n["i"] += 1
            if i == 0:
                return {"content": "", "tool_calls": [{"name": "browser_open", "arguments": {"url": "https://www.amazon.de"}}]}
            return "Ich tippe weiter, Satz %d." % i

        r.lena.reply = work
        r.noah.reply = lambda body: "Darf ich das kaufen?"
        r.say("@Lena Nenne fuer mich zwei Schrumpfschlauch transparent auf Amazon.de")
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "active")
        self.assertIn("Abschluss noch nicht verifiziert", " ".join(m.get("text", "") for m in r.messages() if m["from"] == "lena"))
        before = len(r.noah.chats())
        r.say("Und?")
        r.idle()
        r.say("Noah du nicht")
        r.idle()
        self.assertEqual(len(r.noah.chats()), before)
        self.assertEqual(json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())["state"], "active")
        # Explicit chat to Noah is not the task: his acting tool is refused, and no consent card.
        r.noah.reply = lambda body: {"content": "Darf ich das kaufen und bestellen?", "tool_calls": [{"name": "shell", "arguments": {"command": "echo nein"}}]}
        r.say("@Noah was meinst du?")
        r.idle()
        noah_steps = [p for m in r.messages() if m["from"] == "noah" for p in m.get("parts") or [] if p["k"] == "step"]
        self.assertTrue(noah_steps and "erledigt diese Aufgabe schon" in noah_steps[-1]["output"])
        self.assertFalse(any(p.get("approval") for p in noah_steps))
        # An explicit new task for him still runs.
        r.noah.reply = lambda body: {"content": "", "tool_calls": [{"name": "browser_open", "arguments": {"url": "https://example.com"}}]}
        r.say("@Noah öffne https://example.com")
        r.idle()
        opened = [p for m in r.messages() if m["from"] == "noah" for p in m.get("parts") or [] if p["k"] == "step" and p["tool"] == "browser_open"]
        self.assertTrue(opened)
        self.assertNotIn("erledigt diese Aufgabe schon", opened[-1]["output"])
        self.assertIn("keine eigene VM", opened[-1]["output"])
        # A login wall ends only through goal_blocked and the card, not a made-up result.
        browser.password = True

        checks = {"n": 0}

        def blocked(body):
            checks["n"] += 1
            if checks["n"] == 2:
                return {"content": "", "tool_calls": [{"name": "browser_look", "arguments": {}}]}
            return {"content": "", "tool_calls": [{"name": "goal_blocked", "arguments": {"reason": "Die Seite will eine Anmeldung.", "kind": "login"}}]}

        r.lena.reply = blocked
        r.say("@Lena melde mich bei Amazon an")

        def card():
            for m in r.messages():
                for p in m.get("parts") or []:
                    if p.get("approval"):
                        return p["approval"]
            return None

        r.wait(lambda: card(), "login card")
        blocked_steps = [p for m in r.messages() if m["from"] == "lena" for p in m.get("parts") or [] if p["k"] == "step" and p["tool"] == "goal_blocked"]
        self.assertGreaterEqual(len(blocked_steps), 2)
        self.assertEqual(blocked_steps[0]["state"], "error")
        self.assertIn("gesehenen Seite", blocked_steps[0]["output"])
        self.assertEqual(card()["action"], "login")
        self.assertEqual(r.api("POST", "/api/approval/" + card()["id"], {"decision": "cancel"})[0], 200)
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal["state"], "blocked")
        said = " ".join(m.get("text", "") for m in r.messages() if m["from"] == "lena")
        self.assertNotIn("bestellt", said)
        # Trash ends it.
        self.assertEqual(r.api("POST", "/api/room/clear", {})[0], 200)
        r.idle()
        goal = json.loads((pathlib.Path(r.env["MAG_ROOM_STATE"]) / "goals" / "lena.json").read_text())
        self.assertEqual(goal, {}, "room clear removes the complete old goal")

    def test_whois_and_csrf_guard(self):
        r = self.room
        req = urllib.request.Request(r.url("/_internal/tailscale/whois"), headers={"X-Mag-Remote": "192.0.2.1:34567"})
        self.assertEqual(r.api("GET", "/_internal/tailscale/whois", headers={"X-Mag-Remote": "192.0.2.1:34567"})[0], 403)
        # A state-changing request without the page token or same-origin marker is refused.
        req = urllib.request.Request(r.url("/api/stop"), data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(err.exception.code, 403)
        err.exception.close()
        with urllib.request.urlopen(r.url("/"), timeout=5) as res:
            page = res.read().decode()
        self.assertIn('name="mag-token" content="', page)
        self.assertEqual(r.api("GET", "/readyz")[1]["ok"], True)

    def test_share_menu_target(self):
        # MAG is a target of the system's share menu; the service worker takes
        # the post. One that reaches the room is not read and not refused as a
        # cross-site post: the page is asked to say it, and nothing is stored.
        r = self.room
        status, manifest = r.api("GET", "/manifest.webmanifest")
        self.assertEqual(status, 200)
        target = manifest["share_target"]
        self.assertEqual((target["action"], target["method"], target["enctype"]), ("/share", "POST", "multipart/form-data"))
        self.assertEqual(target["params"]["files"][0]["name"], "files")
        self.assertIn("*/*", target["params"]["files"][0]["accept"])
        before = len(r.messages())
        conn = http.client.HTTPConnection("127.0.0.1", r.port, timeout=10)
        conn.request("POST", "/share", body=b"--x\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nhallo\r\n--x--\r\n",
                     headers={"Content-Type": "multipart/form-data; boundary=x", "Origin": "null", "Sec-Fetch-Site": "none"})
        res = conn.getresponse()
        res.read()
        conn.close()
        self.assertEqual((res.status, res.getheader("Location")), (303, "/?share=lost"))
        self.assertEqual(len(r.messages()), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
