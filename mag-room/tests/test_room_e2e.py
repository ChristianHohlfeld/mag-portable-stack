"""Playwright: the agent-chat contract in a real browser.

One composer button that sends or stops (and Esc), a visible editable queue
with "Jetzt senden", one run card per bot answer (status, thoughts, steps,
tool calls), one room menu and one settings dialog. Runs the native room
against the scripted fake model of test_room_control.py.
Screenshots: $MAG_E2E_SHOTS (default /tmp/mag-e2e-shots).
"""
import os, pathlib, sys, time, unittest

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
        cls.browser = cls.pw.chromium.launch()

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

    def test_02_composer_button_stops_the_running_turn(self):
        p = self.page
        Model.scripts.append([{'_hold': 30}])
        self.send('Lange Aufgabe')
        wait_calls(1)
        button = p.locator('#send-button')
        expect(button).to_have_attribute('data-mode', 'stop', timeout=5000)
        expect(p.locator('.message.work-live .run .work-status')).to_be_visible()
        self.shot('02-running')
        t0 = time.monotonic()
        button.click()
        expect(button).to_have_attribute('data-mode', 'send', timeout=3000)
        self.assertLess(time.monotonic() - t0, 3)
        self.room.until(lambda s: not s['busy'], timeout=3)
        self.assertTrue(Model.disconnects, 'model request closed')

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
        expect(p.locator('#queue-chips .queue-head')).to_contain_text('nächsten Arbeitsschritt')
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
        expect(run.locator('.thinking .think-title')).to_have_text('Gedanken')
        expect(run.locator('.tools-trace')).to_be_attached()
        order = run.evaluate("r=>[...r.children].map(c=>['work-status','thinking','work-steps','tools-trace'].find(k=>c.classList.contains(k))||c.className)")
        self.assertEqual(order, ['work-status', 'thinking', 'work-steps', 'tools-trace'])
        # Done: collapsed to its status line; the line expands everything.
        expect(run).to_have_class('run collapsed')
        expect(run.locator('.thinking')).to_be_hidden()
        status = run.locator('.work-status')
        expect(status).to_have_attribute('aria-expanded', 'false')
        status.click()
        expect(status).to_have_attribute('aria-expanded', 'true')
        expect(run.locator('.thinking')).to_be_visible()
        expect(run.locator('.tools-trace')).to_be_visible()
        run.locator('.think-head').click()
        expect(run.locator('.think-body')).to_contain_text('Gedächtnis')
        status.press('Enter')
        expect(run.locator('.thinking')).to_be_hidden()
        self.assertEqual(msg.locator('.work-stop').count(), 0, 'no stop button inside messages')
        self.shot('05-run-card')

    def test_05b_live_run_is_open_and_thoughts_only_answer_gets_a_header(self):
        p = self.page
        Model.scripts += [[{'reasoning_content': 'Ich überlege laut. '}, {'_sleep': .25}] * 10 + ['Antwort nach Denken.']]
        self.send('Denk kurz nach')
        live = p.locator('.message.work-live > .run')
        expect(live).to_be_visible(timeout=5000)
        expect(live.locator('.work-status')).to_have_attribute('aria-expanded', 'true')
        expect(live.locator('.thinking .think-title')).to_have_text('Denkt nach', timeout=5000)
        self.shot('05b-live-thinking')
        expect(p.locator('.message.assistant .content').last).to_have_text('Antwort nach Denken.', timeout=8000)
        done = p.locator('.message.assistant').last.locator(':scope > .run')
        expect(done.locator('.work-status')).to_contain_text('Fertig')
        expect(done).to_have_class('run collapsed')

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

    def test_06_message_after_stop_runs_and_held_queue_waits(self):
        p = self.page
        Model.scripts += [[{'_hold': 30}], ['Neu beantwortet.'], ['Alt beantwortet.']]
        self.send('Erste')
        wait_calls(1)
        expect(p.locator('#send-button')).to_have_attribute('data-mode', 'stop', timeout=5000)
        self.send('Gewartet')
        expect(p.locator('#queue-chips .q-chip')).to_have_count(1, timeout=5000)
        p.locator('#message').press('Escape')
        expect(p.locator('#queue-chips .q-chip.held')).to_have_count(1, timeout=5000)
        expect(p.locator('#queue-chips .queue-head')).to_contain_text('Fortsetzen')
        self.send('Neu')
        expect(p.locator('.message.assistant .content').last).to_have_text('Neu beantwortet.', timeout=8000)
        self.shot('06-held')
        p.locator('#queue-chips .q-btn', has_text='Fortsetzen').click()
        expect(p.locator('.message.assistant .content').last).to_have_text('Alt beantwortet.', timeout=8000)
        expect(p.locator('#queue-chips .q-chip')).to_have_count(0)

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
