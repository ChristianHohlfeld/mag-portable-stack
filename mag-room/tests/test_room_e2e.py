"""Playwright: the agent-chat contract in a real browser.

One composer button that sends or stops (and Esc), an on-demand folded
queue (not in chat history), and inline status for bots without real tool
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
        expect(p.locator('#room-menu')).to_be_hidden()
        p.click('#room-menu-btn')
        expect(p.locator('#room-menu')).to_be_visible()
        for sel in ('#autotalk', '#offline', '#stop'):
            expect(p.locator('#room-menu ' + sel)).to_be_attached()
        self.shot('01-room-menu')
        p.keyboard.press('Escape')
        expect(p.locator('#room-menu')).to_be_hidden()
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
        self.assertEqual(p.locator('#app-header #selftalk-row, #app-header #selftalk').count(), 0)
        expect(p.locator('#room-line #selftalk-row #selftalk')).to_be_attached()
        expect(p.locator('#room-line #selftalk-row')).to_contain_text('Self-Talk')
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
        button = p.locator('#send-button')
        expect(button).to_have_attribute('data-mode', 'stop', timeout=5000)
        expect(p.locator('.message.work-live .meta .inline-progress')).to_be_visible()
        self.assertEqual(p.locator('.message.work-live .run').count(), 0,
                         'reading alone must not allocate a card')
        self.shot('02-running')
        t0 = time.monotonic()
        button.click()
        expect(button).to_have_attribute('data-mode', 'send', timeout=3000)
        self.assertLess(time.monotonic() - t0, 3)
        self.room.until(lambda s: not s['busy'], timeout=3)
        self.assertTrue(Model.disconnects, 'model request closed')

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
                button = page.locator('#send-button')
                expect(button).to_have_attribute('data-mode', 'stop', timeout=5000)
                live = page.locator('.message.assistant .content').last
                expect(live).to_be_attached()
                self.assertNotIn('Nicht zeigen', live.inner_text())
                self.assertEqual(page.locator('.work-steps', has_text='Denkt nach').count(), 0)
                t0 = time.monotonic()
                seen = page.evaluate("""()=>{
                  const b=document.getElementById('send-button');
                  b.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true}));
                  b.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true}));
                  return b.getAttribute('aria-label');
                }""")
                self.assertEqual(seen, 'Stoppt…')
                self.assertLess(time.monotonic() - t0, 0.4, 'stop paints before the request returns')
                expect(button).to_have_attribute('data-mode', 'send', timeout=3000)
                self.room.until(lambda s: not s['busy'], timeout=3)
                self.assertTrue(Model.disconnects, 'llama stream closed without a service restart')
                text = '\n'.join(page.locator('.message.assistant .content').all_inner_texts())
                self.assertNotIn('Nicht zeigen', text)
                self.assertNotIn('<think>', text)
            finally:
                page.close()

    def test_03_escape_stops(self):
        p = self.page
        Model.scripts.append([{'_hold': 30}])
        self.send('Noch eine lange Aufgabe')
        wait_calls(1)
        expect(p.locator('#send-button')).to_have_attribute('data-mode', 'stop', timeout=5000)
        p.locator('#message').press('Escape')
        self.room.until(lambda s: not s['busy'], timeout=3, what='idle after Esc')

    def test_04_queue_is_visible_editable_and_send_now_interrupts(self):
        p = self.page
        Model.scripts += [[{'_hold': 30}], ['Sofort beantwortet.']]
        self.send('Arbeite lange')
        wait_calls(1)
        expect(p.locator('#send-button')).to_have_attribute('data-mode', 'stop', timeout=5000)
        self.send('Zwischenfrage')
        chip = p.locator('#queue-chips .q-chip')
        expect(chip).to_have_count(1, timeout=5000)
        expect(p.locator('#queue-chips .q-toggle')).to_contain_text('1 wartet')
        expect(p.locator('#queue-chips .queue-items')).to_be_hidden()
        self.assertEqual(p.locator('.message.user .content', has_text='Zwischenfrage').count(), 0,
                         'queued messages are absent from the conversation')
        p.locator('#queue-chips .q-toggle').click()
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
        expect(p.locator('#send-button')).to_have_attribute('data-mode', 'stop', timeout=5000)
        self.send('Gewartet')
        expect(p.locator('#queue-chips .q-chip')).to_have_count(1, timeout=5000)
        p.locator('#queue-chips .q-toggle').click()
        p.locator('#queue-chips .q-btn', has_text='Queue pausieren').click()
        p.locator('#message').press('Escape')
        expect(p.locator('#queue-chips .q-chip.held')).to_have_count(1, timeout=5000)
        expect(p.locator('#queue-chips .q-toggle')).to_contain_text('angehalten')
        self.send('Neu')
        expect(p.locator('.message.assistant .content').last).to_have_text('Neu beantwortet.', timeout=8000)
        self.shot('06-held')
        p.locator('#queue-chips .q-btn', has_text='Fortsetzen').click()
        expect(p.locator('.message.assistant .content').last).to_have_text('Alt beantwortet.', timeout=8000)
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
        # 390x844: the last chat node stays a message. The folded queue uses
        # one 22px line; reply activity is an inline dot, not another panel.
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
            self.assertEqual(p.locator('.q-chip:visible').count(),0)
            self.assertFalse(p.locator('#bot-lanes').is_visible())
            height=p.locator('#queue-chips').bounding_box()['height']
            self.assertLessEqual(height,26,'status and queue fit in one compact line')
            self.assertEqual(p.evaluate("""()=>document.querySelector('#conversation .conversation-inner').lastElementChild.classList.contains('message')"""),True)
            self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth-innerWidth"),1)
            p.screenshot(path=str(SHOTS/'08-mobile-queue-compact.png'))
            last = p.locator('#conversation .message').last
            last_box = last.bounding_box()
            p.locator('#queue-chips .q-toggle').click()
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
            expect(p.locator('#queue-chips .queue-items')).to_be_hidden(timeout=3000)
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
            p.click('#room-menu-btn')
            expect(p.locator('#room-menu')).to_be_visible()
            box = p.locator('#room-menu').bounding_box()
            self.assertGreaterEqual(box['x'], 0)
            self.assertLessEqual(box['x'] + box['width'], 391)
            p.screenshot(path=str(SHOTS / '07-mobile-menu.png'))
            p.keyboard.press('Escape')
            expect(p.locator('#room-menu')).to_be_hidden()
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


    def test_11_clear_retries_through_a_restart(self):
        # A restart answers 503. The clear is kept and retried; the only
        # status is the slim line, and the test itself dies at 4s.
        p = self.browser.new_page(viewport={'width': 390, 'height': 844},
                                  is_mobile=True, has_touch=True)
        try:
            p.goto(f'http://127.0.0.1:{self.room.ui}/')
            p.wait_for_selector('#send-button', timeout=3000)
            p.evaluate("""()=>{
              const orig=window.fetch;
              window.__clearPosts=0;
              window.__roomRelink='';
              let stateFails=0, cleared=false;
              window.fetch=async function(url, opts){
                const u=String(url);
                if(u.includes('/api/room/clear')){
                  window.__clearPosts++;
                  if(window.__clearPosts<=2)
                    return new Response(JSON.stringify({error:'Raum nicht erreichbar'}),
                      {status:503,statusText:'Service Unavailable',
                       headers:{'Content-Type':'application/json'}});
                  cleared=true;
                }
                if(cleared && u.includes('/api/state') && stateFails<1){
                  stateFails++;
                  return new Response('down',{status:502,statusText:'Bad Gateway'});
                }
                return orig(url, opts);
              };
              state.busy=false; state.pending=[]; busyBots=new Set();
              ROOM_RETRY.base=800; ROOM_RETRY.cap=800; ROOM_RETRY.limit=4000;
              document.getElementById('reset').hidden=false;
              document.getElementById('settings-dialog').showModal();
            }""")
            p.locator('#reset').click(timeout=3000)
            p.wait_for_function("window.__roomRelink==='Verbinde neu…'", timeout=3000)
            link = p.locator('#room-link')
            expect(link).to_be_visible(timeout=3000)
            expect(link).to_have_text('Verbinde neu…', timeout=3000)
            place = p.evaluate("""()=>{
              const el=document.getElementById('room-link');
              const msg=document.getElementById('message');
              const eb=el.getBoundingClientRect(), mb=msg.getBoundingClientRect();
              return {inComposer:!!el.closest('.composer-wrap'),
                above:eb.bottom<=mb.top+1, h:eb.height,
                border:getComputedStyle(el).borderTopWidth};
            }""")
            self.assertTrue(place['inComposer'])
            self.assertTrue(place['above'])
            self.assertLessEqual(place['h'], 24)
            self.assertEqual(place['border'], '0px')
            p.wait_for_function('window.__clearPosts>=3', timeout=4000)
            expect(p.locator('#notice')).not_to_have_text('Raum nicht erreichbar', timeout=3000)
            expect(link).to_be_hidden(timeout=3000)
        finally:
            p.close()


    def test_12_phone_chat_keeps_most_of_the_viewport(self):
        # Idle, busy, and queued: the conversation owns about 80% of the
        # phone screen. Status/queue/PhO/toasts stay in one 22px line and
        # never cover the last message. The test itself dies at 8s.
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
            expect(p.locator('#queue-chips')).not_to_have_class(re.compile(r'\bexpanded\b'))
            expect(p.locator('#queue-chips .queue-items')).to_be_hidden()
            p.evaluate('()=>{render();paintRoomLine()}')
            p.wait_for_function("""()=>![...document.querySelectorAll('#conversation .message.user .content')].some(el=>el.textContent.includes('Viewport-Zwischenfrage'))""", timeout=ms())
            self.assertGreaterEqual(share(), 0.80, 'queued chat should still own the phone')
            self.assertTrue(uncovered())
            self.assertLessEqual(p.locator('#queue-chips .queue-head').bounding_box()['height'], 24)
            self.assertLess(time.monotonic() - t0, 8, 'hard timeout')
        finally:
            self.room.api('/api/cancel', {})
            self.room.api('/api/queue', method='DELETE')
            p.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
