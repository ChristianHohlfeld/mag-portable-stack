# mag-test-timeout: 30
"""No-GPU contracts for destructive room context reset and first-class 3D jobs."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class RoomKVIdolContract(unittest.TestCase):
    def test_reset_erases_live_kv_and_blocks_old_saved_prefix(self):
        room = (ROOT/"mag-room/native/magroom.cpp").read_text()
        route = (ROOT/"mag-room/native/routes.hpp").read_text()
        helper = (ROOT/"ops/room/purge-prefix.sh").read_text()
        sudoers = (ROOT/"ops/room/mag-room-prefix.sudoers").read_text()
        deploy = (ROOT/"ops/deploy.sh").read_text()
        self.assertIn('"/slots/" + std::to_string(id) + "?action=erase"', room)
        self.assertIn("static bool reset_room_prefix_cache", room)
        self.assertIn("if (!reset_room_prefix_cache(cache_error))", route)
        self.assertIn("if (result.code == 200) schedule_prewarm()", route)
        self.assertIn("WIRE_SESSIONS.clear()", room)
        self.assertIn("prompt-wire-", room)
        self.assertIn("clean_tokens/", room)
        self.assertIn("std::labs(n_past-expected)", room)
        self.assertIn("n_past", room)
        self.assertIn('prefix_slot_rpc(idx,model,"restore",clean,30000)', room)
        self.assertIn("PREWARM_HOLD=false", room)
        self.assertIn("SLOT_WARM[slot-1]=restored?1:0", room)
        self.assertIn("find -P", helper)
        self.assertIn("-name 'mag-prefix-*.bin' -delete", helper)
        self.assertIn("mag-clean-", room)
        self.assertNotIn("systemctl", helper)
        self.assertNotIn("*.gguf", helper)
        self.assertIn("magbot ALL=(root) NOPASSWD:",sudoers)
        self.assertIn("visudo -cf /etc/sudoers.d/mag-room-prefix",deploy)

    def test_idol_is_normal_persistent_message_and_cleaned_on_delete(self):
        room = (ROOT/"mag-room/native/magroom.cpp").read_text()
        route = (ROOT/"mag-room/native/routes.hpp").read_text()
        worker = (ROOT/"ops/idol/worker.py").read_text()
        js = (ROOT/"mag-room/static/idol-ui.js").read_text()
        html = (ROOT/"mag-room/web/index.html").read_text()
        nginx = (ROOT/"ops/nginx/mag-speech-idol.inc").read_text()
        self.assertIn('m["idol_job"] = idol_job',room)
        self.assertIn('append_room("user", name.empty()',room)
        self.assertIn("static ScopedResult register_idol_message",room)
        self.assertIn("idol_worker_request(idol_job, \"DELETE\")",room)
        self.assertIn('path == "/api/room/idol"',route)
        self.assertIn('path == "/api/room/clear"',route)
        self.assertIn('item["state"] = "deleted"',worker)
        self.assertIn('shutil.rmtree(ROOT / job_id)',worker)
        self.assertIn('def do_DELETE(self):',worker)
        self.assertIn('proxy_set_header X-Mag-Idol-Internal "";',nginx)
        self.assertIn('window.magIdolUI={render:renderHistory,park,clear}',js)
        self.assertIn('msg?.kind!=="idol"',js)
        self.assertIn('await registerCurrent(current)',js)
        self.assertIn('window.magIdolUI?.render(timeline)',html)
        self.assertIn('id="reset-quick"',html)
        self.assertIn("quick.disabled=true",html)
        self.assertIn("showUndo('Nachricht entfernt',r.undo)",html)
        self.assertNotIn("root.insertBefore(inlineCard",js)


if __name__ == "__main__":
    unittest.main()
