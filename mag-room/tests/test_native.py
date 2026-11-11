#!/usr/bin/env python3
"""Room contract with scripted models (fake_llama.py): idle autonomy,
addressing, bounded bot dialogue and PASS, cancellation, live steps,
single-owner tool continuation, the trash button and message deletion.
The fixture proves the room's logic; it does not prove that a real model, a
real VM or the deployed browser work."""
import base64
import http.client
import json
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


class Room:
    """One magroom process with two scripted model servers."""

    def __init__(self, **env):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="magroom-"))
        self.lena, self.noah, self.idol, self.scene = FakeLlama("Lena"), FakeLlama("Noah"), FakeIdol(), FakeScene()
        self.port = free_port()
        self.events = []
        # Nothing here may reach the real host: no sudo, a temp dir as "VM",
        # no firewall helper, no model supervisor, no Idol or scene worker.
        self.env = dict(os.environ, MAG_ROOM_STATE=str(self.tmp / "state"), MAG_ROOM_WEB=str(ROOT / "mag-room/web"),
                        MAG_ROOM_PORT=str(self.port), MAG_ROOM_BRIDGE_PORT="0", MAG_ROOM_GATEWAY="direct",
                        MAG_LLM_PORT_LENA=str(self.lena.port), MAG_LLM_PORT_NOAH=str(self.noah.port),
                        MAG_MODELS_STATUS=str(self.tmp / "no-status.json"), MAG_MODELS_CONTROL=str(self.tmp / "control"),
                        MAG_IDOL_URL="http://127.0.0.1:%d" % self.idol.port, MAG_SCENE_URL="http://127.0.0.1:%d" % self.scene.port,
                        MAG_TEST_ALLOW_LOCAL_OWNER="1", MAG_ROOM_VERSION="test",
                        MAG_ROOM_NO_SUDO="1", MAG_ROOM_GUEST_RUN=str(HERE / "fake_guest_run"),
                        MAG_ROOM_AGENT_CTL="/bin/false", MAG_ROOM_NETMODE="/bin/true",
                        FAKE_GUEST_HOME=str(self.tmp / "guests"), MAG_ROOM_GUEST_HOME=str(self.tmp / "guests" / "agent1"),
                        MAG_BOTS_WWW=str(self.tmp / "www"), MAG_TTS_DIR=str(self.tmp / "tts"),
                        MAG_ROOM_BRAVE_KEY="", BRAVE_API_KEY="")
        self.env.update({k: str(v) for k, v in env.items()})
        (self.tmp / "control").mkdir()
        (self.tmp / "state").mkdir()
        # Lena has a (fake) VM, Noah is chat-only.
        (self.tmp / "state" / "agents.json").write_text(json.dumps([{"name": "Lena", "vm": "agent1"}, {"name": "Noah", "vm": ""}]))
        self.proc = None
        self.start()

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
        raise AssertionError("timeout waiting for " + what)

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
        self.assertIn("Chris: Hi beide", r.lena.chats()[0]["messages"][-1]["content"])
        self.assertIn("Noah: Hallo von Noah.", r.lena.chats()[1]["messages"][-1]["content"])
        time.sleep(1.0)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), 4)

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
        self.assertIn("Chris an Lena: @Lena wie geht es dir?", noah)
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

    def test_speaker_labels_are_plain_and_copied_tags_are_cleaned(self):
        r = self.room
        r.lena.reply = lambda body: "Hallo [Chris], [Noah · Di 10.11. 01:51] meinte das auch. Siehe [Doku](https://example.org)."
        r.say("@Lena hallo")
        r.idle()
        # A bracketed speaker tag the model copies never reaches the chat; links stay links.
        self.assertEqual(r.texts("lena"), ["Hallo Chris, Noah meinte das auch. Siehe [Doku](https://example.org)."])
        sent = r.lena.chats()[0]["messages"]
        self.assertRegex(sent[-1]["content"], r"^\(\w\w \d\d\.\d\d\. \d\d:\d\d\)\nChris: @Lena hallo$")
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
        r.wait(lambda: any(m.get("reason") == "bot" for m in r.messages()), "a bot round")
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
        r.say("@Lena noch ein Befehl")
        r.idle()
        last = [m for m in r.messages() if m["from"] == "lena"][-1]
        self.assertEqual((last["parts"][0]["state"], last["parts"][0]["exit"]), ("error", 3))
        self.assertEqual(r.lena.chats()[-1]["messages"][-1]["content"], "exit=3\nhallo\n")

    def test_finished_html_becomes_a_live_page_on_the_bots_site(self):
        r = self.room
        home = r.tmp / "guests" / "agent1"
        home.mkdir(parents=True)
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
        self.assertIn("Veröffentlicht: https://lena.bot.stardenburg.de/demo.html", r.lena.chats()[-1]["messages"][-1]["content"])
        self.assertIn("demo.html", (home / "published.txt").read_text())
        # Screenshots in the chat are gone; the room may frame the bots' sites and nothing else.
        self.assertNotIn("browser_screenshot", [t["function"]["name"] for t in r.lena.chats()[-1]["tools"]])
        with urllib.request.urlopen(r.url("/"), timeout=5) as res:
            policy = res.headers["Content-Security-Policy"]
            self.assertIsNone(res.headers["Cross-Origin-Embedder-Policy"])
        self.assertIn("frame-src 'self' https://*.bot.stardenburg.de;", policy)

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
        self.assertEqual([m["role"] for m in sent], ["system", "user"])
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
        self.assertIn("Auf diese Bitte hin hast du schon eine Szene gebaut", steps.pop()["output"])
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
        note = ('Chris: @Lena Der Fuchs springt über den Gnom\n[Chris schreibt das zur 3D-Szene "Fuchs besucht Gnom": Er möchte eine neue Fassung mit denselben Figuren. '
                "So wurde sie mit scene_3d gebaut: " + json.dumps(recipe, ensure_ascii=False, separators=(",", ":")) +
                " Baue die neue mit scene_3d: dieselben actors, die actions nach seinem Wunsch.]")
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
        self.assertIn("Chris: @Lena lass ihn tanzen\n[Chris schreibt das zum 3D-Modell %s: Er möchte es animiert sehen. Sieh mit models_3d nach, welche Figuren es zeigt, "
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
            getattr(r, bot).reply = lambda body: next(replies)
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
        self.assertTrue(all(parts[0]["type"] == "text" and parts[0]["text"].endswith("Chris: Was ist auf dem Bild?\n") for parts in seen), seen)
        described = [parts for parts in seen if "Beschreibe das Bild tasse.png" in text_of(parts)]
        self.assertEqual(len(described), 1)
        # ... asked with the picture itself right in front of the question, not by its name alone.
        self.assertEqual((described[0][-2]["type"], described[0][-2]["image_url"]["url"]), ("image_url", placed(described[0], "tasse.png")))
        self.assertIn("Beschreibe das Bild tasse.png, das direkt vor dieser Notiz steht, und nur dieses", described[0][-1]["text"])
        answered = [parts for parts in seen if "Beschreibe das Bild" not in text_of(parts)]
        self.assertIn("Chris: Was ist auf dem Bild?", text_of(answered[0]))
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
        self.assertIn("Foto zu 3D: 3D-Modell aus dem Foto figur.png (Qualität schnell) ist fertig", told)
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
        self.assertIn("Chris an Noah: @Noah wann kommt die Lieferung?", bundle[0]["text"])     # other people's words first ...
        self.assertTrue(bundle[0]["text"].endswith("Chris: Was seht ihr da\n"))                 # ... then the question, the picture,
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
        roles = [m["role"] for m in r.noah.chats()[before]["messages"]]
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

    def test_voices_for_reading_aloud_come_from_their_own_folder(self):
        r = self.room

        def get(path):
            try:
                with urllib.request.urlopen(r.url(path), timeout=10) as res:
                    return res.status, res.headers.get("Cache-Control"), res.headers.get("Content-Type"), res.read()
            except urllib.error.HTTPError as err:
                with err:
                    return err.code, None, None, b""
        # Not installed: the page is told, and reads with the voice of the device.
        self.assertEqual((r.state()["features"]["tts"], get("/tts/config.json")[0]), (False, 404))
        # Installed: a folder named after its content (kept by the browser for good) and a config that says which.
        voices = r.tmp / "tts" / "v-0123456789ab"
        voices.mkdir(parents=True)
        (voices / "tts-worker.js").write_text("// worker")
        (voices / "stimme.onnx").write_bytes(b"\x08\x07onnx")
        (r.tmp / "tts" / "config.json").write_text(json.dumps({"dir": "v-0123456789ab", "voices": [{"id": "thorsten", "model": "stimme.onnx"}]}))
        self.assertTrue(r.state()["features"]["tts"])
        code, cache, kind, body = get("/tts/config.json")
        self.assertEqual((code, cache, json.loads(body)["dir"]), (200, "no-store", "v-0123456789ab"))
        self.assertEqual(get("/tts/v-0123456789ab/tts-worker.js"), (200, "public, max-age=31536000, immutable", "text/javascript; charset=utf-8", b"// worker"))
        self.assertEqual(get("/tts/v-0123456789ab/stimme.onnx")[2:], ("application/octet-stream", b"\x08\x07onnx"))
        # Nothing but that folder.
        for path in ("/tts/../state/bots.json", "/tts/%2e%2e/state/bots.json", "/tts//etc/passwd", "/tts/v-0123456789ab/fehlt.js"):
            self.assertEqual(get(path)[0], 404, path)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
