"""Every inline <script> in the room page must parse (node --check). A literal
closing script tag inside a JS string ends the inline script early and leaves
the page with 'Invalid or unexpected token' and no messages."""
import pathlib,re,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
class InlineScripts(unittest.TestCase):
 def test_inline_scripts_parse(self):
  if not shutil.which('node'):self.skipTest('node is not installed')
  html=(ROOT/'mag-room/web/index.html').read_text()
  blocks=[(a,c) for a,c in re.findall(r'<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>',html,re.S) if 'importmap' not in a]
  self.assertTrue(blocks)
  with tempfile.TemporaryDirectory() as d:
   for i,(attrs,code) in enumerate(blocks):
    f=pathlib.Path(d)/f'inline{i}.{"mjs" if "module" in attrs else "js"}';f.write_text(code)
    r=subprocess.run(['node','--check',str(f)],capture_output=True,text=True)
    self.assertEqual(r.returncode,0,f'inline script {i}: {r.stderr[:500]}')
if __name__=='__main__':unittest.main()
