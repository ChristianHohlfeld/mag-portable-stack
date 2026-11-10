#!/usr/bin/env python3
"""Room contract with scripted models (fake_llama.py): idle autonomy,
addressing, bounded bot dialogue and PASS, cancellation, live steps,
single-owner tool continuation, the trash button and message deletion.
The fixture proves the room's logic; it does not prove that a real model, a
real VM or the deployed browser work."""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from fake_idol import FakeIdol  # noqa: E402
from fake_llama import FakeLlama  # noqa: E402

BIN = pathlib.Path(os.environ.get("MAGROOM_BIN", ROOT / "build/magroom"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Room:
    """One magroom process with two scripted model servers."""

    def __init__(self, **env):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="magroom-"))
        self.lena, self.noah, self.idol = FakeLlama("Lena"), FakeLlama("Noah"), FakeIdol()
        self.port = free_port()
        self.events = []
        # Nothing here may reach the real host: no sudo, a temp dir as "VM",
        # no firewall helper, no model supervisor, no Idol worker.
        self.env = dict(os.environ, MAG_ROOM_STATE=str(self.tmp / "state"), MAG_ROOM_WEB=str(ROOT / "mag-room/web"),
                        MAG_ROOM_PORT=str(self.port), MAG_ROOM_BRIDGE_PORT="0", MAG_ROOM_GATEWAY="direct",
                        MAG_LLM_PORT_LENA=str(self.lena.port), MAG_LLM_PORT_NOAH=str(self.noah.port),
                        MAG_MODELS_STATUS=str(self.tmp / "no-status.json"), MAG_MODELS_CONTROL=str(self.tmp / "control"),
                        MAG_IDOL_URL="http://127.0.0.1:%d" % self.idol.port, MAG_TEST_ALLOW_LOCAL_OWNER="1", MAG_ROOM_VERSION="test",
                        MAG_ROOM_NO_SUDO="1", MAG_ROOM_GUEST_RUN=str(HERE / "fake_guest_run"),
                        MAG_ROOM_AGENT_CTL="/bin/false", MAG_ROOM_NETMODE="/bin/true",
                        FAKE_GUEST_HOME=str(self.tmp / "guests"), MAG_ROOM_GUEST_HOME=str(self.tmp / "guests" / "agent1"),
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
        self.assertIn("[Chris", r.lena.chats()[0]["messages"][-1]["content"])
        self.assertIn("[Noah] Hallo von Noah.", r.lena.chats()[1]["messages"][-1]["content"])
        time.sleep(1.0)
        self.assertEqual(len(r.lena.chats()) + len(r.noah.chats()), 4)

    def test_named_bot_answers_alone(self):
        r = self.room
        r.say("@Lena wie geht es dir?")
        r.idle()
        self.assertEqual([m["from"] for m in r.messages()], ["chris", "lena"])
        # Noah was not asked; he reads Lena's answer and may pass.
        self.assertEqual(len(r.lena.chats()), 1)
        self.assertEqual(len(r.noah.chats()), 1)

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
        self.assertEqual(lena["site"], "https://agent1.bot.stardenburg.de")
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
        self.assertIn("Veröffentlicht: https://agent1.bot.stardenburg.de/demo.html", r.lena.chats()[-1]["messages"][-1]["content"])
        self.assertIn("demo.html", (home / "published.txt").read_text())
        # Screenshots in the chat are gone; the room may frame the bots' sites and nothing else.
        self.assertNotIn("browser_screenshot", [t["function"]["name"] for t in r.lena.chats()[-1]["tools"]])
        with urllib.request.urlopen(r.url("/"), timeout=5) as res:
            policy = res.headers["Content-Security-Policy"]
            self.assertIsNone(res.headers["Cross-Origin-Embedder-Policy"])
        self.assertIn("frame-src 'self' https://*.bot.stardenburg.de;", policy)

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
        code, photo = r.api("POST", "/api/upload?name=foto.png", raw=b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        self.assertEqual((code, photo["kind"]), (201, "image"))
        code, out = r.api("POST", "/api/message", {"text": "", "attachments": [photo["file"]], "mode": "3d", "quality": "high"})
        self.assertEqual(code, 201)
        model_id = out["model3d"]
        r.wait(lambda: [m for m in r.messages() if m["id"] == model_id][0]["state"] == "done", "3D job done")
        messages = r.messages()
        self.assertEqual([m["from"] for m in messages], ["chris", "idol"])  # stored and ordered like any message
        result = messages[1]
        self.assertEqual((result["model3d"]["state"], result["model3d"]["quality"]), ("done", "high"))
        self.assertEqual(sorted(a["name"].split(".")[-1] for a in result["attachments"]), ["glb", "ply", "stl"])
        files = pathlib.Path(r.env["MAG_ROOM_STATE"]) / "files"
        stored = [files / a["file"] for a in result["attachments"]]
        self.assertTrue(all(p.exists() for p in stored))
        self.assertEqual(r.api("GET", "/api/file/" + result["attachments"][0]["file"])[1], b"glTF-fake")
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
