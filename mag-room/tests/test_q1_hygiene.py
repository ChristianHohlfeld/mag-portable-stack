"""Q1 hygiene regression: compile and exercise the actual C++ reply helpers."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "native/magroom.cpp").read_text()


class Q1Hygiene(unittest.TestCase):
    def test_q1_only_request_contract(self):
        self.assertIn('body["min_p"] = 0.08', SOURCE)
        self.assertIn('body["repeat_penalty"] = 1.1', SOURCE)
        self.assertIn('max_output = std::min(max_output, 96)', SOURCE)
        self.assertIn('q1_deadline_ms > 0 ? q1_deadline_ms : 30000', SOURCE)
        self.assertIn('answer.find("q1_degenerate")', SOURCE)
        self.assertIn('reverse_pairs.push_back(answer);reverse_pairs.push_back(user)', SOURCE)
        self.assertIn('t.finish!="stop" || q1_bad(t.text)', SOURCE)
        self.assertIn('std::vector<Val> fresh', SOURCE)
        self.assertIn('fresh.push_back(messages.front())', SOURCE)
        self.assertIn('fresh.push_back(messages.back())', SOURCE)

    @unittest.skipUnless(shutil.which("g++"), "g++ not installed")
    def test_real_cpp_reply_helpers(self):
        start = SOURCE.index('static bool q1_bad(')
        end = SOURCE.index('static std::vector<Val> glm_chat_turns(', start)
        cpp = r"""
#include <algorithm>
#include <cctype>
#include <iostream>
#include <string>
static std::string trim(std::string s) {
  const auto a=s.find_first_not_of(" \\t\\r\\n");
  if(a==std::string::npos) return "";
  return s.substr(a,s.find_last_not_of(" \\t\\r\\n")-a+1);
}
""" + SOURCE[start:end] + r"""
int main() {
  auto check=[](bool ok){if(!ok) throw 1;};
  check(q1_clean("GLM-Q1: GLM-Q2: Hallo.")=="Hallo.");
  check(q1_clean("## GLM-Q1: Antwort.")=="Antwort.");
  check(q1_clean("Rauschen</think> 2, 3, 5.")=="2, 3, 5.");
  check(q1_clean("Eins. Zwei. Drei.")=="Eins. Zwei.");
  check(q1_clean("1. A\\n2. B\\n3. C")=="1. A\\n2. B\\n3. C");
  check(q1_bad("GLM-Q1: Echo"));
  check(q1_bad("a,b,c,d,e,f,g,h,i,j"));
  check(q1_bad(""));
  check(!q1_bad("Die Primzahlen sind 2, 3 und 5."));
}
"""
        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d) / "q1.cpp"
            exe = pathlib.Path(d) / "q1"
            src.write_text(cpp)
            subprocess.run(["g++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                            str(src), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    unittest.main()
