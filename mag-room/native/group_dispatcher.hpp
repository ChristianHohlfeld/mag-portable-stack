#pragma once
// Group-chat dispatcher for the MAG room (replaces proactive rounds).
// Pure, CPU-free helpers shared with tests/test_group_dispatcher.cpp.
//
// Turn-taking follows the usual multi-agent group-chat pattern:
//  - explicit addressing wins (AutoGen GroupChat "speaker selection": a
//    mentioned agent is the next speaker; "auto"/"round_robin" otherwise),
//  - with no addressee exactly ONE suitable speaker is selected,
//  - bot replies may trigger real replies, particularly direct @mentions;
//    unaddressed bots respond only with substance, otherwise invisible PASS,
//  - hard round budget per human message (not once-per-bot), never to self,
//  - a new human message pre-empts every pending follow-up.
#include <algorithm>
#include <cctype>
#include <string>
#include <utility>
#include <vector>

namespace group_chat {

struct Bot {
  std::string id, name;
  bool coder = false; // code-specialised model (e.g. Flint on NUMA1)
};

constexpr int kMaxFollowups = 6;

inline std::string lower(std::string s) {
  for (char &c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return s;
}
inline bool word_char(unsigned char c) { return std::isalnum(c) || c == '_' || c >= 0x80; }

// Whole-word (or whole-name) occurrence of `needle` in lower-cased `hay`.
inline bool has_word(const std::string &hay, const std::string &needle) {
  if (needle.empty()) return false;
  for (size_t p = hay.find(needle); p != std::string::npos; p = hay.find(needle, p + 1)) {
    const size_t e = p + needle.size();
    const bool left = p == 0 || !word_char((unsigned char)hay[p - 1]) ||
                      (hay[p - 1] == '@');
    const bool right = e >= hay.size() || !word_char((unsigned char)hay[e]);
    if (left && right) return true;
  }
  return false;
}

// "beide", "ihr", "euch" and "@alle" address everybody anywhere; a bare
// "alle"/"allen" only at the start or after a greeting ("Hi alle"), so
// "lösch alle Dateien" stays an ordinary sentence.
inline bool addresses_all(const std::string &text) {
  const std::string t = lower(text);
  for (const char *w : {"beide", "beiden", "ihr", "euch", "@alle", "@all", "@beide"})
    if (has_word(t, w)) return true;
  const auto a = t.find_first_not_of(" \r\n\t");
  if (a == std::string::npos) return false;
  std::string rest = t.substr(a);
  for (const char *g : {"hi ", "hallo ", "hey ", "moin ", "servus ", "guten morgen "})
    if (rest.rfind(g, 0) == 0) { rest = rest.substr(std::string(g).size()); break; }
  const auto b = rest.find_first_not_of(" ,");
  if (b == std::string::npos) return false;
  rest = rest.substr(b);
  for (const char *w : {"alle", "allen", "zusammen", "leute"})
    if (rest.rfind(w, 0) == 0 &&
        (rest.size() == std::string(w).size() || !word_char((unsigned char)rest[std::string(w).size()])))
      return true;
  return false;
}

// Names a bot can be called by: full name and its first token
// ("Qwen3-Coder-Next" -> "qwen3-coder-next", "qwen3"). Tokens < 3 chars
// are ignored so "Q" or "ai" never match ordinary prose.
inline std::vector<std::string> aliases(const Bot &b) {
  std::vector<std::string> out;
  const std::string n = lower(b.name);
  if (!n.empty()) out.push_back(n);
  size_t e = 0;
  while (e < n.size() && word_char((unsigned char)n[e])) ++e;
  if (e >= 3 && e < n.size()) out.push_back(n.substr(0, e));
  return out;
}

// `needle` occurs as a word in `t` outside every longer bot name that
// contains it ("qwen3" inside "qwen3-coder-next" does not count).
inline bool occurs_outside(std::string t, const std::string &needle,
                           const std::vector<Bot> &roster) {
  for (const auto &o : roster) {
    const std::string on = lower(o.name);
    if (on.size() <= needle.size() || on.find(needle) == std::string::npos) continue;
    for (size_t p = t.find(on); p != std::string::npos; p = t.find(on, p + 1))
      t.replace(p, on.size(), std::string(on.size(), ' '));
  }
  return has_word(t, needle);
}

// Bots addressed in the text, roster order. All bots on "beide"/"ihr".
// Full names always count; a short alias only when no other bot shares it.
inline std::vector<std::string> addressed(const std::string &text,
                                          const std::vector<Bot> &roster) {
  std::vector<std::string> out;
  if (roster.empty()) return out;
  if (addresses_all(text)) {
    for (const auto &b : roster) out.push_back(b.id);
    return out;
  }
  const std::string t = lower(text);
  for (const auto &b : roster) {
    const auto al = aliases(b);
    bool hit = !al.empty() && occurs_outside(t, al[0], roster);
    for (size_t i = 1; !hit && i < al.size(); ++i) {
      bool shared = false;
      for (const auto &o : roster)
        if (o.id != b.id)
          for (const auto &x : aliases(o))
            if (x == al[i]) shared = true;
      if (!shared && occurs_outside(t, al[i], roster)) hit = true;
    }
    if (hit) out.push_back(b.id);
  }
  return out;
}

inline bool looks_like_code(const std::string &text) {
  const std::string t = lower(text);
  if (t.find("```") != std::string::npos) return true;
  for (const char *k : {"code", "coden", "programm", "skript", "script", "funktion",
                        "function", "bug", "debug", "refactor", "html", "css",
                        "python", "javascript", "typescript", "c++", "rust", "java",
                        "sql", "regex", "compile", "kompil", "implementier", "repo",
                        "github", "api", "klasse", "class ", "three.js", "app ",
                        "webseite", "website", "spiel", "game", "terminal", "bash"})
    if (t.find(k) != std::string::npos) return true;
  return false;
}

// One suitable speaker when nobody is addressed: code -> coder model,
// otherwise the first general bot; fallback the first roster bot.
inline std::string pick_speaker(const std::string &text, const std::vector<Bot> &roster) {
  if (roster.empty()) return "";
  const bool code = looks_like_code(text);
  for (const auto &b : roster)
    if (b.coder == code) return b.id;
  return roster.front().id;
}

// Should-I-respond gate: the follow-up answer is the bare token PASS.
inline bool is_pass(const std::string &text) {
  const auto a = text.find_first_not_of(" \r\n\t*_`\"'.");
  if (a == std::string::npos) return text.find_first_not_of(" \r\n\t") == std::string::npos;
  const auto b = text.find_last_not_of(" \r\n\t*_`\"'.!");
  std::string core = lower(text.substr(a, b - a + 1));
  if (core.size() > 2 && core.front() == '[') { // "[Name] PASS"
    const auto c = core.find(']');
    if (c != std::string::npos) core = core.substr(c + 1);
    const auto s = core.find_first_not_of(" ");
    core = s == std::string::npos ? "" : core.substr(s);
  }
  return core == "pass" || core == "(pass)";
}
// "PASS" on its own first line followed by more text: Flint on the append-only
// complement wire sometimes continues with an invented transcript
// ("PASS\n\n- User: ..."). The first line decides.
inline bool leading_pass(const std::string &text) {
  const auto a = text.find_first_not_of(" \r\n\t");
  if (a == std::string::npos) return false;
  const auto nl = text.find('\n', a);
  if (nl == std::string::npos) return false;
  return is_pass(text.substr(a, nl - a));
}
// While streaming: could this still turn into PASS? Then do not reveal yet.
inline bool maybe_pass(const std::string &partial) {
  if (leading_pass(partial)) return true; // "PASS\n..." is never shown
  std::string t = lower(partial);
  auto a = t.find_first_not_of(" \r\n\t*_`\"'(");
  if (a == std::string::npos) return true;
  t = t.substr(a);
  if (!t.empty() && t.front() == '[') { // "[Name] PASS"
    const auto c = t.find(']');
    if (c == std::string::npos) return t.size() < 64;
    t = t.substr(c + 1);
    a = t.find_first_not_of(" \r\n\t*_`\"'(");
    if (a == std::string::npos) return true;
    t = t.substr(a);
  }
  if (std::string("pass").rfind(t, 0) == 0) return true; // "p", "pa", ...
  return t.rfind("pass", 0) == 0 && t.size() <= 8 &&
         (t.size() == 4 || !word_char((unsigned char)t[4]));
}

// Bot-to-bot next speaker: a direct @mention takes priority. Without one,
// alternate to another bot and let the recipient decide whether to PASS.
// The budget is counted per completed bot-to-bot turn, not per distinct bot.
inline std::string next_followup(const std::vector<Bot> &roster,
                                 const std::vector<std::string> &spoken,
                                 const std::string &speaker, int followups_used,
                                 int max_followups = kMaxFollowups,
                                 const std::string &reply = "") {
  if (followups_used >= max_followups) return "";
  for (const auto &id : addressed(reply, roster))
    if (id != speaker) return id;
  for (const auto &b : roster)
    if (b.id != speaker) return b.id;
  return "";
}

inline const char *gate_instruction() {
  return "\n[Gruppenchat]\nLies die letzte Nachricht eines anderen Bots aufmerksam. "
         "Wenn du mit Namen oder @ angesprochen wurdest, antworte direkt darauf, "
         "frage sinnvoll nach oder widersprich begründet. Sonst antworte nur, "
         "wenn du wirklich neue Information, eine nötige Korrektur oder eine "
         "sinnvolle Rückfrage hast. Keine Floskeln, Danksagungen oder "
         "Höflichkeitsschleifen. Wenn du nichts beizutragen hast, antworte "
         "ausschliesslich PASS. Das wird nicht angezeigt.";
}

// Direct turn after earlier complements in the same wire: the old PASS gate
// text is still in the history and Flint kept answering PASS to greetings
// (live 20:04, 20:32). Lift it explicitly for this message.
inline const char *direct_instruction() {
  return "\n[Gruppenchat]\nDiese Nachricht richtet sich direkt an dich: "
         "antworte normal. PASS ist hier nicht erlaubt.";
}

} // namespace group_chat
