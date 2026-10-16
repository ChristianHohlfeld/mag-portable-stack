"""Playwright: reaction bar, toggle, and a second client seeing the pill live."""
import importlib.util
import pathlib
import shutil
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = pathlib.Path('/opt/cursor/artifacts')
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
            msg.hover()
            bar = msg.locator('.react-bar')
            bar.wait_for(state='visible')
            box = msg.bounding_box()
            page.screenshot(path=str(ART / 'reactions_bar.png'), clip={
                'x': max(0, box['x'] - 16),
                'y': max(0, box['y'] - 56),
                'width': min(520, box['width'] + 48),
                'height': box['height'] + 80,
            })
            bar.locator('.react-quick', has_text='👍').click()
            page.locator('.react-pill', has_text='👍').wait_for(state='visible')
            other.locator('.react-pill', has_text='👍').wait_for(state='visible', timeout=10000)
            pill = page.locator('.message', has_text='Hallo aus dem Modell').last
            box = pill.bounding_box()
            page.screenshot(path=str(ART / 'reactions_pills.png'), clip={
                'x': max(0, box['x'] - 16),
                'y': max(0, box['y'] - 12),
                'width': min(560, box['width'] + 48),
                'height': box['height'] + 28,
            })
            self.assertIn('1', pill.locator('.react-pill').inner_text())
            pill.locator('.react-pill').click()
            other.wait_for_function(
                "() => !document.querySelector('.react-pill')",
                timeout=10000)
            self.assertEqual(errors, [])
        finally:
            pwa.stop_room(proc, log, model)
            context.close()
            browser.close()
            pw.stop()


if __name__ == '__main__':
    unittest.main(verbosity=2)
