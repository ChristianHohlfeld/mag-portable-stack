"""Static regression contract for MAG's existing live tool/audit event bus."""
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]

class LiveToolTimeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=(ROOT/"mag-room/web/index.html").read_text()
        cls.js=(ROOT/"mag-room/static/tool-timeline.js").read_text()
        cls.css=(ROOT/"mag-room/static/tool-timeline.css").read_text()
        cls.backend=(ROOT/"mag-room/native/magroom.cpp").read_text()

    def test_existing_native_audit_events_are_source(self):
        for value in ('e["type"] = "tool";','e["args"] = args;','e["type"] = "tool_result";','e["result"] = mj::utf8_cut(result, 1500);'):
            self.assertIn(value,self.backend)
        for value in ("event.type==='tool'","event.type==='tool_result'","event.type==='step'"):
            self.assertIn(value,self.html)

    def test_ui_connected_to_live_chat_node(self):
        self.assertIn('/static/tool-timeline.js',self.html)
        self.assertIn('/static/tool-timeline.css',self.html)
        self.assertIn('magToolTimeline?.paint(s.node,s,id=>stopBot(id))',self.html)
        self.assertIn('magToolTimeline?.tick(s.node,s)',self.html)
        self.assertIn('result:null,at:Date.now()',self.html)
        self.assertIn("window.magToolTimeline={paint,tick,status,redact}",self.js)

    def test_real_arguments_and_completed_output_not_fake_stream(self):
        for value in ('t?.args_text||t?.args','typeof a.command','t.result','result==null','output_tail'):
            self.assertIn(value,self.js)
        self.assertIn("Noch keine stdout-Daten vom laufenden Befehl",self.js)
        self.assertIn("event.type==='tool_output'",self.html)
        self.assertIn('e["type"] = "tool_output";',self.backend)
        self.assertIn('on_output(std::string(b, (size_t)n))',(ROOT/"mag-room/native/common.hpp").read_text())
        self.assertNotIn('Math.random(',self.js)

    def test_cancel_uses_existing_targeted_stop(self):
        self.assertIn("id=>stopBot(id)",self.html)
        self.assertIn("fetch('/api/cancel'",self.html)
        self.assertIn("cancel(node.dataset.bot||'')",self.js)
        self.assertIn("btn.disabled=!!s.stopping",self.js)

    def test_mobile_and_scroll_contract(self):
        self.assertIn('@media(max-width:560px)',self.css)
        self.assertIn('max-height:55dvh',self.css)
        self.assertIn("if(!stickToBottom)",self.html)
        self.assertIn("stableScrollBefore()",self.html)
        self.assertIn("stableScrollAfter(scroll)",self.html)

    def test_no_additional_public_endpoint(self):
        self.assertNotIn('EventSource(',self.js)
        self.assertNotIn('WebSocket(',self.js)
        self.assertNotIn('http://',self.js)

if __name__=="__main__":
    unittest.main()
