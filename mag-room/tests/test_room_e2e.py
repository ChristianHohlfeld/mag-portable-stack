"""Playwright: the agent-chat contract in a real browser.

One composer button that sends or stops (and Esc), an always-visible
scrollable queue (not in chat history), and inline status for bots without real tool
steps. One room menu and one settings dialog. Runs the native room
against the scripted fake model of test_room_control.py.
Screenshots: $MAG_E2E_SHOTS (default /tmp/mag-e2e-shots).
"""
# mag-test-timeout: 240
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(240)
import os, pathlib, re, sys, time, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_room_control import BIN, Model, Room, reset_model, tool_call, wait_calls  # noqa: E402

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:  # pragma: no cover
    sync_playwright = None

SHOTS = pathlib.Path(os.environ.get('MAG_E2E_SHOTS', '/tmp/mag-e2e-shots'))


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class RoomE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BIN.exists():
            raise unittest.SkipTest('build/magroom missing')
        SHOTS.mkdir(parents=True, exist_ok=True)
        # One bot keeps the scripted model replies in order; two-bot
        # priority is covered by test_room_control.py.
        cls.room = Room([{'id': 'a', 'name': 'Alpha', 'vm': '', 'memory': []}])
        cls.pw = sync_playwright().start()
        chrome = os.environ.get('MAG_CHROME')
        cls.browser = cls.pw.chromium.launch(**({'executable_path': chrome} if chrome else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.room.close()

    def setUp(self):
        self.room.until(lambda s: not s['busy'], what='idle before test')
        self.room.api('/api/queue', method='DELETE')
        self.room.api('/api/queue/resume', {})
        reset_model()
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 900})
        self.page.goto(f'http://127.0.0.1:{self.room.ui}/')
        expect(self.page.locator('#connection')).to_contain_text('Verbunden', timeout=10000)

    def tearDown(self):
        self.page.close()

    def shot(self, name):
        self.page.screenshot(path=str(SHOTS / f'{name}.png'))

    def send(self, text):
        box = self.page.locator('#message')
        box.fill(text)
        box.press('Enter')

    def test_01_header_has_one_room_menu_and_one_settings_dialog(self):
        p = self.page
        self.assertEqual(p.locator('#left-panel >> text=Zusammenarbeit').count(), 0)
        self.assertEqual(p.locator('#left-panel #stop, #left-panel #autotalk, #left-panel #settings-section').count(), 0)
        # The old room menu is retained only for legacy event bindings; it
        # must not be an actionable part of the minimal worker-room UI.
        expect(p.locator('#room-menu-btn')).to_be_hidden()
        expect(p.locator('#room-menu')).to_be_hidden()
        for sel in ('#autotalk', '#offline', '#stop'):
            expect(p.locator('#room-menu ' + sel)).to_be_attached()
        p.click('#settings-open')
        d = p.locator('#settings-dialog')
        expect(d).to_be_visible()
        for sel in ('#language', '#theme-toggle', '#data-areas', '#area-room'):
            expect(d.locator(sel)).to_be_attached()
        self.shot('01-settings')
        p.click('#settings-close')
        expect(d).to_be_hidden()
        self.assertEqual(p.locator('#plus-web').count(), 0, 'offline only in the room menu')
        # One header: one status group, one room menu, one settings button.
        self.assertEqual(p.locator('#app-header .status-group .top-stat').count(), 2)
        self.assertEqual(p.locator('#app-header #proaktiv-row, #app-header #proaktiv').count(), 0)
        expect(p.locator('#room-line #proaktiv-row #proaktiv')).to_be_attached()
        expect(p.locator('#room-line #proaktiv-row')).to_contain_text('Proaktiv')
        self.assertNotIn('siehe Tooltip', p.content())
        self.assertEqual(p.locator('#app-header #room-menu-btn').count(), 1)
        self.assertEqual(p.locator('#app-header #settings-open').count(), 1)
        # One Stop: the composer button (the room menu's "Alles anhalten"
        # also pauses jobs); no stop buttons in messages or the sidebar.
        self.assertEqual(p.locator('.work-stop, #left-panel button:has-text("Stopp"), #left-panel #stop').count(), 0)

    def test_02_composer_button_stops_the_running_turn(self):
        p = self.page
        Model.scripts.append([{'_hold': 30}])
        self.send('Lange Aufgabe')
        wait_calls(1)
        stop = p.locator('#stop-run')
        expect(stop).to_be_visible(timeout=5000)
        expect(stop).to_have_attribute('aria-label', 'Stoppen')
        self.assertEqual(stop.inner_text().strip(), '')
        expect(p.locator('#send-button')).to_have_attribute('aria-label', 'Senden')
        expect(p.locator('.message.work-live .meta .inline-progress')).to_be_visible()
        self.assertEqual(p.locator('.message.work-live .run').count(), 0,
                         'reading alone must not allocate a card')
        self.shot('02-running')
        t0 = time.monotonic()
        stop.click()
        expect(stop).to_be_hidden(timeout=3000)
        self.assertLess(time.monotonic() - t0, 3)
        self.room.until(lambda s: not s['busy'], timeout=3)
        self.assertTrue(Model.disconnects, 'model request closed')
        p.reload()
        expect(p.locator('#connection')).to_contain_text('Verbunden', timeout=10000)
        expect(p.locator('#stop-run')).to_be_hidden()
        Model.scripts.append(['Fertig.'])
        self.send('kurz')
        expect(p.locator('.message.assistant .content').last).to_have_text('Fertig.', timeout=8000)
        expect(p.locator('#stop-run')).to_be_hidden()

    def test_02b_stop_is_immediate_on_phone_and_desktop(self):
        # One tap shows Stoppt… before the model thread unwinds. A second tap
        # does not send. Reasoning never appears in the live answer. No
        # contentless Denkt-nach card. 390x844 and a desktop window.
        for width, height in ((390, 844), (1280, 800)):
            reset_model()
            self.room.until(lambda s: not s['busy'], timeout=3)
            self.room.api('/api/queue', method='DELETE')
            self.room.api('/api/queue/resume', {})
            page = self.browser.new_page(viewport={'width': width, 'height': height})
            try:
                page.goto(f'http://127.0.0.1:{self.room.ui}/')
                expect(page.locator('#connection')).to_contain_text('Verbunden', timeout=10000)
                Model.scripts.append([
                    {'reasoning_content': 'Nicht zeigen 8191 '},
                    '<think>Nicht zeigen 8192</think>',
                    {'_hold': 30},
                ])
                page.locator('#message').fill('Halt bitte')
                page.locator('#message').press('Enter')
                wait_calls(1)
                time.sleep(.3)
                stop = page.locator('#stop-run')
                expect(stop).to_be_visible(timeout=5000)
                expect(stop).to_have_attribute('aria-label', 'Stoppen')
                self.assertEqual(stop.inner_text().strip(), '')
                live = page.locator('.message.assistant .content').last
                expect(live).to_be_attached()
                self.assertNotIn('Nicht zeigen', live.inner_text())
                self.assertEqual(page.locator('.work-steps', has_text='Denkt nach').count(), 0)
                if width == 390:
                    box = page.evaluate("""()=>{
                      const c=document.getElementById('conversation');
                      const r=c.getBoundingClientRect();
                      return {sw:document.documentElement.scrollWidth, iw:innerWidth, ch:r.height, ih:innerHeight};
                    }""")
                    self.assertLessEqual(box['sw'], box['iw'] + 1, 'mobile page overflows')
                    self.assertGreaterEqual(box['ch'], box['ih'] * 0.8, 'chat is under 80% of the viewport')
                t0 = time.monotonic()
                seen = page.evaluate("""()=>{
                  const b=document.getElementById('stop-run');
                  b.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true}));
                  b.click();
                  return {label:b.getAttribute('aria-label'), text:(b.textContent||'').trim()};
                }""")
                self.assertEqual(seen['label'], 'Stoppt…')
                self.assertEqual(seen['text'], '')
                self.assertLess(time.monotonic() - t0, 0.4, 'stop paints before the request returns')
                expect(stop).to_be_hidden(timeout=3000)
                self.room.until(lambda s: not s['busy'], timeout=3)
                self.assertTrue(Model.disconnects, 'llama stream closed without a service restart')
                text = '\n'.join(page.locator('.message.assistant .content').all_inner_texts())
                self.assertNotIn('Nicht zeigen', text)
                self.assertNotIn('<think>', text)
            finally:
                page.close()

    def test_02c_stop_stays_pending_through_delayed_ack(self):
        # A model can spend a minute reading context. A delayed cancellation
        # must not hide Stop or leave the user with a dead-looking button.
        p = self.page
        p.evaluate("""()=>{
          window.__stopPosts=0;
          window.__ackStop=()=>{};
          const oldFetch=window.fetch;
          window.fetch=async (url,options)=>{
            if(String(url).includes('/api/cancel')){
              window.__stopPosts++;
              await new Promise(resolve=>window.__ackStop=resolve);
              return new Response(JSON.stringify({cancelled:true}),
                {status:200,headers:{'Content-Type':'application/json'}});
            }
            return oldFetch(url,options);
          };
          state.busy=true;state.busy_bots=['a'];busyBots=new Set(['a']);
          streams.a={name:'Alpha',phase:'prompt',label:'Liest Verlauf',
            started:Date.now()-41000,lastEvent:Date.now(),text:''};
          syncComposerButton();
        }""")
        stop = p.locator('#stop-run')
        expect(stop).to_be_visible()
        stop.click()
        expect(stop).to_have_attribute('aria-label','Stoppt…')
        expect(stop).to_have_attribute('aria-busy','true')
        expect(stop).to_be_disabled()
        expect(stop).to_be_visible()
        self.assertEqual(p.evaluate('window.__stopPosts'),1)
        self.assertIn('stoppt',p.locator('#room-line .activity-status').inner_text().lower())
        p.wait_for_timeout(500)
        self.assertEqual(p.evaluate('window.__stopPosts'),1,
                         'duplicate Stop while one cancellation is pending')
        p.evaluate('window.__ackStop()')
        expect(stop).to_be_hidden(timeout=4000)
        self.assertFalse(p.evaluate('stopInFlight'))
        expect(stop).not_to_have_attribute('aria-busy','true')

    def test_02d_stop_visible_when_only_background_task_or_prefill_is_active(self):
        # Native task status and a reading stream must independently enable
        # the same Stop control, even if the older room busy flag is false.
        p=self.browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            p.wait_for_selector('#send-button',timeout=5000)
            p.evaluate("""()=>{
              state.busy=false;state.busy_bots=[];
              state.activities=[];busyBots.clear();streams={};
              state.tasks=[{id:'bg-1',status:'running'}];
              syncStopButton();
            }""")
            expect(p.locator('#stop-run')).to_be_visible()
            expect(p.locator('#stop-run')).to_be_enabled()
            p.evaluate("""()=>{
              state.tasks=[];streams.qwen={phase:'prompt',label:'Liest Verlauf',
                started:Date.now()-41000,lastEvent:Date.now(),text:''};
              syncStopButton();
            }""")
            expect(p.locator('#stop-run')).to_be_visible()
            expect(p.locator('#stop-run')).to_be_enabled()
            p.evaluate("""()=>{stopInFlight=true;syncStopButton()}""")
            expect(p.locator('#stop-run')).to_have_attribute('aria-label','Stoppt…')
            expect(p.locator('#stop-run')).to_have_attribute('aria-busy','true')
            expect(p.locator('#stop-run')).to_be_disabled()
            p.evaluate("""()=>{
              stopInFlight=false;streams={};state.tasks=[];syncStopButton();
            }""")
            expect(p.locator('#stop-run')).to_be_hidden()
        finally:
            p.close()

    def test_02d_browser_http_fallback_recovers_after_udp_stalls(self):
        # Regression: old startHttpLive stopped whenever frameLastAt was
        # nonzero, even if that successful frame was minutes old.
        p=self.page
        p.evaluate("""()=>{
          const old=window.fetch;
          window.__fallbackCalls=0;
          window.fetch=(url,options)=> {
            if(String(url).includes('/api/screen/')){
              window.__fallbackCalls++;
              return Promise.resolve(new Response('',{status:404}));
            }
            return old(url,options);
          };
          document.getElementById('live').checked=true;
          ++liveGeneration;stopHttpLive();
          frameLastAt=performance.now()-11000;
          startHttpLive(liveGeneration,'fakebot');
        }""")
        p.wait_for_function('window.__fallbackCalls>0',timeout=2500)
        self.assertFalse(p.evaluate('freshUdpFrame()'),
                         'an old frame must not disable HTTP fallback')
        p.evaluate("""()=>{
          frameLastAt=performance.now();
          stopHttpLive();
        }""")
        self.assertTrue(p.evaluate('freshUdpFrame()'))
        old=p.evaluate('window.__fallbackCalls')
        p.wait_for_timeout(1000)
        self.assertEqual(p.evaluate('window.__fallbackCalls'),old,
                         'restored UDP frames must stop HTTP polling')

    def test_03_escape_stops(self):
        p = self.page
        Model.scripts.append([{'_hold': 30}])
        self.send('Noch eine lange Aufgabe')
        wait_calls(1)
        expect(p.locator('#stop-run')).to_be_visible(timeout=5000)
        p.locator('#message').press('Escape')
        expect(p.locator('#stop-run')).to_be_hidden(timeout=3000)
        self.room.until(lambda s: not s['busy'], timeout=3, what='idle after Esc')

    def test_03b_stream_first_token_is_not_duplicate_assistant_card(self):
        # The first content token is saved immediately in ROOM. It must
        # REPLACE the live bubble, not show a second thought-only bubble.
        p = self.page
        p.evaluate("""()=>{
          const now=Date.now();
          state.room=[{id:'partial-one',role:'assistant',name:'Alpha',bot:'a',
                      content:'Ich',display_ms:now,ts:Math.floor(now/1000)}];
          streams={a:{name:'Alpha',text:'Ich',phase:'answer',label:'Antwortet',
                      displayMs:now,started:now,thinkingEnabled:true,
                      reasoning:'Kurz nachgedacht',steps:[],tools:[],
                      partialId:'partial-one'}};
          render();
        }""")
        self.assertEqual(p.locator('#conversation .message').count(), 1,
                         'streaming + durable first token must share a card')
        self.assertEqual(p.locator('#conversation .message[data-live-bot="a"]').count(), 1)
        p.evaluate("""()=>{
          streams.a.partialId='';
          render();
        }""")
        self.assertEqual(p.locator('#conversation .message').count(), 1,
                         'fallback matches exact turn display_ms, not content')
        p.evaluate("""()=>{
          delete streams.a;
          render();
        }""")
        self.assertEqual(p.locator('#conversation .message').count(), 1,
                         'after bot_done the saved message replaces live card')

    def test_04_queue_is_visible_editable_and_send_now_interrupts(self):
        p = self.page
        Model.scripts += [[{'_hold': 30}], ['Sofort beantwortet.']]
        self.send('Arbeite lange')
        wait_calls(1)
        expect(p.locator('#stop-run')).to_be_visible(timeout=5000)
        self.send('Zwischenfrage')
        chip = p.locator('#queue-chips .q-chip')
        expect(chip).to_have_count(1, timeout=5000)
        expect(p.locator('#queue-chips .q-toggle')).to_contain_text('1 wartet')
        expect(p.locator('#queue-chips .queue-items')).to_be_visible()
        expect(chip.locator('.q-text')).to_have_text('Zwischenfrage')
        self.assertEqual(p.locator('.message.user .content', has_text='Zwischenfrage').count(), 0,
                         'queued messages are absent from the conversation')
        chip.locator('button[title="Bearbeiten"]').click()
        edit = p.locator('#queue-chips .q-edit')
        edit.fill('Zwischenfrage (geändert)')
        edit.press('Enter')
        expect(chip.locator('.q-text')).to_have_text('Zwischenfrage (geändert)', timeout=5000)
        self.shot('04-queue')
        chip.locator('.q-now').click()
        expect(p.locator('.message.assistant .content').last).to_have_text('Sofort beantwortet.', timeout=8000)
        expect(p.locator('#queue-chips .q-chip')).to_have_count(0)
        users = p.locator('.message.user .content').all_inner_texts()
        self.assertEqual(users[-1], 'Zwischenfrage (geändert)')

    def test_04b_stale_now_reconciles_canonical_queue(self):
        p = self.page
        p.evaluate("""()=>{
          const revision=queueState.revision;
          renderQueue({pending:[{id:'already-claimed',text:'Bereits bearbeitet',
            client_id:'now-stale-id',sender:'Nutzer',login:'',bots:['test']}],
            paused:false,revision});
        }""")
        expect(p.locator('#queue-chips .q-chip')).to_be_visible()
        p.locator('#queue-chips .q-now').click()
        expect(p.locator('#queue-chips .q-chip')).to_have_count(0,timeout=5000)
        expect(p.locator('#notice')).to_contain_text('Warteschlange wurde aktualisiert.',timeout=5000)
        self.assertNotIn('Unbekannter Eintrag',p.locator('#notice').inner_text())
        self.assertEqual(len(self.room.api('/api/queue',method='GET')['pending']),0)

    def test_04c_accepted_send_remains_sent_if_refresh_fails(self):
        p=self.page
        p.evaluate("""()=>{
          window.__previousRefresh=refresh;
          refresh=async()=>{throw new Error('simulierter GET-Fehler')};
        }""")
        try:
            self.send('Senden mit verlorener Statusabfrage')
            expect(p.locator('#notice')).to_contain_text('Gesendet',timeout=7000)
            expect(p.locator('#message')).to_have_value('')
            self.assertEqual(p.evaluate("failedSends.size"),0)
        finally:
            p.evaluate('refresh=window.__previousRefresh')
        p.evaluate('refresh()')
        state=self.room.until(lambda s:any(m.get('content')=='Senden mit verlorener Statusabfrage'
                            for m in s['room']),timeout=10,what='acked send')
        self.assertEqual(len([m for m in state['room']
             if m.get('content')=='Senden mit verlorener Statusabfrage']),1)

    def test_04d_double_submit_is_one_request(self):
        p=self.page
        p.evaluate("""()=>{
          window.__sendCount=0;
          window.__finishSend=()=>{};
          const original=window.fetch;
          window.fetch=async (url,options)=>{
            if(String(url).includes('/api/message')){
              window.__sendCount++;
              const response=await original(url,options);
              await new Promise(resolve=>{window.__finishSend=resolve});
              return response;
            }
            return original(url,options);
          };
        }""")
        try:
            p.locator('#message').fill('Einmal trotz Doppelklick')
            p.locator('#message').press('Enter')
            p.wait_for_function('window.__sendCount===1')
            # Even a second form submit while HTTP acknowledgement is withheld
            # must never produce another network write.
            p.evaluate("document.getElementById('send-form').requestSubmit()")
            self.assertEqual(p.evaluate('window.__sendCount'),1)
        finally:
            p.evaluate("window.__finishSend()")
        self.room.until(lambda s:any(m.get('content')=='Einmal trotz Doppelklick'
                        for m in s['room']),timeout=10,what='single send')
        p.wait_for_function('composerSending===false',timeout=5000)
        self.assertEqual(p.evaluate('window.__sendCount'),1)

    def test_05_run_card_groups_status_thoughts_and_tools(self):
        p = self.page
        Model.scripts += [[{'reasoning_content': 'Ich prüfe erst das Gedächtnis. '},
                           tool_call('remember', {'fact': 'E2E'})],
                          [{'reasoning_content': 'Fertig geprüft. '}, 'Erledigt: gemerkt.']]
        self.send('Merk dir E2E')
        expect(p.locator('.message.assistant .content').last).to_have_text('Erledigt: gemerkt.', timeout=8000)
        msg = p.locator('.message.assistant').last
        run = msg.locator(':scope > .run')
        expect(run).to_have_count(1)
        expect(run.locator('.work-status')).to_contain_text('Fertig')
        expect(run.locator('.thinking .think-title')).to_have_text('Gedacht für')
        expect(run.locator('.tools-trace')).to_be_attached()
        order = run.evaluate("r=>[...r.children].map(c=>['work-status','thinking','work-steps','tools-trace'].find(k=>c.classList.contains(k))||c.className)")
        self.assertEqual(order, ['work-status', 'thinking', 'work-steps', 'tools-trace'])
        # Done: collapsed to its status line; the line expands everything.
        expect(run).to_have_class('run collapsed')
        expect(run.locator('.thinking')).to_be_visible()
        expect(run.locator('.think-body')).to_be_hidden()
        status = run.locator('.work-status')
        expect(status).to_have_attribute('aria-expanded', 'false')
        status.click()
        expect(status).to_have_attribute('aria-expanded', 'true')
        expect(run.locator('.thinking')).to_be_visible()
        expect(run.locator('.tools-trace')).to_be_visible()
        run.locator('.think-head').click()
        expect(run.locator('.think-body')).to_contain_text('Gedächtnis')
        status.press('Enter')
        expect(run.locator('.thinking')).to_be_visible()
        expect(run.locator('.think-body')).to_be_hidden()
        self.assertEqual(msg.locator('.work-stop').count(), 0, 'no stop button inside messages')
        self.shot('05-run-card')

    def test_05b_reasoning_without_tools_never_creates_a_card(self):
        p = self.page
        Model.scripts += [[{'reasoning_content': 'Ich überlege laut. '}, {'_sleep': .25}] * 4 + ['Antwort nach Denken.']]
        self.send('Denk kurz nach')
        live = p.locator('.message.work-live')
        expect(live.locator('.inline-progress')).to_be_visible(timeout=5000)
        self.assertEqual(live.locator(':scope > .run').count(), 0,
                         'reasoning-only activity is zero-info chrome')
        expect(p.locator('.message.assistant .content').last).to_have_text('Antwort nach Denken.', timeout=8000)
        done = p.locator('.message.assistant').last
        self.assertEqual(done.locator(':scope > .run, .work-steps, .thinking').count(), 0)
        self.shot('05b-no-thinking-noise')

    def test_05b2_no_meaningless_denkt_nach_step(self):
        p = self.page
        Model.scripts += [[{'reasoning_content': 'Kurz gedacht. '}, {'_sleep': .25}] * 4 + ['Fertig ohne Aufklappen.']]
        self.send('Antworte kurz')
        expect(p.locator('.message.work-live .inline-progress')).to_be_visible(timeout=5000)
        expect(p.locator('.message.assistant .content').last).to_have_text('Fertig ohne Aufklappen.', timeout=8000)
        self.assertEqual(p.locator('.message.assistant').last.locator('.run,.work-steps,.thinking').count(), 0)

    def test_05c_bot_actions_in_one_menu_and_connectors_in_settings(self):
        p = self.page
        card = p.locator('#agents .agent').first
        self.assertEqual(card.locator('.actions > button:not(.agent-more)').count(), 0, 'no loose action buttons')
        card.locator('.agent-more').click()
        menu = p.locator('.pop-menu')
        expect(menu).to_be_visible()
        expect(menu.get_by_role('menuitem')).to_have_text(['Erinnerung', 'Umbenennen', 'Entfernen'])
        p.keyboard.press('Escape')
        expect(menu).to_have_count(0)
        card.locator('.agent-more').click()
        p.get_by_role('menuitem', name='Erinnerung').click()
        expect(p.locator('#memory-dialog')).to_be_visible()
        p.keyboard.press('Escape')
        # "+" holds attachments and Thinking only; connectors are settings.
        p.click('#plus')
        self.assertEqual(p.locator('#plus-menu #plus-github, #plus-menu #plus-mcp').count(), 0)
        p.keyboard.press('Escape')
        p.click('#settings-open')
        expect(p.locator('#settings-dialog #plus-github')).to_be_visible()
        expect(p.locator('#settings-dialog #plus-mcp')).to_be_visible()
        self.shot('05c-settings-connectors')
        p.click('#settings-dialog #plus-mcp')
        expect(p.locator('#settings-dialog')).to_be_hidden()
        expect(p.locator('#mcp-dialog')).to_be_visible()

    def test_05d_pho_compress_is_next_to_thinking_and_persists(self):
        p = self.page
        p.click('#plus')
        toggle = p.locator('#plus-pho')
        expect(toggle).to_be_visible()
        expect(toggle).to_contain_text('PhO-Compress')
        expect(toggle).to_have_attribute('aria-checked', 'true')
        self.assertTrue(p.evaluate("document.querySelector('#plus-think').nextElementSibling.id==='plus-pho'"))
        try:
            toggle.click()
            expect(toggle).to_have_attribute('aria-checked', 'false')
            self.room.until(lambda s: all(a['pho_compress'] is False for a in s['agents']), what='PhO off saved')
            p.reload()
            expect(p.locator('#connection')).to_contain_text('Verbunden')
            p.click('#plus')
            expect(p.locator('#plus-pho')).to_have_attribute('aria-checked', 'false')
            p.locator('#plus-pho').click()
            expect(p.locator('#plus-pho')).to_have_attribute('aria-checked', 'true')
        finally:
            self.room.api('/api/bot-settings', {'scope': 'room', 'pho_compress': True}, method='PUT')

    def test_05e_compression_gain_sits_on_the_message(self):
        # The saving is one line on the compressed message. It is not a
        # percentage in the plus menu, the queue, or a second copy.
        for width, height, touch in ((390, 844, True), (1280, 800, False)):
            p = self.browser.new_page(viewport={'width': width, 'height': height},
                                      is_mobile=touch, has_touch=touch)
            try:
                p.goto(f'http://127.0.0.1:{self.room.ui}/')
                p.wait_for_selector('#send-button', timeout=3000)
                p.evaluate("""()=>{
                  state.room=[{id:'old',role:'user',name:'Du',content:'Alter Verlauf',
                    pho_before:12400,pho_after:3100,ts:1}];
                  state.agents[0].pho_compress=true;
                  render();
                  syncBotSwitches();
                }""")
                gain = p.locator('.message .pho-gain')
                expect(gain).to_have_count(1, timeout=3000)
                expect(gain).to_have_text('12.4k → 3.1k Tokens (−75%)', timeout=3000)
                box = p.evaluate("""()=>{
                  const g=document.querySelector('.message .pho-gain');
                  const msg=g.closest('.message');
                  const gb=g.getBoundingClientRect(), mb=msg.getBoundingClientRect();
                  const cs=getComputedStyle(g);
                  return {inside:g.parentElement===msg,
                    above:gb.bottom<=mb.bottom+1, left:gb.left>=mb.left-1,
                    right:gb.right<=mb.right+1, h:gb.height,
                    border:cs.borderTopWidth, bg:cs.backgroundColor,
                    copies:document.querySelectorAll('.pho-gain').length};
                }""")
                self.assertTrue(box['inside'])
                self.assertTrue(box['above'])
                self.assertTrue(box['left'] and box['right'])
                self.assertLessEqual(box['h'], 18)
                self.assertEqual(box['border'], '0px')
                self.assertEqual(box['copies'], 1)
                self.assertEqual(p.locator('#queue-chips .pho-gain').count(), 0)
                self.assertEqual(p.locator('.composer-wrap .pho-gain').count(), 0)
                p.click('#plus')
                expect(p.locator('#plus-pho-state')).to_have_text('an', timeout=3000)
                self.assertNotIn('%', p.locator('#plus-pho').inner_text())
            finally:
                p.close()

    def test_06_message_after_stop_runs_and_held_queue_waits(self):
        p = self.page
        Model.scripts += [[{'_hold': 30}], ['Neu beantwortet.'], ['Alt beantwortet.']]
        self.send('Erste')
        wait_calls(1)
        expect(p.locator('#stop-run')).to_be_visible(timeout=5000)
        self.send('Gewartet')
        expect(p.locator('#queue-chips .q-chip')).to_have_count(1, timeout=5000)
        expect(p.locator('#queue-chips .q-chip')).to_be_visible()
        p.locator('#queue-chips .q-btn', has_text='Queue pausieren').click()
        p.locator('#message').press('Escape')
        expect(p.locator('#queue-chips .q-chip')).to_have_count(0, timeout=5000)
        expect(p.locator('#stop-run')).to_be_hidden(timeout=3000)
        self.send('Neu')
        expect(p.locator('.message.assistant .content').last).to_have_text('Neu beantwortet.', timeout=8000)
        self.shot('06-cleared')
        expect(p.locator('#queue-chips .q-chip')).to_have_count(0)

    def test_06b_quiet_stream_has_zero_layout_shift_and_keeps_scroll_anchor(self):
        p=self.page
        self.room.api('/api/bot-settings',{'bots':['a'],'thinking':False},method='PUT')
        p.reload();expect(p.locator('#connection')).to_contain_text('Verbunden')
        Model.scripts += [[{'_sleep':1.5},'Hallo ',{'_sleep':.25},'Welt.']]
        self.send('Inline ohne Sprung')
        content=p.locator('.message.work-live > .content')
        expect(content).to_have_attribute('data-loading','1')
        expect(p.locator('.message.work-live > .run')).to_have_count(0)
        # Start after message insertion; monitor only status/stream/finalization.
        p.evaluate("""()=>{window.quietShifts=[];window.quietObserver=new PerformanceObserver(list=>{
          for(const e of list.getEntries())quietShifts.push({value:e.value,sources:e.sources.map(s=>({tag:s.node?.tagName,cls:s.node?.className,prev:s.previousRect.toJSON(),now:s.currentRect.toJSON(),chat:!!s.node?.closest?.('#conversation')}))})});
          quietObserver.observe({type:'layout-shift',buffered:false});
          window.quietNode=document.querySelector('.work-live');
          window.quietTop=quietNode.querySelector('.content').getBoundingClientRect().top;
        }""")
        for phase in ('read','prompt','answer'):
            p.evaluate("""phase=>events.onmessage({data:JSON.stringify({type:'step',bot:'a',phase,label:'Liest Verlauf',thinking_enabled:false})})""",phase)
            p.wait_for_timeout(60)
            self.assertLessEqual(abs(p.evaluate("quietNode.querySelector('.content').getBoundingClientRect().top-quietTop")),.5)
        expect(p.locator('.message.assistant > .content').last).to_have_text('Hallo Welt.',timeout=8000)
        p.wait_for_timeout(250)
        self.assertEqual(p.locator('.message.assistant').last.locator('.work-status,.thinking,.quiet-loading').count(),0)
        print('QUIET_SHIFTS',p.evaluate('quietShifts'),flush=True)
        self.shot('06b-inline-cls-debug')
        self.assertLessEqual(p.evaluate('quietShifts.reduce((a,b)=>a+b.value,0)'),1e-8,'status, first token and completion must have CLS=0')
        self.assertLessEqual(p.evaluate("(()=>{const c=document.querySelector('#conversation');return c.scrollHeight-c.clientHeight-c.scrollTop})()"),2)
        # An explicit upward scroll detaches; later streamed text must not pull
        # the reader back to the bottom. Natural answer growth is allowed.
        p.evaluate("""()=>{state.room=Array.from({length:24},(_,i)=>({id:'anchor'+i,role:'assistant',name:'Alpha',content:'Absatz '.repeat(60),ts:1700000000+i}));render()}""")
        p.wait_for_timeout(100)
        p.locator('#conversation').hover();p.mouse.wheel(0,-550);p.wait_for_timeout(150)
        top=p.evaluate("document.querySelector('#conversation').scrollTop")
        p.evaluate("""()=>{events.onmessage({data:JSON.stringify({type:'bot_start',bot:'a',name:'Alpha',thinking_enabled:false})});
          events.onmessage({data:JSON.stringify({type:'delta',bot:'a',text:'Neue Antwort '.repeat(100)})})}""")
        p.wait_for_timeout(150)
        self.assertLessEqual(abs(p.evaluate("document.querySelector('#conversation').scrollTop")-top),.5)
        self.shot('06b-inline-cls-zero')
        self.room.api('/api/bot-settings',{'bots':['a'],'thinking':True},method='PUT')

    def test_08a_labels_and_stored_reasoning_stay_out_of_the_answer(self):
        got = self.page.evaluate("""()=>{
          state.agents=[{id:'q',name:'BotBot'}];
          return {
            name: roomBotName({role:'assistant',name:'BotBot',bot:'q'}),
            text: shownText({role:'assistant',name:'Bot1',content:'Chris tippt nur "Wie".\\n\\nBot1: Ich bin da.'})
          };
        }""")
        self.assertEqual(got['name'], 'Qwen')
        self.assertEqual(got['text'], 'Ich bin da.')
        self.assertNotIn('Bot1:', got['text'])
        self.assertNotIn('Chris tippt', got['text'])

    def test_08_mobile_queue_does_not_push_below_last_message(self):
        # 390x844: pending text is always visible below the chat; it must
        # never overlay or insert itself into the conversation history.
        p = self.browser.new_page(viewport={'width':390,'height':844},
                                  is_mobile=True, has_touch=True)
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            expect(p.locator('#connection')).to_contain_text('Verbunden', timeout=10000)
            Model.scripts += [[{'_hold': 30}], ['Zweite Antwort']]
            p.locator('#message').fill('Mobile lange Aufgabe')
            p.locator('#message').press('Enter')
            wait_calls(1)
            expect(p.locator('.message.work-live .inline-progress')).to_be_visible(timeout=5000)
            self.assertEqual(p.locator('.message.work-live > .run').count(), 0)
            p.locator('#message').fill('Mobile Zwischenfrage')
            p.locator('#message').press('Enter')
            expect(p.locator('#queue-chips .q-toggle')).to_contain_text('1 wartet', timeout=5000)
            self.assertEqual(p.locator('.message.user .content',has_text='Mobile Zwischenfrage').count(),0)
            expect(p.locator('#queue-chips .q-chip')).to_be_visible()
            expect(p.locator('#queue-chips .q-text')).to_have_text('Mobile Zwischenfrage')
            self.assertFalse(p.locator('#bot-lanes').is_visible())
            height=p.locator('#queue-chips').bounding_box()['height']
            self.assertLessEqual(height,170,'pending list is capped to preserve the chat')
            self.assertEqual(p.evaluate("""()=>document.querySelector('#conversation .conversation-inner').lastElementChild.classList.contains('message')"""),True)
            self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth-innerWidth"),1)
            p.screenshot(path=str(SHOTS/'08-mobile-queue-compact.png'))
            last = p.locator('#conversation .message').last
            last_box = last.bounding_box()
            expect(p.locator('#queue-chips .q-chip')).to_be_visible(timeout=3000)
            expect(p.locator('#queue-chips')).to_have_class(re.compile(r'\bexpanded\b'), timeout=3000)
            place = p.evaluate("""()=>{
              const items=document.querySelector('#queue-items');
              const last=[...document.querySelectorAll('#conversation .message')].at(-1);
              const q=document.getElementById('queue-chips');
              const ib=items.getBoundingClientRect(), lb=last.getBoundingClientRect(), qb=q.getBoundingClientRect();
              const cover=!(lb.right<=qb.left||qb.right<=lb.left||lb.bottom<=qb.top||qb.bottom<=lb.top);
              return {position:getComputedStyle(items).position, cover, lastBottom:lb.bottom, queueTop:qb.top};
            }""")
            self.assertEqual(place['position'], 'static')
            self.assertFalse(place['cover'], 'expanded queue covers the last message')
            self.assertLessEqual(place['lastBottom'], place['queueTop'] + 1)
            p.locator('#conversation').click(position={'x': 20, 'y': 20})
            expect(p.locator('#queue-chips .queue-items')).to_be_visible(timeout=3000)
            expect(p.locator('#queue-chips .q-text')).to_have_text('Mobile Zwischenfrage')
        finally:
            self.room.api('/api/cancel', {})
            self.room.api('/api/queue', method='DELETE')
            p.close()

    def test_07_mobile_layout_has_no_overflowing_header(self):
        p = self.browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            expect(p.locator('#connection')).to_be_attached()
            p.wait_for_timeout(500)
            over = p.evaluate("document.querySelector('#app-header').scrollWidth-document.querySelector('#app-header').clientWidth")
            p.screenshot(path=str(SHOTS / '07-mobile.png'))
            self.assertLessEqual(over, 2, 'header fits on a phone')
            expect(p.locator('#room-menu-btn')).to_be_hidden()
            expect(p.locator('#room-menu')).to_be_hidden()
            # The header must reserve room for two worker names, not the
            # retired room menu. Bot and tool drawers remain reachable.
            # Drawers open from the header, over the chat, and close with Esc.
            self.assertEqual(p.locator('.rail-edge').count(), 0, 'no floating edge tabs')
            p.click('#toggle-agents')
            expect(p.locator('#left-panel')).to_be_visible()
            expect(p.locator('#toggle-agents')).to_be_visible()
            self.assertEqual(p.evaluate("getComputedStyle(document.querySelector('#left-panel')).position"), 'fixed')
            expect(p.locator('.main')).to_be_attached()
            p.screenshot(path=str(SHOTS / '07-mobile-drawer.png'))
            p.keyboard.press('Escape')
            expect(p.locator('#left-panel')).to_be_hidden()
        finally:
            p.close()


    def test_09_reset_opens_on_one_tap_and_banner_stays_one_line(self):
        # One tap/click opens Bot zurücksetzen. The same gesture must not
        # dismiss the dialog. The model line stays 22px and names both bots.
        for width, height, touch in ((390, 844, True), (1280, 800, False)):
            page = self.browser.new_page(
                viewport={'width': width, 'height': height},
                is_mobile=touch, has_touch=touch)
            try:
                page.goto(f'http://127.0.0.1:{self.room.ui}/')
                page.wait_for_selector('#send-button')
                page.evaluate("""()=>{
                  state.agents=[
                    {id:'ds',name:'DeepSeek',vm:'agent1',model:'deepseek-v4-flash-0731',slot:1},
                    {id:'qw',name:'Qwen',vm:'agent2',model:'qwen3.8-flash-next-uncensored',slot:2}
                  ];
                  state.model_instances=[
                    {slot:1,model:'deepseek-v4-flash-0731',label:'DeepSeek',state:'down'},
                    {slot:2,model:'qwen3.8-flash-next-uncensored',label:'Qwen',state:'ok'}
                  ];
                  state.busy=false; busyBots=new Set();
                  selected=new Set(['ds']);
                  renderData();
                  document.getElementById('settings-dialog').showModal();
                }""")
                btn = page.locator('#work-areas button', has_text='Bot zurücksetzen').first
                btn.scroll_into_view_if_needed()
                box = btn.bounding_box()
                if touch:
                    page.touchscreen.tap(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                else:
                    btn.click()
                modal = page.locator('#mag-modal')
                expect(modal).to_be_visible()
                expect(page.locator('#mag-modal-title')).to_have_text('Bot zurücksetzen')
                page.wait_for_timeout(300)
                expect(modal).to_be_visible()
                self.assertNotIn('Tooltip', page.locator('#mag-modal-text').inner_text())
                page.locator('#mag-modal-cancel').click()
                expect(modal).to_be_hidden()
                page.evaluate("""()=>{
                  document.getElementById('settings-dialog').close();
                  modelStatus({state:'offline',ready:false,label:'Modell-Backend nicht erreichbar',detail:'Port 8191'});
                }""")
                banner = page.locator('#model-banner')
                expect(banner).to_be_visible()
                text = banner.inner_text()
                self.assertIn('DeepSeek nicht erreichbar', text)
                self.assertIn('Qwen bereit', text)
                self.assertNotIn('Tooltip', text)
                self.assertNotIn('nicht verfügbar', text)
                h0 = banner.bounding_box()['height']
                self.assertLessEqual(h0, 24)
                wrap0 = page.locator('.composer-wrap').bounding_box()['height']
                banner.locator('.model-banner-line').click()
                self.assertIn('Modell-Backend nicht erreichbar', banner.inner_text())
                self.assertIn('Port 8191', banner.inner_text())
                self.assertEqual(banner.bounding_box()['height'], h0)
                self.assertEqual(page.locator('.composer-wrap').bounding_box()['height'], wrap0)
                page.locator('.model-banner-switch').click()
                self.assertEqual(page.evaluate('[...selected]'), ['qw'])
                page.evaluate("""()=>{
                  state.model_instances[0].state='busy';
                  busyBots=new Set(['ds']);
                  modelStatus({state:'prefilling',ready:false,label:'prefilling',detail:'slot 0'});
                }""")
                shown = banner.inner_text()
                self.assertIn('beschäftigt', shown)
                self.assertNotIn('nicht verfügbar', shown)
                self.assertNotIn('Tooltip', shown)
            finally:
                page.close()


    def test_10_toasts_share_the_slim_line(self):
        # Status, PhO, queue and toasts share the 22px line. Nothing covers
        # the last message on phone portrait, landscape, or desktop.
        for width, height, touch in ((390, 844, True), (844, 390, True), (1280, 800, False)):
            page = self.browser.new_page(
                viewport={'width': width, 'height': height},
                is_mobile=touch, has_touch=touch)
            try:
                page.goto(f'http://127.0.0.1:{self.room.ui}/')
                page.wait_for_selector('#send-button', timeout=3000)
                page.evaluate("""()=>{
                  state.room=[{id:'m1',role:'assistant',name:'Alpha',content:'Letzte Antwort',ts:1}];
                  render();
                  const banner=document.getElementById('model-banner');
                  banner.hidden=false;
                  banner.textContent='Modell beschäftigt';
                  showUndo('Nachricht gelöscht','undo-1');
                  notice('Bot info');
                  placeToasts();
                }""")
                page.wait_for_function("()=>document.getElementById('room-line')&&!document.getElementById('queue-chips').hidden", timeout=3000)
                info = page.evaluate("""()=>{
                  const line=document.getElementById('room-line');
                  const last=[...document.querySelectorAll('#conversation .message')].at(-1).getBoundingClientRect();
                  const sels=['#undo-toast','#notice','#room-line','#queue-chips'];
                  const boxes=Object.fromEntries(sels.map(s=>{
                    const el=document.querySelector(s);
                    const b=el.getBoundingClientRect();
                    return [s,{h:b.height,y:b.y,bottom:b.bottom,cover:!(last.right<=b.left||b.right<=last.left||last.bottom<=b.top||b.bottom<=last.top)}];
                  }));
                  return {lineH:line.getBoundingClientRect().height, lastBottom:last.bottom, boxes,
                    inLine:!!document.getElementById('undo-toast').closest('#room-line'),
                    undo:document.getElementById('undo-toast').innerText,
                    notice:document.getElementById('notice').innerText};
                }""")
                self.assertLessEqual(info['lineH'], 24)
                for sel, box in info['boxes'].items():
                    self.assertFalse(box['cover'], f'{sel} covers the last message at {width}x{height}')
                    self.assertLessEqual(info['lastBottom'], box['y'] + 1, sel)
                self.assertTrue(info['inLine'])
                self.assertIn('Nachricht gelöscht', info['undo'])
                self.assertEqual(info['notice'], 'Bot info')
            finally:
                page.close()


    def test_11_clear_feedback_and_no_silent_retries(self):
        # Destructive POSTs are never blindly retried after an ambiguous
        # gateway failure. The user sees progress BEFORE the request starts.
        p = self.browser.new_page(viewport={'width':390,'height':844},
                                  is_mobile=True,has_touch=True)
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            p.wait_for_selector('#send-button',timeout=3000)
            p.evaluate("""()=>{
              window.__clearPosts=0;
              window.__releaseClear=()=>{};
              const old=window.fetch;
              window.fetch=async function(url,opts){
                const u=String(url);
                if(u.includes('/api/room/clear')){
                  window.__clearPosts++;
                  await new Promise(resolve=>{window.__releaseClear=resolve});
                  return new Response(JSON.stringify({error:'Backend wurde neu gestartet'}),
                    {status:503,headers:{'Content-Type':'application/json'}});
                }
                if(u.includes('/api/state')&&window.__clearPosts){
                  const reply=await old(url,opts), data=await reply.json();
                  return new Response(JSON.stringify({...data,room:[],files:[],pending:[],busy:false}),
                    {status:200,headers:{'Content-Type':'application/json'}});
                }
                return old(url,opts);
              };
              state.busy=false;state.pending=[];busyBots=new Set();
              document.getElementById('reset').hidden=false;
              document.getElementById('settings-dialog').showModal();
            }""")
            p.locator('#reset').click(timeout=3000)
            expect(p.locator('#reset')).to_have_text('Raum wird geleert…',timeout=1500)
            self.assertEqual(p.locator('#reset').get_attribute('aria-busy'),'true')
            expect(p.locator('#room-link')).to_have_text('Raum wird geleert…')
            p.evaluate('window.__releaseClear()')
            p.wait_for_function('window.__clearPosts===1 && !clearInFlight',timeout=3000)
            self.assertEqual(p.evaluate('window.__clearPosts'),1)
            # The read-only verification sees the empty server room; the
            # uncertain state must resolve without another destructive POST.
            expect(p.locator('#notice')).to_contain_text('Raum ist leer – Serverstatus geprüft')
            expect(p.locator('#room-link')).to_be_hidden()
            expect(p.locator('#reset')).to_have_text('Raum leeren')
            p.wait_for_timeout(800)
            self.assertEqual(p.evaluate('window.__clearPosts'),1,
                             'never repeat destructive operation in background')
            p.evaluate("""()=>{
              state.room=[{id:'m1',role:'assistant',name:'Bot',content:'Alt',ts:1}];
              state.pending=[{id:'p1',input:'Alt'}];
              streams.test={text:'alt'};
              render();
              events.onmessage({data:JSON.stringify({type:'room_cleared'})});
            }""")
            self.assertEqual(p.evaluate('state.room.length'),0)
            self.assertEqual(p.evaluate('state.pending.length'),0)
            self.assertEqual(p.evaluate('Object.keys(streams).length'),0)
        finally:
            p.close()

    def test_11b_message_hover_does_not_open_emoji_menu(self):
        p = self.browser.new_page(viewport={'width':1280,'height':800})
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            p.wait_for_selector('#send-button',timeout=3000)
            p.evaluate("""()=>{
              state.room=[{id:'hover-probe',role:'assistant',name:'Bot',
                           content:'Text bleibt sichtbar',ts:1}];
              render();
            }""")
            msg=p.locator('#conversation .message').last
            msg.hover()
            p.wait_for_timeout(550)
            expect(p.locator('#msg-pop.open')).to_have_count(0)
            trigger=msg.locator('.message-action-trigger')
            expect(trigger).to_be_visible()
            trigger.click()
            expect(p.locator('#msg-pop.open')).to_be_visible()
            expect(p.locator('#msg-pop .pop-emoji')).to_be_hidden()
            expect(p.locator('#msg-pop .pop-grid')).to_be_hidden()
            p.locator('#msg-pop button',has_text='Reagieren').click()
            expect(p.locator('#msg-pop .pop-emoji')).to_be_visible()
            p.keyboard.press('Escape')
            expect(p.locator('#msg-pop.open')).to_have_count(0)
            self.assertIn('Text bleibt sichtbar',msg.inner_text())
        finally:
            p.close()


    def test_11c_webudp_stall_uses_visible_http_browser_without_reconnect_flicker(self):
        # A reverse proxy can serve HTTPS while direct UDP ICE never connects.
        # The real UI must not stay at "WebRTC/UDP · verbinde …" forever.
        p = self.browser.new_page(viewport={'width':1280,'height':800})
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            p.wait_for_selector('#browser-agent', state='attached', timeout=3000)
            p.evaluate(r"""()=>{
              window.__screenRequests=0;window.__offers=0;
              const nativeFetch=window.fetch.bind(window);
              window.fetch=(url,opts)=>{
                const path=String(url);
                if(path.includes('/api/browser-live/offer')){
                  ++window.__offers;
                  return new Promise(()=>{}); // deliberately stuck SDP
                }
                if(path.includes('/api/screen/')){
                  ++window.__screenRequests;
                  if(window.__screenRequests===1)
                    return Promise.resolve(new Response('',{status:502}));
                  return Promise.resolve(new Response(new Blob(['jpeg'],{type:'image/jpeg'}),
                    {status:200,headers:{'Content-Type':'image/jpeg'}}));
                }
                return nativeFetch(url,opts);
              };
              window.createImageBitmap=async()=>{
                const canvas=document.createElement('canvas');
                canvas.width=2;canvas.height=2;canvas.close=()=>{};
                return canvas;
              };
              class DeadPeer {
                constructor(){this.connectionState='connecting';this.iceGatheringState='complete'}
                createDataChannel(){return {readyState:'connecting'}}
                async createOffer(){return {type:'offer',sdp:'v=0'}}
                async setLocalDescription(v){this.localDescription=v}
                close(){this.connectionState='closed'}
              }
              window.RTCPeerConnection=DeadPeer;
              state.agents=[{id:'a',name:'DeepSeek V4 Flash',vm:'agent1'}];
              document.getElementById('browser-agent').replaceChildren(new Option('DeepSeek V4 Flash','a'));
              document.getElementById('browser-agent').value='a';
              document.getElementById('live').checked=true;
              connectLive();
            }""")
            p.wait_for_function("window.__screenRequests>=1",timeout=8000)
            expect(p.locator('#browser-status')).to_contain_text(
                'VM-Browser noch nicht erreichbar (HTTP 502)',timeout=3000)
            p.wait_for_function("window.__screenRequests>=2",timeout=8500)
            expect(p.locator('#browser-status')).to_contain_text(
                'Browser über HTTPS · agent1 · live',timeout=2500)
            expect(p.locator('#screen')).to_be_visible()
            p.wait_for_timeout(1300)
            self.assertEqual(p.evaluate('window.__offers'),1,
                             'functional HTTPS fallback must not cycle/restart WebRTC')
        finally:
            p.close()

    def test_12_phone_chat_keeps_most_of_the_viewport(self):
        # Idle, busy, and queued: the conversation owns about 80% of the
        # phone screen. Status remains compact, queued text is always
        # readable in a bounded area, and nothing covers the last message.
        p = self.browser.new_page(viewport={'width': 390, 'height': 844},
                                  is_mobile=True, has_touch=True)
        t0 = time.monotonic()
        def ms():
            left = 8 - (time.monotonic() - t0)
            self.assertGreater(left, 0, 'hard timeout')
            return max(1, int(left * 1000))
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            p.wait_for_selector('#send-button', timeout=ms())

            def share():
                return p.evaluate("""()=>{
                  const c=document.getElementById('conversation').getBoundingClientRect();
                  return c.height/innerHeight;
                }""")

            def uncovered():
                return p.evaluate("""()=>{
                  const last=[...document.querySelectorAll('#conversation .message')].at(-1);
                  if(!last)return true;
                  const lb=last.getBoundingClientRect();
                  const covers=[...document.querySelectorAll('#queue-chips,#room-line,#toast-stack,#undo-toast,#notice')]
                    .filter(el=>el&&!el.hidden&&el.getClientRects().length)
                    .map(el=>el.getBoundingClientRect())
                    .some(b=>!(lb.right<=b.left||b.right<=lb.left||lb.bottom<=b.top||b.bottom<=lb.top));
                  return !covers;
                }""")

            self.assertGreaterEqual(share(), 0.80, 'idle chat should own the phone')
            Model.scripts += [[{'_hold': 30}], ['Zweite']]
            p.locator('#message').fill('Viewport lange Aufgabe')
            p.locator('#message').press('Enter')
            wait_calls(1, timeout=max(0.1, 8 - (time.monotonic() - t0)))
            expect(p.locator('.message.work-live')).to_be_visible(timeout=ms())
            self.assertGreaterEqual(share(), 0.80, 'busy chat should still own the phone')
            self.assertTrue(uncovered())
            line = p.locator('#room-line')
            expect(line).to_be_visible(timeout=ms())
            self.assertLessEqual(line.bounding_box()['height'], 24)
            p.locator('#message').fill('Viewport-Zwischenfrage')
            p.locator('#message').press('Enter')
            expect(p.locator('#queue-chips .q-toggle')).to_contain_text('1 wartet', timeout=ms())
            expect(p.locator('#queue-chips')).to_have_class(re.compile(r'\bexpanded\b'))
            expect(p.locator('#queue-chips .queue-items')).to_be_visible()
            expect(p.locator('#queue-chips .q-text')).to_have_text('Viewport-Zwischenfrage')
            p.evaluate('()=>{render();paintRoomLine()}')
            p.wait_for_function("""()=>![...document.querySelectorAll('#conversation .message.user .content')].some(el=>el.textContent.includes('Viewport-Zwischenfrage'))""", timeout=ms())
            self.assertGreaterEqual(share(), 0.68, 'visible queue must preserve most of the phone for chat')
            self.assertTrue(uncovered())
            self.assertLessEqual(p.locator('#queue-chips .queue-head').bounding_box()['height'], 24)
            self.assertLess(time.monotonic() - t0, 8, 'hard timeout')
        finally:
            self.room.api('/api/cancel', {})
            self.room.api('/api/queue', method='DELETE')
            p.close()


    def test_13_bot_faces_keep_color_motion_and_phone_share(self):
        # Two roster slots, renamed in the client: creature shape and color
        # follow the slot, not the label. Idle blink, streaming bounce and
        # reduced motion are class states. Message bodies stay unframed.
        # The phone chat stays at least 80% tall. Dies at 12s.
        p = self.browser.new_page(viewport={'width': 390, 'height': 844},
                                  is_mobile=True, has_touch=True)
        t0 = time.monotonic()

        def ms():
            left = 12 - (time.monotonic() - t0)
            self.assertGreater(left, 0.4, 'hard timeout')
            return max(400, int(left * 1000))

        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            p.wait_for_function(
                "()=>document.getElementById('connection')&&document.getElementById('connection').textContent.includes('Verbunden')&&state.agents&&state.agents.length",
                timeout=ms())
            snap = p.evaluate(r"""()=>{
              refreshSeq=1e9;
              try{events.close()}catch(e){}
              document.documentElement.dataset.theme='dark';
              const you=state.you||{role:'owner',name:'Chris',login:''};
              state.you=you;
              state.agents=[
                {id:'ds',name:'Bot',slot:1,vm:'',model:'deepseek',color:'#8fd0ff'},
                {id:'qw',name:'Dot',slot:2,vm:'',model:'qwen',color:'#8fd0ff'}
              ];
              state.room=[
                {id:'u1',role:'user',name:you.name||'Chris',login:you.login||'',content:'Hallo von Chris',ts:1,display_ms:1000},
                {id:'m1',role:'assistant',name:'Bot',bot:'ds',content:'Erste Antwort',ts:2,display_ms:2000},
                {id:'m2',role:'assistant',name:'Dot',bot:'qw',content:'Zweite Antwort',ts:3,display_ms:3000}
              ];
              state.pending=[];
              busyBots=new Set();
              streams={};
              render();
              const rgb=s=>{const m=String(s).match(/[\d.]+/g);return m?m.slice(0,3).map(Number):[0,0,0]};
              const lin=v=>{v/=255;return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4)};
              const L=c=>{const [r,g,b]=rgb(c);return 0.2126*lin(r)+0.7152*lin(g)+0.0722*lin(b)};
              const contrast=(a,b)=>{const x=L(a),y=L(b),hi=Math.max(x,y),lo=Math.min(x,y);return (hi+0.05)/(lo+0.05)};
              const opaque=el=>{let n=el;while(n){const c=getComputedStyle(n).backgroundColor;const m=String(c).match(/[\d.]+/g);if(m&&(m.length<4||Number(m[3])>0.9))return c;n=n.parentElement}return getComputedStyle(document.body).backgroundColor};
              const msg=id=>document.querySelector('.message.assistant[data-bot="'+id+'"]');
              const nameOf=id=>getComputedStyle(msg(id).querySelector('strong')).color;
              const faceOf=id=>msg(id).querySelector('.bot-face');
              const dark={ds:nameOf('ds'),qw:nameOf('qw')};
              const bg=opaque(msg('ds').querySelector('strong'));
              const darkContrast={ds:contrast(dark.ds,bg),qw:contrast(dark.qw,bg)};
              state.agents[0].name='Nova';
              state.agents[1].name='Lumen';
              render();
              const renamed=nameOf('ds')===dark.ds&&nameOf('qw')===dark.qw;
              const shapes={ds:faceOf('ds').dataset.shape,qw:faceOf('qw').dataset.shape};
              const eyes=faceOf('ds').querySelectorAll('.bf-eye').length;
              const fills={ds:getComputedStyle(faceOf('ds').querySelector('.bf-shape')).fill,qw:getComputedStyle(faceOf('qw').querySelector('.bf-shape')).fill};
              const idle={ds:faceOf('ds').className,qw:faceOf('qw').className};
              const idleAnim=getComputedStyle(faceOf('ds').querySelector('.bf-body')).animationName;
              const blink=getComputedStyle(faceOf('ds').querySelector('.bf-eye')).animationName;
              const wash=getComputedStyle(faceOf('ds'),'::before').content;
              state.agents[0].slot=2;state.agents[1].slot=1;
              render();
              const swapped=nameOf('ds')===dark.qw&&nameOf('qw')===dark.ds;
              const swappedShapes={ds:faceOf('ds').dataset.shape,qw:faceOf('qw').dataset.shape};
              state.agents[0].slot=1;state.agents[1].slot=2;
              const saved=state.agents.map(a=>({...a}));
              state.agents.forEach(a=>{delete a.slot});
              render();
              const hashed=nameOf('ds')!==nameOf('qw')&&botToneIndex({id:'ds',name:'Zeta'})===botToneIndex({id:'ds',name:'Andere'});
              state.agents=saved;
              render();
              document.documentElement.dataset.theme='light';
              const lightBg=opaque(msg('ds').querySelector('strong'));
              const light={ds:nameOf('ds'),qw:nameOf('qw')};
              const lightContrast={ds:contrast(light.ds,lightBg),qw:contrast(light.qw,lightBg)};
              document.documentElement.dataset.theme='dark';
              const mine=document.querySelector('.message.user');
              const mineColor=getComputedStyle(mine.querySelector('strong')).color;
              const bodyColor=getComputedStyle(document.body).color;
              const box=faceOf('ds').getBoundingClientRect();
              const live=id=>{
                streams[id]={name:id,text:'',phase:'think',label:'Denkt nach',thinking:true,thinkingEnabled:false,started:Date.now(),displayMs:Date.now(),node:null,seq:0,frame:0,reasoning:'',tools:[],steps:[],error:'',hung:false};
              };
              live('ds');live('qw');
              busyBots=new Set(['ds','qw']);
              updateStream('ds',true);
              updateStream('qw',true);
              paintActivityStatus();
              const streamFace=document.querySelector('.message.work-live[data-bot="ds"] .bot-face');
              const streamAnim=streamFace?getComputedStyle(streamFace.querySelector('.bf-body')).animationName:'';
              const lookAnim=streamFace?getComputedStyle(streamFace.querySelector('.bf-eyes')).animationName:'';
              const dots=[...document.querySelectorAll('#room-line .bot-dot')].map(d=>getComputedStyle(d).backgroundColor);
              const dotEl=document.querySelector('#room-line .bot-dot.is-stream');
              const dotAnim=dotEl?getComputedStyle(dotEl).animationName:'none';
              const line=document.getElementById('room-line').getBoundingClientRect();
              const chat=document.getElementById('conversation').getBoundingClientRect();
              const accent=getComputedStyle(msg('ds').querySelector('.content')).boxShadow;
              const otherAccent=getComputedStyle(msg('qw').querySelector('.content')).boxShadow;
              faceSettle.set('ds',Date.now()+30000);
              delete streams.ds;delete streams.qw;
              busyBots=new Set();
              render();
              const settled=document.querySelector('.message.assistant[data-bot="ds"] .bot-face');
              const css=document.getElementById('bot-faces').textContent;
              return {
                dark,light,darkContrast,lightContrast,renamed,swapped,shapes,swappedShapes,eyes,fills,hashed,idle,idleAnim,blink,wash,
                mineClass:mine.className,
                mineFace:!!mine.querySelector('.bot-face'),
                mineNeutral:mineColor===bodyColor,
                box:{w:box.width,h:box.height},
                streamClass:streamFace?streamFace.className:'',
                streamAnim,lookAnim,dotAnim,
                dots,lineH:line.height,share:chat.height/innerHeight,
                accent,otherAccent,
                settleClass:settled?settled.className:'',
                roster:{
                  ds:getComputedStyle(document.querySelector('#agents .agent[data-bot="ds"] .bot-name')).color,
                  qw:getComputedStyle(document.querySelector('#agents .agent[data-bot="qw"] .bot-name')).color,
                  shape:document.querySelector('#agents .agent[data-bot="ds"] .bot-face').dataset.shape
                },
                stats:document.querySelectorAll('#app-header .status-group .top-stat').length,
                proaktiv:!!document.querySelector('#room-line #proaktiv'),
                field:!!document.getElementById('message'),
                lottie:/lottie/i.test(document.documentElement.innerHTML),
                gif:!!document.querySelector('.bot-face img'),
                external:/https?:/i.test(css),
                frame:/inset\s+2px|conic-gradient|bf-wash/.test(css)
              };
            }""")
            self.assertNotEqual(snap['dark']['ds'], snap['dark']['qw'])
            self.assertNotEqual(snap['light']['ds'], snap['light']['qw'])
            self.assertGreaterEqual(snap['darkContrast']['ds'], 4.5)
            self.assertGreaterEqual(snap['darkContrast']['qw'], 4.5)
            self.assertGreaterEqual(snap['lightContrast']['ds'], 4.5)
            self.assertGreaterEqual(snap['lightContrast']['qw'], 4.5)
            self.assertTrue(snap['renamed'], 'rename keeps the slot color')
            self.assertTrue(snap['swapped'], 'slot, not the stored color, picks the accent')
            self.assertEqual(snap['shapes'], {'ds': 'heart', 'qw': 'blob'})
            self.assertEqual(snap['swappedShapes'], {'ds': 'blob', 'qw': 'heart'})
            self.assertEqual(snap['eyes'], 2)
            self.assertNotEqual(snap['fills']['ds'], snap['fills']['qw'])
            self.assertNotIn('none', snap['fills']['ds'])
            self.assertTrue(snap['hashed'])
            self.assertIn('is-idle', snap['idle']['ds'])
            self.assertIn('is-idle', snap['idle']['qw'])
            self.assertEqual(snap['idleAnim'], 'bf-idle')
            self.assertEqual(snap['blink'], 'bf-blink')
            self.assertEqual(snap['wash'], 'none')
            self.assertNotIn('bot-accent', snap['mineClass'])
            self.assertIn('mine', snap['mineClass'])
            self.assertFalse(snap['mineFace'])
            self.assertTrue(snap['mineNeutral'])
            self.assertGreaterEqual(snap['box']['h'], 26)
            self.assertLessEqual(snap['box']['h'], 34)
            self.assertLessEqual(snap['box']['w'], 34)
            self.assertIn('is-stream', snap['streamClass'])
            self.assertIn('is-pulse', snap['streamClass'])
            self.assertEqual(snap['streamAnim'], 'bf-live')
            self.assertEqual(snap['lookAnim'], 'bf-look')
            self.assertEqual(snap['dotAnim'], 'bf-dot')
            self.assertEqual(len(snap['dots']), 2)
            self.assertNotEqual(snap['dots'][0], snap['dots'][1])
            self.assertLessEqual(snap['lineH'], 24)
            self.assertGreaterEqual(snap['share'], 0.80)
            self.assertEqual(snap['accent'], 'none')
            self.assertEqual(snap['otherAccent'], 'none')
            self.assertIn('is-settle', snap['settleClass'])
            self.assertNotIn('is-stream', snap['settleClass'])
            self.assertEqual(snap['roster']['ds'], snap['dark']['ds'])
            self.assertEqual(snap['roster']['qw'], snap['dark']['qw'])
            self.assertEqual(snap['roster']['shape'], 'heart')
            self.assertNotEqual(snap['roster']['ds'], snap['roster']['qw'])
            self.assertEqual(snap['stats'], 2)
            self.assertTrue(snap['proaktiv'])
            self.assertTrue(snap['field'])
            self.assertFalse(snap['lottie'])
            self.assertFalse(snap['gif'])
            self.assertFalse(snap['external'])
            self.assertFalse(snap['frame'])
            p.emulate_media(reduced_motion='reduce')
            reduced = p.evaluate(r"""()=>{
              streams.ds={name:'ds',text:'',phase:'think',label:'Denkt nach',thinking:true,thinkingEnabled:false,started:Date.now(),displayMs:Date.now(),node:null,seq:1,frame:0,reasoning:'',tools:[],steps:[],error:'',hung:false};
              busyBots=new Set(['ds']);
              paintActivityStatus();
              const face=document.querySelector('.message.assistant .bot-face');
              const body=getComputedStyle(face.querySelector('.bf-body')).animationName;
              const eye=getComputedStyle(face.querySelector('.bf-eye')).animationName;
              const dot=document.querySelector('.bot-dot');
              return {body,eye,dot:dot?getComputedStyle(dot).animationName:'missing'};
            }""")
            self.assertEqual(reduced['body'], 'none')
            self.assertEqual(reduced['eye'], 'none')
            self.assertEqual(reduced['dot'], 'none')
            self.assertLess(time.monotonic() - t0, 12, 'hard timeout')
        finally:
            p.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
