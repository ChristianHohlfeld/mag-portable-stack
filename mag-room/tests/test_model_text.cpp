// What a model wrote, made fit for the chat (llm.hpp): raw channel markup is
// read and never shown, a text that runs in circles is cut to its first
// round, and the arguments of a tool call are a JSON object or marked.
#include <unistd.h>
#include "../native/llm.hpp"
#include <cstdio>
using namespace mag;
using namespace mag::llm;
static int fails = 0;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x); ++fails; } } while (0)
using Pair = std::pair<std::string, std::string>; // text, thoughts

static Pair clean(const std::vector<std::string> &deltas) {
  Clean c;
  Pair out;
  for (auto &d : deltas) c.feed(d, out.first, out.second);
  c.flush(out.first, out.second);
  return out;
}
// The same text in pieces of every size: a tag may be split anywhere.
static bool any_split(const std::string &raw, const Pair &want) {
  for (size_t size = 1; size <= raw.size(); ++size) {
    std::vector<std::string> deltas;
    for (size_t at = 0; at < raw.size(); at += size) deltas.push_back(raw.substr(at, size));
    if (clean(deltas) != want) { std::fprintf(stderr, "  pieces of %zu: \"%s\" | \"%s\"\n", size, clean(deltas).first.c_str(), clean(deltas).second.c_str()); return false; }
  }
  return true;
}
static std::string times(const std::string &s, int n) {
  std::string out;
  for (int i = 0; i < n; ++i) out += s;
  return out;
}

int main() {
  alarm(60);
  // ---- markup --------------------------------------------------------------
  // Gemma 4: an empty thought in front of the answer (what stood in the chat on 2026-11-11).
  CHECK(any_split("<|channel>thought\n<channel|>Die neue Szene ist bereit!", {"Die neue Szene ist bereit!", ""}));
  CHECK(any_split("<|channel>thought\nErst rechnen: 2+2.<channel|>Vier.", {"Vier.", "Erst rechnen: 2+2."}));
  CHECK(any_split("Fertig.<channel|>", {"Fertig.", ""}));
  CHECK(any_split("Ich baue das.<|tool_call>call:scene_3d{title:<|\"|>X<|\"|>}<tool_call|>", {"Ich baue das.", ""}));
  // A turn the model made up for Chris is not its answer.
  CHECK(any_split("Gern.<turn|>\n<|turn>user\nUnd jetzt?<turn|>\n<|turn>model\nWeiter.", {"Gern.\n\nWeiter.", ""}));
  // gpt-oss
  CHECK(any_split("<|channel|>analysis<|message|>Hm, kurz.<|end|><|start|>assistant<|channel|>final<|message|>Passt.", {"Passt.", "Hm, kurz."}));
  CHECK(any_split("<|start|>assistant to=functions.shell<|channel|>commentary <|constrain|>json<|message|>{\"command\":\"ls\"}<|call|>", {"", ""}));
  CHECK(any_split("Passt.<|return|>", {"Passt.", ""}));
  // Ordinary text keeps its angle brackets, and code is never markup.
  CHECK(any_split("<b>fett</b>, a < b und <em>x</em> <div>", {"<b>fett</b>, a < b und <em>x</em> <div>", ""}));
  const std::string about = "Das Template beginnt mit `<start_of_turn>user`.\n```\n<|channel>thought\n<|turn>model\n```\nSo ist das.";
  CHECK(any_split(about, {about, ""}));
  // The start of a tag that never arrived is no text; a lone bracket is.
  CHECK(clean({"Ende<|chan"}).first == "Ende");
  CHECK(clean({"a <"}).first == "a <");

  // ---- circles ---------------------------------------------------------------
  const std::string round = "Die neue Szene „Das große Abenteuer“ ist bereit! Ich habe eine längere Abfolge eingebaut. 🦊✨\n";
  const std::string lead = "Hier ist sie: ";
  CHECK(round.size() > 60 && round.size() < 150);
  CHECK(circles(lead + times(round, 3), false) == std::string::npos);   // a sentence said three times may be meant
  CHECK(circles(lead + times(round, 4), false) == (lead + round).size());
  // Noticed in the middle of the next round: still cut to exactly one.
  CHECK(circles(lead + times(round, 4) + "Die neue Sz", false) == (lead + round).size());
  CHECK(circles(lead + times(round, 6) + "Die", false) == (lead + round).size());
  // A long passage three times over is enough.
  const std::string longer = round + "Der Gnom winkt dem Fuchs zu, der Fuchs wedelt mit dem Schwanz, und beide gehen zusammen durch den Schnee.\n";
  CHECK(longer.size() >= 150 && circles(lead + times(longer, 2), false) == std::string::npos && circles(lead + times(longer, 3), false) == (lead + longer).size());
  // Never in the middle of a character, wherever the circle began.
  const std::string odd = "Ĥ" + times("ä und so weiter und so fort, immer und immer wieder dasselbe, ohne Ende. ", 5);
  CHECK(mj::utf8_fix(odd.substr(0, circles(odd, false))) == odd.substr(0, circles(odd, false)));
  // Code and arguments may repeat themselves: a grid of equal cells is no circle ...
  const std::string cell = "  <div class=\"cell\"></div>\n";
  CHECK(circles("<div class=\"grid\">\n" + times(cell, 64), true) == std::string::npos);
  CHECK(circles("```html\n" + times(cell, 64), in_code("```html\n" + times(cell, 64))) == std::string::npos);
  CHECK(in_code("```html\n<p>") && !in_code("```html\n<p>\n```\nFertig."));
  const std::string action = "{\"actor\":\"Fuchs\",\"do\":\"jump\",\"seconds\":1},";
  CHECK(circles("{\"actions\":[" + times(action, 12), true) == std::string::npos);
  // ... until it is far over the top.
  CHECK(circles(times(round, 60), true) == round.size());
  // A short thing said two thousand bytes long is a circle in prose.
  CHECK(circles(times("ha ", 700), false) == 3);
  CHECK(circles("| a | b |\n|---|---|\n" + times("| 1 | 2 |\n", 12), false) == std::string::npos);
  // The same passage a fourth time with something else in between.
  const std::string passage = "Ich habe eine längere Abfolge an Bewegungen eingebaut, damit es etwas lebhafter zugeht, und den Fuchs zweimal springen lassen. ";
  std::string varied;
  for (int i = 1; i <= 4; ++i) varied += "Fassung " + std::to_string(i) + ": " + passage + "\n";
  CHECK(circles(varied, false) != std::string::npos && circles(varied, false) < 2 * passage.size());
  CHECK(circles("Fassung 1: " + passage + "\nFassung 2: " + passage, false) == std::string::npos);

  // ---- arguments of a tool call ------------------------------------------------
  // The call that ran away: the string closed with a plain quote, then markup and the answer in circles.
  ToolCall away;
  away.arguments = "{\"ground\":\"snow\",\"title\":\"Das große Abenteuer von Gnom und Fuchs\\\"}<tool_call|><tool_call|>\\n" +
                   times("<|channel>thought\\n<channel|>Die neue Szene ist bereit! 🦊|\\n", 40);
  CHECK(find_tag(away.arguments) != std::string::npos);
  mend(away);
  CHECK(away.valid && jstr(jparse(away.arguments), "title") == "Das große Abenteuer von Gnom und Fuchs" && jstr(jparse(away.arguments), "ground") == "snow");
  ToolCall fine;
  fine.arguments = "{\"command\":\"echo \\\"hi\\\"\"}";
  mend(fine);
  CHECK(fine.valid && fine.arguments == "{\"command\":\"echo \\\"hi\\\"\"}");
  ToolCall none;
  mend(none);
  CHECK(none.valid && none.arguments == "{}");
  for (const char *broken : {"{\"fact\":\"abgeschnitten", "[1,2]", "nichts", "{\"a\":1}<tool_call|>{\"b\":"}) {
    ToolCall call;
    call.arguments = broken;
    mend(call);
    CHECK(std::string(broken) == "{\"a\":1}<tool_call|>{\"b\":" ? call.valid && call.arguments == "{\"a\":1}" : !call.valid && call.arguments == "{}");
  }

  // ---- thoughts --------------------------------------------------------------
  // Gemma's outline as the server hands it on (Lena, 2026-11-11): the first
  // line without its indent, the rest indented, groups without headings, and
  // an entry with nothing behind its bullet.
  const std::string outline = "*   Current Status: Lena is back.\n    *   Context: 3D scenes.\n    *   Goal: answer.\n\n"
                              "    *   The model is simple.\n    *   \n\n\n    *   Be concise.\n";
  CHECK(tidy_thoughts(outline) == "*   Current Status: Lena is back.\n*   Context: 3D scenes.\n*   Goal: answer.\n\n*   The model is simple.\n\n*   Be concise.");
  CHECK(tidy_thoughts("*   \n*   \n-\n1.\n  2)\n•\n") == "");
  CHECK(tidy_thoughts("\n\n  \n") == "");
  CHECK(tidy_thoughts("Plan:\n- Respond \"ok\".\n") == "Plan:\n- Respond \"ok\".");
  CHECK(tidy_thoughts("Total: 2+2 = 4.\n1. a\n10. b") == "Total: 2+2 = 4.\n1. a\n10. b");
  // What is nested stays nested, and code keeps its shape.
  CHECK(tidy_thoughts("    1.  Steps:\n        *   first\n        *   second\n    ```\n      x = 1\n    ```") ==
        "1.  Steps:\n    *   first\n    *   second\n```\n  x = 1\n```");

  // ---- errors ----------------------------------------------------------------
  const std::string quoted = "Failed to parse tool call arguments as JSON: [json.exception.parse_error.101] parse error at line 1, column 12345: "
                             "syntax error while parsing value - invalid string: missing closing quote; last read: '\"Das große Abenteuer\\\"}<tool_call|>\\n" +
                             times("<|channel>thought\\n<channel|>Die neue Szene ist bereit!|\\n", 40) + "'";
  CHECK(short_error(quoted) == "Ein Werkzeugaufruf im Verlauf war nicht lesbar; der Modell-Server hat die Anfrage abgelehnt.");
  const std::string other = short_error("Something broke:\\nline two\n<|channel>thought\\n<channel|>" + times("und so weiter ", 100));
  CHECK(other == "Something broke: line two");
  CHECK(short_error(times("ä", 400)).size() <= 244 && find_tag(short_error(times("x<channel|>", 9))) == std::string::npos);

  if (fails) return 1;
  std::printf("model text ok\n");
  return 0;
}
