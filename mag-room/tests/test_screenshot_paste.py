"""Playwright: pasting/dropping screenshots into the composer, on emulated
iPhone 13, Pixel 7 and desktop, against the real native room and upload path.

Covers: several images per paste, x removes one, plain-text paste untouched,
EXIF orientation kept, downscale to 2048 px JPEG, drag and drop, the
"Bild einfügen" clipboard button, and the upload reaching the room.
Screenshots go to /opt/cursor/artifacts/screenshots when that folder exists.
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
import json, pathlib, shutil, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_pwa as tp

SHOTS = pathlib.Path('/opt/cursor/artifacts/screenshots')

# One image per call: a wide PNG screenshot (downscale), a small PNG, and a
# JPEG whose EXIF says "rotate 90°" (orientation 6), built in the page.
MAKE_FILES = """async () => {
  const png=async(w,h,color)=>{const c=document.createElement('canvas');c.width=w;c.height=h;
    const x=c.getContext('2d');x.fillStyle=color;x.fillRect(0,0,w,h);x.fillStyle='#fff';x.font='bold 60px sans-serif';x.fillText('MAG',20,80);
    return new Promise(r=>c.toBlob(r,'image/png'))};
  const wide=new File([await png(3000,1200,'#1f6feb')],'Bildschirmfoto 2026-10-19.png',{type:'image/png'});
  const small=new File([await png(320,200,'#e62b2b')],'image.png',{type:'image/png'});
  const c=document.createElement('canvas');c.width=60;c.height=30;const x=c.getContext('2d');x.fillStyle='#2ea043';x.fillRect(0,0,60,30);
  const raw=new Uint8Array(await (await new Promise(r=>c.toBlob(r,'image/jpeg',.9))).arrayBuffer());
  const exif=[0xFF,0xE1,0x00,0x22,0x45,0x78,0x69,0x66,0x00,0x00,0x4D,0x4D,0x00,0x2A,0x00,0x00,0x00,0x08,
    0x00,0x01,0x01,0x12,0x00,0x03,0x00,0x00,0x00,0x01,0x00,0x06,0x00,0x00,0x00,0x00,0x00,0x00];
  const rot=new Uint8Array(raw.length+exif.length);rot.set(raw.slice(0,2));rot.set(exif,2);rot.set(raw.slice(2),2+exif.length);
  const rotated=new File([rot],'IMG_0001.jpg',{type:'image/jpeg'});
  window.__files={wide,small,rotated};
}"""

PASTE = """names => {
  const dt=new DataTransfer();for(const n of names)dt.items.add(window.__files[n]);
  const ev=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});
  document.getElementById('message').dispatchEvent(ev);return ev.defaultPrevented;
}"""

STAGED = """async () => Promise.all(stagedPhotos.map(async p => {
  const b=await createImageBitmap(p.blob);return {name:p.name,type:p.blob.type,w:b.width,h:b.height};
}))"""


class ScreenshotPaste(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('playwright is not installed')
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        (cls.tmp / 'state').mkdir()
        (cls.tmp / 'state/agents.json').write_text(json.dumps(
            [{'id': 'v41', 'name': 'v4.1', 'vm': '', 'memory': [], 'color': '#7aa2f7'}]))
        cls.proc, cls.log, cls.model, cls.ui, _ = tp.start_room(
            cls.tmp, tp.ROOT / 'mag-room/static', 'paste', extra={'MAG_TEST_NO_MEMGIT': '1'})
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(args=['--no-sandbox'])
        except Exception:
            cls.browser = cls.pw.chromium.launch(channel='chrome', args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        tp.stop_room(cls.proc, cls.log, cls.model)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def device(self, name):
        if name == 'desktop':
            return {'viewport': {'width': 1280, 'height': 800}}
        d = dict(self.pw.devices[name])
        d.pop('default_browser_type', None)
        return d

    def shot(self, page, name):
        if SHOTS.is_dir():
            page.screenshot(path=str(SHOTS / name))

    def open(self, name, **extra):
        ctx = self.browser.new_context(color_scheme='dark', **self.device(name), **extra)
        page = ctx.new_page()
        page.goto(f'http://127.0.0.1:{self.ui}/')
        page.wait_for_selector('#message')
        page.wait_for_function('typeof stagedPhotos!=="undefined" && state && state.agents && state.agents.length>0')
        page.evaluate(MAKE_FILES)
        return ctx, page

    def files_in_room(self):
        return [f['name'] for f in tp.api(self.ui, '/api/state')['files']]

    def test_paste_on_every_device(self):
        for name, tag in (('iPhone 13', 'iphone13'), ('Pixel 7', 'pixel7'), ('desktop', 'desktop')):
            with self.subTest(device=name):
                ctx, page = self.open(name)
                try:
                    page.click('#message')
                    self.assertTrue(page.evaluate(PASTE, ['wide', 'small', 'rotated']))
                    page.wait_for_function('document.querySelectorAll("#photo-preview .photo-card").length===3')
                    self.assertTrue(page.is_visible('#photo-note'))
                    staged = page.evaluate(STAGED)
                    wide, small, rotated = staged
                    self.assertEqual((wide['w'], wide['h']), (2048, 819), 'long side capped at 2048 px')
                    self.assertEqual((small['w'], small['h']), (320, 200), 'small images keep their size')
                    self.assertEqual((rotated['w'], rotated['h']), (30, 60), 'EXIF orientation 6 is applied')
                    self.assertTrue(all(s['type'] == 'image/jpeg' and s['name'].endswith('.jpg') for s in staged))
                    box = page.locator('#photo-preview .photo-card').first.bounding_box()
                    vw = page.viewport_size['width']
                    self.assertGreaterEqual(box['x'], 0)
                    self.assertLessEqual(box['x'] + box['width'], vw)
                    self.shot(page, f'paste-{tag}.png')
                    # Text paste is not intercepted.
                    prevented = page.evaluate("""() => {const dt=new DataTransfer();dt.setData('text/plain','nur Text');
                      const ev=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});
                      document.getElementById('message').dispatchEvent(ev);return ev.defaultPrevented}""")
                    self.assertFalse(prevented)
                    page.locator('#photo-preview .photo-card button').nth(1).click()
                    self.assertEqual(page.locator('#photo-preview .photo-card').count(), 2)
                    before = len(self.files_in_room())
                    page.fill('#message', 'Was siehst du auf dem Screenshot? (' + tag + ')')
                    page.click('#send-button')
                    page.wait_for_function('document.querySelectorAll("#photo-preview .photo-card").length===0')
                    page.wait_for_function('state.files && state.files.length>=%d' % (before + 2))
                    page.wait_for_selector('text=Was siehst du auf dem Screenshot? (' + tag + ')')
                    page.wait_for_timeout(600)
                    self.assertEqual(len(self.files_in_room()), before + 2)
                    self.shot(page, f'paste-{tag}-sent.png')
                finally:
                    ctx.close()

    def test_drag_and_drop_desktop(self):
        ctx, page = self.open('desktop')
        try:
            page.evaluate("""() => {const dt=new DataTransfer();dt.items.add(window.__files.small);dt.items.add(window.__files.wide);
              const form=document.getElementById('send-form');
              form.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));
              window.__hot=form.classList.contains('drop-hot');
              form.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}))}""")
            page.wait_for_function('document.querySelectorAll("#photo-preview .photo-card").length===2')
            self.assertTrue(page.evaluate('window.__hot'))
            self.assertFalse(page.evaluate("document.getElementById('send-form').classList.contains('drop-hot')"))
            self.shot(page, 'paste-drop-desktop.png')
        finally:
            ctx.close()

    def test_paste_button_reads_clipboard(self):
        ctx, page = self.open('desktop', permissions=['clipboard-read', 'clipboard-write'])
        try:
            page.evaluate("async () => navigator.clipboard.write([new ClipboardItem({'image/png': window.__files.small})])")
            page.click('#plus')
            self.assertTrue(page.is_visible('#upload-paste'))
            self.assertTrue(page.is_visible('text=Foto/Screenshot'))
            self.shot(page, 'paste-plus-menu.png')
            page.click('#upload-paste')
            page.wait_for_function('document.querySelectorAll("#photo-preview .photo-card").length===1')
        finally:
            ctx.close()

    def test_gallery_input_accepts_images(self):
        ctx, page = self.open('iPhone 13')
        try:
            self.assertEqual(page.get_attribute('#photo-input', 'accept'), 'image/*')
            self.assertIsNotNone(page.get_attribute('#photo-input', 'multiple'))
            page.tap('#plus')
            self.assertEqual(page.evaluate("""(()=>{const r=document.getElementById('upload-photo').getBoundingClientRect();
              return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('button')?.id||''})()"""),
              'upload-photo', 'bottom-sheet item must be on top of the dim overlay')
            self.shot(page, 'paste-plus-menu-iphone13.png')
            with page.expect_file_chooser() as fc:
                page.tap('#upload-photo')
            chooser = fc.value
            self.assertTrue(chooser.is_multiple())
            png = self.tmp / 'gallery.png'
            png.write_bytes(bytes.fromhex(
                '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                '1f15c4890000000d49444154789c6360f80f0000010101005d0d0b0000000049454e44ae426082'))
            chooser.set_files([str(png)])
            page.wait_for_function('document.querySelectorAll("#photo-preview .photo-card").length===1')
        finally:
            ctx.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
