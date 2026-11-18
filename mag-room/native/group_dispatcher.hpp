#pragma once
// Group-chat dispatcher for the MAG room (replaces proactive rounds).
// Pure, CPU-free helpers shared with tests/test_group_dispatcher.cpp.
//
// Turn-taking follows the usual multi-agent group-chat pattern:
//  - explicit addressing wins (AutoGen GroupChat "speaker selection": a
//    mentioned agent is the next speaker; "auto"/"round_robin" otherwise),
//  - with no addressee every active bot answers the human message for real,
//  - bot replies trigger further replies, particularly direct @mentions;
//    conversations run server-side without browser/SSE;
//  - optional PASS lets bots decline a round; with PASS disabled engine.hpp
//    validates a substantive reply or retries once, never executing an
//    unattended tool;
//  - when all bots voluntarily PASS with the option on, the current exchange
//    rests; the room may start a new autonomous exchange after its interval;
//  - optional bot follow-up budget MAG_ROOM_BOT_ROUNDS (0 = unlimited,
//    no hidden fuse; autonomous opener does not use up a follow-up),
//  - a new human message or Stop pre-empts every pending follow-up.
#include <algorithm>
#include <cctype>
#include <string>
#include <utility>
#include <vector>

namespace group_chat {

struct Bot {
  std::string id, name;
};

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
// ("Lena-Mini" -> "lena-mini", "lena"). Tokens < 3 chars
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
// contains it ("lena" inside "lena-mini" does not count).
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

// Explicit mentions: "@Lena", "@noah", an earlier name or the id of a bot,
// and "@alle"/"@beide" for everybody. Unlike a name in a sentence, a mention
// decides who is asked: only the mentioned bots answer. `names` are all names
// a bot answers to, lower-case. `everyone` reports an "@alle".
struct Named {
  std::string id;
  std::vector<std::string> names;
};
inline std::vector<std::string> mentioned(const std::string &text, const std::vector<Named> &roster, bool *everyone = nullptr) {
  const std::string t = lower(text);
  std::vector<bool> hit(roster.size(), false);
  bool all = false;
  for (size_t at = t.find('@'); at != std::string::npos; at = t.find('@', at + 1)) {
    if (at > 0 && word_char((unsigned char)t[at - 1])) continue; // part of an address, not a mention
    const auto is = [&](const std::string &name) {
      const size_t end = at + 1 + name.size();
      if (name.empty() || t.compare(at + 1, name.size(), name) != 0) return false;
      if (end >= t.size()) return true;
      const unsigned char next = (unsigned char)t[end];
      // "@max" is not "@max-2" and not "@maxi".
      return !word_char(next) && !(next == '-' && end + 1 < t.size() && std::isalnum((unsigned char)t[end + 1]));
    };
    for (const char *word : {"alle", "all", "beide", "everyone"})
      if (is(word)) all = true;
    for (size_t i = 0; i < roster.size(); ++i)
      for (const auto &name : roster[i].names)
        if (is(name)) hit[i] = true;
  }
  if (everyone) *everyone = all;
  std::vector<std::string> out;
  for (size_t i = 0; i < roster.size(); ++i)
    if (all || hit[i]) out.push_back(roster[i].id);
  return out;
}

// Should-I-respond gate: the follow-up answer is the bare token PASS.
// Strip wrapping brackets/markup/punctuation: "[PASS]", "(pass)", "<PASS>".
inline std::string bare_token(const std::string &s) {
  static const char *junk = " \r\n\t*_`\"'.!?:;,@[](){}<>-";
  const auto x = s.find_first_not_of(junk);
  if (x == std::string::npos) return "";
  const auto y = s.find_last_not_of(junk);
  return s.substr(x, y - x + 1);
}
// Same words as an earlier line of this chain (case/space/punctuation-blind).
inline std::string norm_reply(const std::string &text) {
  std::string out;
  bool sp = false;
  for (unsigned char c : text) {
    if (std::isalnum(c) || c >= 0x80) { if (sp && !out.empty()) out.push_back(' '); sp = false; out.push_back(static_cast<char>(std::tolower(c))); }
    else sp = true;
  }
  return out;
}
// Meta lines a model writes instead of PASS. Never chat content: live
// 2026-11-06/07 Lena chipped in with "Kein Content" (posted), and the
// three copies in her own wire made her repeat it; Noah answered with a bare
// room control marker "[[MAG:ENDE]]" (words "mag ende").
inline const std::vector<std::string> &silent_markers() {
  // Only phrases no human answer consists of ("Keine Antwort." can be one).
  static const std::vector<std::string> m = {"kein content", "no content",
                                             "mag ende", "mag weiter"};
  return m;
}
inline bool is_pass(const std::string &text) {
  if (lower(bare_token(text)) == "pass") return true; // **[PASS]**, <pass>, (Pass).
  const std::string words = norm_reply(text);
  for (const auto &m : silent_markers())
    if (words == m) return true; // "Kein Content", "*kein Inhalt.*"
  const auto a = text.find_first_not_of(" \r\n\t*_`\"'.");
  if (a == std::string::npos) return text.find_first_not_of(" \r\n\t") == std::string::npos;
  const auto b = text.find_last_not_of(" \r\n\t*_`\"'.!");
  std::string core = lower(text.substr(a, b - a + 1));
  if (core == "[pass]" || core == "(pass)") return true; // live 21:29: "[PASS]"
  if (core.size() > 2 && core.front() == '[') { // "[Name] PASS"
    const auto c = core.find(']');
    if (c != std::string::npos) core = core.substr(c + 1);
    const auto s = core.find_first_not_of(" ");
    core = s == std::string::npos ? "" : core.substr(s);
  }
  return core == "pass" || core == "(pass)" || core == "[pass]";
}

// A reply that is only a participant name ("Noah", "[Noah]", "**Lena:**"):
// the model echoed a transcript label and stopped. Never real content.
inline bool name_only(const std::string &text, const std::vector<std::string> &names) {
  std::string core;
  for (unsigned char c : text)
    if (!std::isspace(c) && c != '[' && c != ']' && c != '(' && c != ')' &&
        c != '*' && c != '_' && c != '`' && c != '"' && c != '\'' && c != ':' &&
        c != '.' && c != '!' && c != '@' && c != ',' && c != '-')
      core.push_back(static_cast<char>(std::tolower(c)));
  if (core.empty()) return false; // empty = interrupted turn, handled elsewhere
  for (const auto &n : names) {
    std::string nn;
    for (unsigned char c : n)
      if (!std::isspace(c) && c != '[' && c != ']' && c != '(' && c != ')' &&
          c != '*' && c != '_' && c != '`' && c != '"' && c != '\'' && c != ':' &&
          c != '.' && c != '!' && c != '@' && c != ',' && c != '-')
        nn.push_back(static_cast<char>(std::tolower(c)));
    if (!nn.empty() && core == nn) return true;
  }
  return false;
}

// The room names who spoke in the prompt with "[Sprecher <Name>] ", a
// message of Chris with its clock in front ("(Mi 18.11. 14:10)\n"). A model
// copies that head into its own answer; at the start of an answer it is
// taken out, once or as often as it was copied. Nothing else is touched.
inline size_t clock_label_end(const std::string &t, size_t at) {
  // "(Mi 18.11. 14:10)": two letters, day, month, hour and minute.
  static const char shape[] = "(aa 00.00. 00:00)";
  if (t.size() < at + sizeof shape - 1) return std::string::npos;
  for (size_t i = 0; i + 1 < sizeof shape; ++i) {
    const unsigned char c = (unsigned char)t[at + i], s = (unsigned char)shape[i];
    if (s == 'a' ? !std::isalpha(c) : s == '0' ? !std::isdigit(c) : c != s) return std::string::npos;
  }
  return at + sizeof shape - 1;
}
inline size_t speaker_tag_end(const std::string &t, size_t at) {
  static const std::string open = "[Sprecher ";
  if (t.compare(at, open.size(), open) != 0) return std::string::npos;
  const size_t close = t.find(']', at + open.size());
  if (close == std::string::npos || close - at > 72 || t.find('\n', at) < close) return std::string::npos;
  return close + 1;
}
inline std::string strip_speaker_tags(const std::string &text) {
  size_t at = 0;
  for (;;) {
    size_t p = at;
    while (p < text.size() && std::isspace((unsigned char)text[p])) ++p;
    size_t end = clock_label_end(text, p);
    if (end == std::string::npos) end = speaker_tag_end(text, p);
    if (end == std::string::npos) break;
    at = end;
  }
  if (at == 0) return text;
  while (at < text.size() && std::isspace((unsigned char)text[at])) ++at;
  return text.substr(at);
}
// While streaming: could the head still turn out to be such a tag ("[Spre",
// "(Mi 18.1")? Then the bubble waits, so no part of it is ever shown.
inline bool maybe_speaker_tag(const std::string &partial) {
  const std::string t = strip_speaker_tags(partial);
  size_t p = 0;
  while (p < t.size() && std::isspace((unsigned char)t[p])) ++p;
  const std::string head = t.substr(p);
  if (head.empty()) return t.size() != partial.size(); // only tags so far
  static const std::string open = "[Sprecher ", clock = "(aa 00.00. 00:00)";
  if (head.size() < open.size()) {
    if (open.compare(0, head.size(), head) == 0) return true;
  } else if (head.compare(0, open.size(), open) == 0) {
    return head.size() <= 72 && head.find_first_of("]\n") == std::string::npos;
  }
  if (head[0] == '(' && head.size() < clock.size()) {
    for (size_t i = 0; i < head.size(); ++i) {
      const unsigned char c = (unsigned char)head[i], s = (unsigned char)clock[i];
      if (s == 'a' ? !std::isalpha(c) : s == '0' ? !std::isdigit(c) : c != s) return false;
    }
    return true;
  }
  return false;
}

// While streaming: could this still end up as a bare name ("No", "[Noah",
// "Noah (gpt-o")? Then hold the bubble; it may turn out to be silent.
inline bool maybe_name_only(const std::string &partial, const std::vector<std::string> &names) {
  const std::string c = lower(bare_token(partial));
  if (c.empty()) return true;
  for (const auto &n : names) {
    const std::string ln = lower(n);
    if (ln.empty()) continue;
    if (ln.rfind(c, 0) == 0) return true;
    if (c.rfind(ln, 0) == 0) {
      const std::string rest = c.substr(ln.size());
      if (bare_token(rest).empty()) return true;
      const auto r = rest.find_first_not_of(" ");
      if (r != std::string::npos && rest[r] == '(' && rest.size() < 48 &&
          rest.find(')') == std::string::npos) return true;
    }
  }
  return false;
}
// "PASS" on its own first line followed by more text: a model on the append-only
// complement wire sometimes continues with an invented transcript
// ("PASS\n\n- User: ..."). The first line decides.
inline bool leading_pass(const std::string &text) {
  const auto a = text.find_first_not_of(" \r\n\t");
  if (a == std::string::npos) return false;
  const auto nl = text.find('\n', a);
  if (nl == std::string::npos) return false;
  return is_pass(text.substr(a, nl - a));
}
// Consecutive PASSes: rest once every bot passed in a row.
inline bool conversation_rests(int passes_in_row, size_t roster_size) {
  return roster_size < 2 || passes_in_row >= (int)roster_size;
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
  if (t.size() < 32) // "Kein Con" may still become the "Kein Content" marker
    for (const auto &m : silent_markers())
      if (m.rfind(t, 0) == 0 || norm_reply(t) == m) return true;
  return t.rfind("pass", 0) == 0 && t.size() <= 8 &&
         (t.size() == 4 || !word_char((unsigned char)t[4]));
}

} // namespace group_chat
