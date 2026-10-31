"""Stop, user priority and queue contract of the room scheduler.

Runs the real native room against a fake OpenAI-compatible model that can
stay silent (like llama/ik while it evaluates a long prompt) and records when
the room closes the connection. llama.cpp/ik_llama abort the running decode
when the client disconnects, so "the room closed the socket" is the
server-side cancel this suite proves.
"""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import http.server, json, os, pathlib, select, socket, subprocess, tempfile, threading, time, unittest, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT / 'build/magroom'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Model(http.server.BaseHTTPRequestHandler):
    """Scripted chat model. A script is a list of pieces per request:
    str -> content delta, dict -> raw delta, {'_sleep': s}, {'_hold': s}
    (silent, watches for the client closing the socket)."""
    protocol_version = 'HTTP/1.1'
    calls = []          # (time, body)
    scripts = []        # per chat request, in order
    by_model = {}       # model id -> scripts, used first (parallel lanes)
    prewarm_script = None
    reject_missing_user = False
    fail_prewarms = False
    rejected = []
    disconnects = []    # (time, kind)
    active = 0
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = b'{"status":"ok"}' if self.path == '/health' else b'{"data":[{"id":"deepseek-v4-flash-0731"}]}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _chunk(self, payload):
        self.wfile.write(('%x\r\n' % len(payload)).encode() + payload + b'\r\n')
        self.wfile.flush()

    def _hold(self, seconds, kind):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            r, _, _ = select.select([self.connection], [], [], 0.05)
            if r:
                try:
                    if self.connection.recv(1, socket.MSG_PEEK) == b'':
                        Model.disconnects.append((time.monotonic(), kind))
                        return False
                except OSError:
                    Model.disconnects.append((time.monotonic(), kind))
                    return False
        return True

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if self.path.startswith('/slots'):
            data = b'{}'
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(data)
            return
        obj = json.loads(body)
        prewarm = obj.get('max_tokens') == 1 and obj.get('stream') is False
        kind = 'prewarm' if prewarm else 'turn'
        with Model.lock:
            Model.calls.append((time.monotonic(), obj, kind))
            missing_user = (Model.reject_missing_user and
                obj.get('model') == 'qwen3.8-flash-next-uncensored' and
                not any(m.get('role') == 'user' for m in obj.get('messages', [])))
            forced_failure = prewarm and Model.fail_prewarms
            if missing_user or forced_failure:
                Model.rejected.append((obj.get('model'), kind, 'missing user' if missing_user else 'forced prewarm failure'))
            if prewarm:
                script = Model.prewarm_script or []
            elif Model.by_model.get(obj.get('model')):
                script = Model.by_model[obj.get('model')].pop(0)
            else:
                script = Model.scripts.pop(0) if Model.scripts else ['Okay.']
            if not (missing_user or forced_failure):
                Model.active += 1
        if missing_user or forced_failure:
            data = b'Jinja Exception: No user query found in messages.'
            self.send_response(500)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        try:
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            for piece in script:
                if isinstance(piece, dict) and '_sleep' in piece:
                    time.sleep(piece['_sleep'])
                    continue
                if isinstance(piece, dict) and '_hold' in piece:
                    if not self._hold(piece['_hold'], kind):
                        return
                    continue
                delta = piece if isinstance(piece, dict) else {'content': piece}
                self._chunk(b'data: ' + json.dumps({'choices': [{'delta': delta}]}).encode() + b'\n\n')
            self._chunk(b'data: [DONE]\n\n')
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            Model.disconnects.append((time.monotonic(), kind))
        finally:
            with Model.lock:
                Model.active -= 1


def tool_call(name, args, cid='call_1'):
    return {'tool_calls': [{'index': 0, 'id': cid, 'function': {'name': name, 'arguments': json.dumps(args)}}]}


class Room:
    def __init__(self, agents, extra_env=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.state = self.dir / 'state'
        self.state.mkdir()
        (self.state / 'agents.json').write_text(json.dumps(agents))
        netmode = self.dir / 'netmode.sh'
        netmode.write_text('#!/bin/sh\nexit 0\n')
        netmode.chmod(0o755)
        self.model = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model)
        threading.Thread(target=self.model.serve_forever, daemon=True).start()
        self.ui, self.bridge = free_port(), free_port()
        env = dict(os.environ, MAG_OWNER_CANONICAL_LOGIN='chris@github', MAG_TEST_ALLOW_LOCAL_OWNER='1',
                   MAG_ROOM_STATE=str(self.state), MAG_ROOM_PORT=str(self.ui),
                   MAG_ROOM_BRIDGE_PORT=str(self.bridge), MAG_ROOM_BRIDGE_IP='127.0.0.1',
                   MAG_ROOM_BACKEND=f'http://127.0.0.1:{self.model.server_port}/v1/chat/completions',
                   MAG_ROOM_UI=str(ROOT / 'mag-room/web/index.html'), MAG_ROOM_STATIC=str(ROOT / 'mag-room/static'),
                   MAG_ROOM_MODEL_STATUS=str(self.dir / 'model-status.json'), MAG_NETMODE=str(netmode),
                   MAG_TEST_NO_MEMGIT='1', MAG_TEST_NO_CLEAR_BACKUP='1', MAG_ROOM_MCP_AUTO='0',
                   MAG_ROOM_PREWARM='0', MAG_ROOM_STAGGER='off')
        env.update(extra_env or {})
        self.log = open(self.dir / 'server.log', 'a')
        self.proc = subprocess.Popen([str(BIN)], env=env, stdout=self.log, stderr=self.log)
        for _ in range(200):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/healthz', timeout=2).read()
                break
            except Exception:
                time.sleep(.03)
        else:
            raise RuntimeError('room did not start')
        html = urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/', timeout=5).read().decode()
        self.token = html.split('name="mag-token" content="', 1)[1].split('"', 1)[0]

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.log.close()
        self.model.shutdown()
        self.model.server_close()
        self.tmp.cleanup()

    def api(self, path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Content-Type': 'application/json', 'X-MAG-Token': self.token} if data is not None or method else {}
        if method and data is None:
            headers = {'X-MAG-Token': self.token}
        req = urllib.request.Request(f'http://127.0.0.1:{self.ui}{path}', data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def state_(self):
        return self.api('/api/state')

    def until(self, pred, timeout=10, what='condition'):
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            last = self.state_()
            if pred(last):
                return last
            time.sleep(.05)
        raise AssertionError(f'{what} not reached; busy={last and last.get("busy")} '
                             f'room={[m.get("content") for m in (last or {}).get("room", [])][-6:]}')


def reset_model():
    Model.calls.clear()
    Model.scripts.clear()
    Model.by_model.clear()
    Model.disconnects.clear()
    Model.prewarm_script = None
    Model.reject_missing_user = False
    Model.fail_prewarms = False
    Model.rejected.clear()


def wait_calls(n, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if len(Model.calls) >= n:
            return
        time.sleep(.02)
    raise AssertionError(f'expected {n} model calls, got {len(Model.calls)}')


def contents(state):
    return [m.get('content') for m in state['room']]


class StopAndPriority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.exists():
            raise unittest.SkipTest('build/magroom missing')
        cls.room = Room([{'id': 'a', 'name': 'Alpha', 'vm': '', 'memory': []},
                         {'id': 'b', 'name': 'Beta', 'vm': '', 'memory': []}])

    @classmethod
    def tearDownClass(cls):
        cls.room.close()

    def setUp(self):
        self.room.until(lambda s: not s['busy'], what='idle before test')
        self.room.api('/api/queue', method='DELETE')
        self.room.api('/api/queue/resume', {})
        reset_model()

    def test_01_stop_during_prompt_evaluation_closes_the_model_request(self):
        Model.scripts.append([{'_hold': 30}, 'zu spät'])
        self.room.api('/api/message', {'text': 'Lange Aufgabe', 'bots': ['a']})
        wait_calls(1)
        time.sleep(.3)
        t0 = time.monotonic()
        r = self.room.api('/api/cancel', {})
        self.assertTrue(r['cancelled'])
        self.room.until(lambda s: not s['busy'], timeout=3, what='idle after stop')
        self.assertLess(time.monotonic() - t0, 2.0, 'Stop must end the turn at once')
        for _ in range(40):
            if Model.disconnects:
                break
            time.sleep(.05)
        self.assertTrue(Model.disconnects, 'room must close the model connection (server-side cancel)')
        self.assertLess(Model.disconnects[0][0] - t0, 1.5)
        s = self.room.state_()
        self.assertEqual(contents(s)[-1], 'Lange Aufgabe', 'a stop before any output leaves no bot message')
        self.assertFalse(s['queue_paused'])
        time.sleep(.5)
        self.assertEqual(len(Model.calls), 1, 'nothing restarts after Stop')

    def test_02_stop_during_streaming_keeps_partial_marked_stopped(self):
        Model.scripts += [['Teil eins. '] + [{'_sleep': .1}, 'mehr '] * 100, ['Queue geht weiter.']]
        self.room.api('/api/message', {'text': 'Erzähl', 'bots': ['a']})
        wait_calls(1)
        time.sleep(.6)
        self.room.api('/api/message', {'text': 'Weiter', 'bots': ['a']})
        self.room.api('/api/cancel', {})
        s = self.room.until(lambda s: not s['busy'] and not s['pending'] and any(m.get('interrupted') for m in s['room']),
                            timeout=4, what='partial after composer stop')
        self.assertEqual(s['pending'], [])
        self.assertFalse(s['queue_paused'])
        self.assertNotIn('Queue geht weiter.', contents(s))
        self.assertNotIn('Weiter', contents(s))
        last = [m for m in s['room'] if m.get('interrupted')][-1]
        self.assertTrue(last['content'].startswith('Teil eins.'))
        self.assertTrue(last.get('interrupted'))
        self.assertEqual(last.get('stopped'), 'user')
        # The fake model notices the closed socket on its next write.
        for _ in range(40):
            if Model.disconnects:
                break
            time.sleep(.05)
        self.assertTrue(Model.disconnects, 'streaming request closed')

    def test_02b_stop_during_reasoning_double_tap_keeps_one_queued_reply(self):
        # DeepSeek reasoning_content (8191) and Qwen <think> (8192) must not
        # become the answer. A second Stop while the first is settling must
        # not drop or duplicate the queued message.
        Model.scripts += [[
            {'reasoning_content': 'DeepSeek-Gedanke 8191 '},
            '<thi', 'nk>Qwen-Gedanke 8192</think>',
            'Teil ',
            {'_hold': 30},
        ], ['Queue bleibt.']]
        self.room.api('/api/message', {'text': 'Denk', 'bots': ['a']})
        wait_calls(1)
        time.sleep(.25)
        self.room.api('/api/message', {'text': 'Danach', 'bots': ['a']})
        t0 = time.monotonic()
        first = self.room.api('/api/cancel', {})
        second = self.room.api('/api/cancel', {})
        self.assertTrue(first['cancelled'])
        self.assertFalse(second['cancelled'], 'double tap is not a second stop')
        s = self.room.until(lambda s: not s['busy'] and not s['pending'] and any(m.get('interrupted') for m in s['room']),
                            timeout=4, what='partial after reasoning stop')
        self.assertLess(time.monotonic() - t0, 3)
        self.assertNotIn('Queue bleibt.', contents(s))
        self.assertNotIn('Danach', contents(s))
        self.assertEqual(s['pending'], [])
        for msg in s['room']:
            if msg.get('role') != 'assistant':
                continue
            text = msg.get('content') or ''
            self.assertNotIn('DeepSeek-Gedanke', text)
            self.assertNotIn('Qwen-Gedanke', text)
            self.assertNotIn('<think>', text)
            self.assertNotIn('</think>', text)
        stopped = [m for m in s['room'] if m.get('interrupted')]
        self.assertTrue(stopped, 'partial answer stays, marked stopped')
        self.assertEqual(stopped[-1].get('stopped'), 'user')
        self.assertTrue((stopped[-1].get('content') or '').startswith('Teil'))
        again = self.room.state_()
        self.assertEqual([m.get('content') for m in again['room']], contents(s))

    def test_03_new_message_after_stop_is_never_blocked_and_held_items_wait(self):
        Model.scripts += [[{'_hold': 30}], ['Antwort auf neu.'], ['Antwort auf alt.']]
        self.room.api('/api/message', {'text': 'Erste', 'bots': ['a']})
        wait_calls(1)
        q = self.room.api('/api/message', {'text': 'Gewartet', 'bots': ['a']})
        self.assertTrue(q['queued'])
        self.room.api('/api/queue/pause', {})
        r = self.room.api('/api/cancel', {})
        self.assertEqual(r['held'], 0)
        s = self.room.until(lambda s: not s['busy'], timeout=3)
        self.assertEqual(s['pending'], [])
        self.assertFalse(s['queue_paused'])
        time.sleep(0.4)
        self.assertEqual(len(Model.calls), 1, 'cleared queue items do not run by themselves')
        t0 = time.monotonic()
        r = self.room.api('/api/message', {'text': 'Neu', 'bots': ['a']})
        self.assertFalse(r.get('queued'), 'a new message after Stop runs immediately')
        s = self.room.until(lambda s: not s['busy'] and contents(s)[-1] == 'Antwort auf neu.')
        self.assertLess(time.monotonic() - t0, 3)
        self.assertNotIn('Gewartet', contents(s))
        self.assertFalse(s['queue_paused'])

    def test_04_message_during_tool_run_reaches_the_running_bot_at_the_next_step(self):
        Model.scripts += [[{'_sleep': 1.2}, tool_call('remember', {'fact': 'Schritt eins'})],
                          ['Beides erledigt.']]
        before = len([m for m in self.room.state_()['room']
                      if m.get('role') == 'assistant' and m.get('bot') == 'a'])
        self.room.api('/api/message', {'text': 'Aufgabe X', 'bots': ['a']})
        wait_calls(1)
        r = self.room.api('/api/message', {'text': 'Bitte auch Y', 'bots': ['a']})
        self.assertTrue(r['queued'])
        s = self.room.until(lambda s: not s['busy'] and contents(s)[-1] == 'Beides erledigt.')
        self.assertEqual(len(Model.calls), 2, 'no extra turn: the message was handled inside the run')
        second = Model.calls[1][1]['messages']
        self.assertEqual(second[-1]['role'], 'user')
        self.assertIn('Bitte auch Y', second[-1]['content'])
        self.assertIn('Vorrang', second[-1]['content'])
        self.assertEqual([m['role'] for m in second[-3:]], ['assistant', 'tool', 'user'])
        self.assertEqual(contents(s)[-3:], ['Aufgabe X', 'Bitte auch Y', 'Beides erledigt.'])
        new_rows = [m for m in s['room'] if m.get('role') == 'assistant'
                    and m.get('bot') == 'a'][before:]
        self.assertEqual(len(new_rows), 1, 'no extra empty assistant row')
        user = next(m for m in s['room'] if m.get('content') == 'Bitte auch Y')
        self.assertLessEqual(user['display_ms'], new_rows[0]['display_ms'])
        self.assertEqual(s['pending'], [])

    def test_05_running_bot_gives_way_to_a_message_for_another_bot_on_its_model(self):
        long_run = [[{'_sleep': .8}, tool_call('remember', {'fact': 'n%d' % i}, 'c%d' % i)] for i in range(20)]
        Model.scripts += long_run[:1]
        self.room.api('/api/message', {'text': 'Langer Lauf', 'bots': ['a']})
        wait_calls(1)
        Model.scripts += ['Beta antwortet.'], *long_run[1:]
        r = self.room.api('/api/message', {'text': 'Beta, kurz bitte', 'bots': ['b']})
        self.assertTrue(r['queued'])
        s = self.room.until(lambda s: 'Beta antwortet.' in contents(s), timeout=8, what='Beta answer')
        alpha = [m for m in s['room'] if m.get('bot') == 'a']
        self.assertTrue(alpha and alpha[-1].get('interrupted'), 'Alpha ended cleanly at its tool step')
        self.assertEqual(alpha[-1].get('stopped'), 'system')
        self.assertLessEqual(len([c for c in Model.calls if c[2] == 'turn']), 3)
        Model.scripts.clear()

    def test_06_queue_items_are_editable_removable_and_send_now_interrupts(self):
        Model.scripts += [[{'_hold': 30}], ['Sofort beantwortet.']]
        self.room.api('/api/message', {'text': 'Läuft', 'bots': ['a']})
        wait_calls(1)
        payload = {'text': 'eins', 'bots': ['a'], 'request_id': 'queue-six-once'}
        one = self.room.api('/api/message', payload)['item']['id']
        self.assertTrue(self.room.api('/api/message', payload)['duplicate'])
        two = self.room.api('/api/message', {'text': 'zwei', 'bots': ['a']})['item']['id']
        self.room.api('/api/queue/' + two + '/move', {'before': one})
        self.assertEqual([p['id'] for p in self.room.state_()['pending']], [two, one])
        self.room.api('/api/queue/' + one, {'text': 'eins (bearbeitet)'}, method='PUT')
        self.room.api('/api/queue/' + two, method='DELETE')
        self.assertEqual([p['text'] for p in self.room.state_()['pending']], ['eins (bearbeitet)'])
        t0 = time.monotonic()
        self.room.api('/api/queue/' + one + '/now', {})
        s = self.room.until(lambda s: not s['busy'] and contents(s)[-1] == 'Sofort beantwortet.', timeout=5)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertTrue(Model.disconnects, 'send now closes the running request')
        self.assertEqual(contents(s)[-2], 'eins (bearbeitet)')

    def test_06b_reasoning_channel_and_speaker_prefix_never_become_answer(self):
        # Fake backend fragments deliberately split tags/prefixes across SSE.
        Model.scripts += [[
            {'reasoning_content': 'Nur interne Analyse. '},
            'Chris tippt nur "Wie" – er scheint noch zu überlegen.\n\n',
            'Bot', '1:', ' Ich bin da.'
        ]]
        self.room.api('/api/message', {'text': 'Wie', 'bots': ['a']})
        s = self.room.until(lambda s: not s['busy'] and
                            contents(s)[-1] == 'Ich bin da.',
                            timeout=8, what='visible answer without leaked thinking')
        self.assertNotIn('Chris tippt', contents(s)[-1])
        self.assertNotIn('Nur interne Analyse', contents(s)[-1])
        self.assertNotIn('Bot1:', contents(s)[-1])
        assistant = s['room'][-1]
        self.assertEqual(assistant['role'], 'assistant')

    def test_06c_think_tags_do_not_escape_to_final_content(self):
        Model.scripts += [['<thi', 'nk>Geheime Gedanken</think>',
                           'Alpha:', ' Nur Antwort.']]
        self.room.api('/api/message', {'text': 'Antworte nur', 'bots': ['a']})
        s = self.room.until(lambda s: not s['busy'] and
                            contents(s)[-1] == 'Nur Antwort.',
                            timeout=8, what='think-tag-free content')
        self.assertNotIn('Geheime Gedanken', contents(s)[-1])

    def test_06d_prefix_strip_keeps_the_answer_and_clears_busy(self):
        Model.scripts += [['Alpha: Die Antwort bleibt.']]
        self.room.api('/api/message', {'text': 'Sag es', 'bots': ['a']})
        s = self.room.until(lambda s: not s['busy'] and contents(s)[-1] == 'Die Antwort bleibt.',
                            timeout=8, what='prefix does not empty the answer')
        self.assertNotIn('Alpha:', contents(s)[-1])
        self.assertNotIn('Es wurde keine Antwort', contents(s)[-1])
        self.assertNotIn('a', s.get('busy_bots') or [])

    def test_06e_both_reasoning_formats_stay_out_after_reload(self):
        # One request per production shape: reasoning_content is DeepSeek on
        # 8191, <think> is Qwen on 8192. The room stores neither in content.
        Model.scripts += [[
            {'reasoning_content': 'DeepSeek-Gedanke 8191'},
            'DeepSeek sagt Hallo.',
        ], [
            '<think>Qwen-Gedanke 8192</think>',
            'Qwen sagt Hallo.',
        ], [
            {'reasoning_content': 'Nur Gedanke'},
            '<think>immer noch nur Gedanke</think>',
        ]]
        self.room.api('/api/message', {'text': 'DeepSeek', 'bots': ['a']})
        s = self.room.until(lambda s: not s['busy'] and contents(s)[-1] == 'DeepSeek sagt Hallo.',
                            timeout=8, what='deepseek answer')
        self.room.api('/api/message', {'text': 'Qwen', 'bots': ['a']})
        s = self.room.until(lambda s: not s['busy'] and contents(s)[-1] == 'Qwen sagt Hallo.',
                            timeout=8, what='qwen answer')
        self.room.api('/api/message', {'text': 'Nur denken', 'bots': ['a']})
        s = self.room.until(lambda s: not s['busy'] and len(Model.calls) >= 3,
                            timeout=8, what='reasoning-only turn ends')
        self.assertEqual(contents(s)[-1], 'Nur denken')
        self.assertEqual(contents(s)[-2], 'Qwen sagt Hallo.')
        visible = [c or '' for c in contents(s)]
        self.assertIn('DeepSeek sagt Hallo.', visible)
        self.assertIn('Qwen sagt Hallo.', visible)
        blob = '\n'.join(visible)
        for banned in ('DeepSeek-Gedanke', 'Qwen-Gedanke', 'Nur Gedanke',
                       'immer noch nur Gedanke', '<think>', '</think>', 'Denkt nach'):
            self.assertNotIn(banned, blob)
        self.assertNotIn('Es wurde keine Antwort', blob)
        reloaded = self.room.state_()
        self.assertEqual([m.get('content') for m in reloaded['room']], contents(s))
        self.assertFalse(reloaded['busy'])

    def test_07_bot_stop_only_stops_that_bot(self):
        Model.scripts += [[{'_hold': 30}]]
        self.room.api('/api/message', {'text': 'Nur Alpha', 'bots': ['a']})
        wait_calls(1)
        self.assertTrue(self.room.api('/api/cancel', {'bot': 'a'})['cancelled'])
        self.room.until(lambda s: not s['busy'], timeout=3)
        self.assertFalse(self.room.api('/api/cancel', {'bot': 'a'})['cancelled'], 'idle bot stop is a no-op')


class TwoModels(unittest.TestCase):
    """Production shape: one bot per model server (NUMA 0 / NUMA 1)."""
    @classmethod
    def setUpClass(cls):
        if not BIN.exists():
            raise unittest.SkipTest('build/magroom missing')
        cls.room = Room([{'id': 'a', 'name': 'Alpha', 'vm': '', 'memory': [], 'model': 'deepseek-v4-flash-0731'},
                         {'id': 'b', 'name': 'Beta', 'vm': '', 'memory': [], 'model': 'deepseek-v4-flash-0731-iq3'}])

    @classmethod
    def tearDownClass(cls):
        cls.room.close()

    def test_both_bots_work_in_parallel_and_one_stop_ends_both(self):
        reset_model()
        Model.scripts += [[{'_hold': 30}], [{'_hold': 30}]]
        self.room.api('/api/message', {'text': 'Beide bitte', 'bots': ['a', 'b']})
        wait_calls(2, timeout=5)
        self.assertEqual({c[1]['model'] for c in Model.calls},
                         {'deepseek-v4-flash-0731', 'deepseek-v4-flash-0731-iq3'}, 'one request per model server')
        s = self.room.state_()
        self.assertEqual(set(s.get('busy_bots') or []), {'a', 'b'})
        t0 = time.monotonic()
        self.assertTrue(self.room.api('/api/cancel', {})['cancelled'])
        self.room.until(lambda s: not s['busy'], timeout=3, what='both idle')
        for _ in range(40):
            if len(Model.disconnects) >= 2:
                break
            time.sleep(.05)
        self.assertEqual(len(Model.disconnects), 2, 'both model requests closed')
        self.assertLess(max(t for t, _ in Model.disconnects) - t0, 1.5)


    def test_message_for_one_bot_reaches_it_while_the_other_keeps_working(self):
        reset_model()
        q4, second = 'deepseek-v4-flash-0731', 'deepseek-v4-flash-0731-iq3'
        # Alpha (Q4) works in tool steps; Beta (second model) is busy in a long prompt.
        Model.by_model[q4] = [[{'_sleep': 1.0}, tool_call('remember', {'fact': 'A1'})],
                              ['Alpha: erledigt, inklusive Nachtrag.']]
        Model.by_model[second] = [[{'_hold': 30}]]
        self.room.api('/api/message', {'text': 'Startet', 'bots': ['a', 'b']})
        wait_calls(2, timeout=5)
        r = self.room.api('/api/message', {'text': 'Nachtrag nur fuer Alpha', 'bots': ['a']})
        self.assertTrue(r['queued'], 'Alpha is busy, so the message is queued visibly')
        s = self.room.until(
            lambda s: 'erledigt, inklusive Nachtrag.' in contents(s) and 'a' not in (s.get('busy_bots') or []),
            timeout=8, what='Alpha answer with the late message')
        self.assertIn('b', s.get('busy_bots') or [], 'Beta keeps working, untouched')
        self.assertFalse(Model.disconnects, 'nobody was cancelled')
        alpha = [c[1] for c in Model.calls if c[1]['model'] == q4]
        self.assertEqual(len(alpha), 2, 'handed over inside the run, no extra turn')
        self.assertIn('Nachtrag nur fuer Alpha', alpha[-1]['messages'][-1]['content'])
        self.assertEqual(s['pending'], [])
        self.assertLess(contents(s).index('Nachtrag nur fuer Alpha'),
                        contents(s).index('erledigt, inklusive Nachtrag.'))
        self.room.api('/api/cancel', {})
        self.room.until(lambda s: not s['busy'], timeout=3)


@unittest.skipUnless(subprocess.run(['sudo', '-n', 'true'], capture_output=True).returncode == 0,
                     'needs sudo -n (tool commands run via sudo -n GUEST_RUN)')
class StopKillsTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.exists():
            raise unittest.SkipTest('build/magroom missing')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pidfile = pathlib.Path(cls.tmp.name) / 'tool.pid'
        guest = pathlib.Path(cls.tmp.name) / 'guest-run'
        # Only a real tool command (--stdin) hangs; status probes fail fast.
        guest.write_text('#!/bin/sh\n[ "$2" = --stdin ] || exit 1\necho $$ > %s\nexec sleep 60\n' % cls.pidfile)
        guest.chmod(0o755)
        os.chmod(cls.tmp.name, 0o755)
        cls.room = Room([{'id': 'a', 'name': 'Alpha', 'vm': 'agent9', 'memory': []}],
                        {'MAG_ROOM_GUEST_RUN': str(guest)})

    @classmethod
    def tearDownClass(cls):
        cls.room.close()
        cls.tmp.cleanup()

    def test_stop_kills_the_running_tool_command(self):
        reset_model()
        Model.scripts += [[tool_call('run_command', {'command': 'sleep 60'})], ['darf nicht kommen']]
        self.room.api('/api/message', {'text': 'Führe aus', 'bots': ['a']})
        for _ in range(200):
            if self.pidfile.exists() and self.pidfile.read_text().strip():
                break
            time.sleep(.05)
        pid = int(self.pidfile.read_text())
        t0 = time.monotonic()
        self.room.api('/api/cancel', {})
        s = self.room.until(lambda s: not s['busy'], timeout=5)
        self.assertLess(time.monotonic() - t0, 3)
        gone = False
        for _ in range(60):
            if subprocess.run(['sudo', '-n', 'kill', '-0', str(pid)], capture_output=True).returncode != 0:
                gone = True
                break
            time.sleep(.05)
        self.assertTrue(gone, 'tool process must be killed by Stop')
        self.assertEqual(len(Model.calls), 1, 'no model call after the stopped tool')
        last = s['room'][-1]
        self.assertTrue(last.get('interrupted'))
        self.assertEqual(last.get('tools', [{}])[-1].get('result'), 'abgebrochen')


class StopCancelsPrewarm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.exists():
            raise unittest.SkipTest('build/magroom missing')
        cls.room = Room([{'id': 'a', 'name': 'Alpha', 'vm': '', 'memory': []}],
                        {'MAG_ROOM_PREWARM': '1', 'MAG_ROOM_PREFIX_DISK': '0'})

    @classmethod
    def tearDownClass(cls):
        cls.room.close()

    def test_stop_cancels_idle_prewarm_and_keeps_it_off_until_the_next_message(self):
        reset_model()
        Model.prewarm_script = [{'_hold': 30}]
        Model.scripts.append(['Hallo.'])
        self.room.api('/api/message', {'text': 'Hi', 'bots': ['a']})
        for _ in range(200):
            if any(c[2] == 'prewarm' for c in Model.calls):
                break
            time.sleep(.05)
        self.assertTrue(any(c[2] == 'prewarm' for c in Model.calls), 'idle prewarm started')
        time.sleep(.2)
        t0 = time.monotonic()
        r = self.room.api('/api/cancel', {})
        self.assertTrue(r['cancelled'], 'Stop reports the cancelled prewarm')
        for _ in range(40):
            if any(k == 'prewarm' for _, k in Model.disconnects):
                break
            time.sleep(.05)
        self.assertTrue(any(k == 'prewarm' for _, k in Model.disconnects), 'prewarm request closed')
        self.assertLess(min(t for t, k in Model.disconnects if k == 'prewarm') - t0, 1.5)
        n = len(Model.calls)
        time.sleep(1.5)
        self.assertEqual(len(Model.calls), n, 'no new prewarm after Stop')
        Model.prewarm_script = []
        Model.scripts.append(['Wieder da.'])
        self.room.api('/api/message', {'text': 'Weiter', 'bots': ['a']})
        self.room.until(lambda s: not s['busy'] and contents(s)[-1] == 'Wieder da.')
        for _ in range(100):
            if sum(c[2] == 'prewarm' for c in Model.calls[n:]):
                break
            time.sleep(.05)
        self.assertTrue(sum(c[2] == 'prewarm' for c in Model.calls[n:]), 'prewarm resumes after a new message')



class QwenRoomTurns(unittest.TestCase):
    QWEN = 'qwen3.8-flash-next-uncensored'
    GLM = 'glm-5.3-flash'

    def setUp(self):
        reset_model()
        Model.reject_missing_user = True
        agents = [
            {'id': 'qwen', 'name': 'Qwen', 'vm': '', 'memory': [], 'slot': 1, 'numa': 0},
            {'id': 'glm', 'name': 'GLM-Q2', 'vm': '', 'memory': [], 'slot': 2, 'numa': 1},
        ]
        self.room = Room(agents, {
            'MAG_ROOM_MODEL_ID': self.GLM,
            'MAG_ROOM_MODEL_LABEL': 'GLM room',
            'MAG_ROOM_MODEL_PORTS': '8191:0,8192:1',
            'MAG_ROOM_MODEL_ID_0': self.QWEN,
            'MAG_ROOM_MODEL_LABEL_0': 'Qwen',
            'MAG_ROOM_MODEL_ID_1': self.GLM,
            'MAG_ROOM_MODEL_LABEL_1': 'GLM-Q2',
            'MAG_ROOM_PRESETS': str(ROOT / 'ops/room/presets.json'),
            'MAG_ROOM_PREWARM': '1',
            'MAG_ROOM_PREFIX_DISK': '0',
            'MAG_ROOM_SINGLE_BOT_VM': 'off',
        })

    def tearDown(self):
        self.room.close()

    def wait_qwen_prewarm(self):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if any(c[1].get('model') == self.QWEN and c[2] == 'prewarm' for c in Model.calls):
                return
            time.sleep(.03)
        self.fail('Qwen prewarm did not reach the fake template server')

    def qwen_turns(self):
        return [c for c in Model.calls if c[1].get('model') == self.QWEN and c[2] == 'turn']

    def assert_qwen_requests_end_in_user(self):
        requests = [c[1] for c in Model.calls if c[1].get('model') == self.QWEN]
        self.assertTrue(requests, 'expected Qwen requests')
        for req in requests:
            self.assertEqual(req['messages'][-1].get('role'), 'user', req['messages'])

    def test_qwen_hi_and_hi_beide_each_answer_once_with_template_valid_turns(self):
        self.wait_qwen_prewarm()
        self.room.until(lambda s: not s['busy'], timeout=8, what='idle after Qwen prewarm')
        Model.by_model[self.QWEN] = [['Hallo von Qwen.'], ['Qwen antwortet einmal.']]
        self.room.api('/api/message', {'text': 'Hi', 'bots': ['qwen']})
        s = self.room.until(lambda s: not s['busy'] and
            any(m.get('role') == 'assistant' and m.get('bot') == 'qwen' and
                m.get('content') == 'Hallo von Qwen.' for m in s['room']),
            timeout=8, what='Qwen answer to Hi')
        self.assertEqual(sum(m.get('role') == 'assistant' and m.get('bot') == 'qwen'
                             and m.get('content') == 'Hallo von Qwen.' for m in s['room']), 1)
        self.assertEqual(len(self.qwen_turns()), 1)

        self.room.api('/api/message', {'text': 'Hi beide'})
        s = self.room.until(lambda s: not s['busy'] and
            any(m.get('role') == 'assistant' and m.get('bot') == 'qwen' and
                m.get('content') == 'Qwen antwortet einmal.' for m in s['room']) and
            any(m.get('role') == 'assistant' and m.get('bot') == 'glm' for m in s['room']),
            timeout=8, what='both bots answer Hi beide')
        self.assertEqual(sum(m.get('role') == 'assistant' and m.get('bot') == 'qwen'
                             and m.get('content') == 'Qwen antwortet einmal.' for m in s['room']), 1)
        self.assertEqual(len(self.qwen_turns()), 2, 'one Qwen inference per addressed turn')
        self.assert_qwen_requests_end_in_user()
        self.assertEqual(Model.rejected, [], 'the Qwen fake Jinja template must never see a missing user query')

    def test_failed_prewarm_backoff_does_not_hold_real_qwen_turns(self):
        Model.fail_prewarms = True
        self.wait_qwen_prewarm()
        self.room.until(lambda s: not s['busy'], timeout=8, what='idle after rejected prewarm')
        time.sleep(2.4)
        self.assertEqual(sum(c[1].get('model') == self.QWEN and c[2] == 'prewarm'
                             for c in Model.calls), 1, 'failed prewarm must not retry every 2 seconds')
        Model.by_model[self.QWEN] = [['Trotzdem eine Antwort.']]
        self.room.api('/api/message', {'text': 'Hi', 'bots': ['qwen']})
        s = self.room.until(lambda s: not s['busy'] and
            any(m.get('role') == 'assistant' and m.get('bot') == 'qwen' and
                m.get('content') == 'Trotzdem eine Antwort.' for m in s['room']),
            timeout=8, what='real Qwen turn after failed prewarm')
        self.assertEqual(len(self.qwen_turns()), 1)
        self.assert_qwen_requests_end_in_user()


if __name__ == '__main__':
    unittest.main(verbosity=2)
