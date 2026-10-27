"""Playwright: reaction bar, toggle, and a second client seeing the pill live."""
import importlib.util
import os
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = pathlib.Path(os.environ.get('MAG_TEST_ARTIFACTS', pathlib.Path(tempfile.gettempdir()) / 'mag-artifacts'))
spec = importlib.util.spec_from_file_location('test_pwa', ROOT / 'mag-room/tests/test_pwa.py')
pwa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pwa)


class Reactions(unittest.TestCase):
    def test_toggle_and_second_client(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('playwright is not installed')
        ART.mkdir(parents=True, exist_ok=True)
        tmp = pathlib.Path('/tmp/mag-reactions')
        if tmp.exists():
            shutil.rmtree(tmp)
        static = tmp / 'static'
        shutil.copytree(ROOT / 'mag-room/static', static)
        proc, log, model, ui, bridge = pwa.start_room(tmp, static, 'reactions')
        errors = []
        pw = sync_playwright().start()
        browser = pw.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1280, 'height': 860}, color_scheme='dark')
        context.add_init_script("try{localStorage.setItem('mag-theme','dark')}catch(e){}")
        page = context.new_page()
        other = context.new_page()
        page.on('pageerror', lambda err: errors.append(str(err)))
        other.on('pageerror', lambda err: errors.append(str(err)))
        try:
            page.goto('http://127.0.0.1:%s/' % ui, wait_until='domcontentloaded')
            other.goto('http://127.0.0.1:%s/' % ui, wait_until='domcontentloaded')
            page.locator('#message').fill('Danke fuer die Hilfe')
            page.locator('#send-button').click()
            page.wait_for_function(
                "() => [...document.querySelectorAll('.message')].some(n => n.textContent.includes('Hallo aus dem Modell'))",
                timeout=15000)
            other.wait_for_function(
                "() => [...document.querySelectorAll('.message')].some(n => n.textContent.includes('Hallo aus dem Modell'))",
                timeout=15000)
            msg = page.locator('.message', has_text='Hallo aus dem Modell').last
            # No permanent per-message reaction buttons any more.
            self.assertEqual(page.locator('.react-bar, .react-more, .msg-trash').count(), 0)
            pop = page.locator('#msg-pop')
            msg.locator('.content').hover()
            pop.wait_for(state='visible')
            self.assertEqual(pop.locator('.pop-emoji button').count(), 7)  # 6 emoji + more
            self.assertEqual(pop.locator('.pop-actions button').count(), 3)  # copy, read aloud, delete
            box = msg.bounding_box()
            page.screenshot(path=str(ART / 'reactions_bar.png'), clip={
                'x': max(0, box['x'] - 16), 'y': max(0, box['y'] - 90),
                'width': min(560, box['width'] + 48), 'height': box['height'] + 120})
            # Escape closes, outside click closes, choosing closes.
            page.keyboard.press('Escape')
            pop.wait_for(state='hidden')
            msg.click(button='right')
            pop.wait_for(state='visible')
            page.mouse.click(5, 5)
            pop.wait_for(state='hidden')
            msg.click(button='right')
            pop.locator('.pop-more').click()
            self.assertTrue(pop.locator('.pop-grid').is_visible())
            pop.locator('.pop-emoji button', has_text='👍').click()
            pop.wait_for(state='hidden')
            page.locator('.react-pill', has_text='👍').wait_for(state='visible')
            other.locator('.react-pill', has_text='👍').wait_for(state='visible', timeout=10000)
            pill = page.locator('.message', has_text='Hallo aus dem Modell').last
            box = pill.bounding_box()
            page.screenshot(path=str(ART / 'reactions_pills.png'), clip={
                'x': max(0, box['x'] - 16), 'y': max(0, box['y'] - 12),
                'width': min(560, box['width'] + 48), 'height': box['height'] + 28})
            self.assertIn('1', pill.locator('.react-pill').inner_text())
            page.mouse.move(5, 5)
            pill.locator('.react-pill').click()
            other.wait_for_function("() => !document.querySelector('.react-pill')", timeout=10000)
            # Only one popover at a time.
            user = page.locator('.message.user').last
            msg.click(button='right'); user.click(button='right')
            self.assertEqual(page.locator('.msg-pop.open').count(), 1)
            self.assertEqual(page.locator('.message.pop-target').count(), 1)
            page.keyboard.press('Escape')
            self._mobile(browser, ui, errors)
            self.assertEqual(errors, [])
        finally:
            pwa.stop_room(proc, log, model)
            context.close()
            browser.close()
            pw.stop()


    def _mobile(self, browser, ui, errors):
        """Phone portrait: long-press opens; scroll, back gesture and choice close."""
        ctx = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
        page = ctx.new_page()
        page.on('pageerror', lambda err: errors.append(str(err)))
        try:
            page.goto('http://127.0.0.1:%s/' % ui, wait_until='domcontentloaded')
            msg = page.locator('.message', has_text='Hallo aus dem Modell').last
            msg.wait_for()
            pop = page.locator('#msg-pop')
            def long_press():
                msg.locator('.content').dispatch_event('pointerdown', {'pointerType': 'touch', 'clientX': 60, 'clientY': 200, 'bubbles': True})
                page.wait_for_timeout(600)
                msg.locator('.content').dispatch_event('pointerup', {'pointerType': 'touch', 'bubbles': True})
                pop.wait_for(state='visible')
            long_press()
            box = pop.bounding_box()
            self.assertLessEqual(box['x'] + box['width'], 390)
            self.assertGreaterEqual(box['x'], 0)
            page.screenshot(path=str(ART / 'reactions_mobile.png'))
            page.go_back()  # Android back gesture
            pop.wait_for(state='hidden')
            self.assertIn('127.0.0.1', page.url)
            long_press()
            page.evaluate("document.dispatchEvent(new Event('scroll'))")
            pop.wait_for(state='hidden')
            long_press()
            page.touchscreen.tap(5, 300)
            pop.wait_for(state='hidden')
            # A short tap never opens it.
            msg.locator('.content').dispatch_event('pointerdown', {'pointerType': 'touch', 'bubbles': True})
            msg.locator('.content').dispatch_event('pointerup', {'pointerType': 'touch', 'bubbles': True})
            page.wait_for_timeout(600)
            self.assertFalse(pop.is_visible())
        finally:
            ctx.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)

