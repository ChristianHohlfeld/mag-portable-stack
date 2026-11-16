// A browser goal Chris gave one bot. It lives beside the prompt log, so a
// compact of that log cannot forget the task, the page or a correction.
// Only goal_finish and goal_blocked end it. Trash and Stop end it too.
#pragma once
#include "llm.hpp"
#include "util.hpp"

#include <cmath>
#include <cstdlib>
#include <set>

namespace mag::goal {
inline double envd(const char *key, double def) {
  const std::string v = env(key);
  if (v.empty()) return def;
  char *end = nullptr;
  const double n = strtod(v.c_str(), &end);
  return end && *end == 0 ? n : def;
}
} // namespace mag::goal

namespace mag::goal {

enum class Kind { Chat, Status, Correction, Task, Stop };

struct Read {
  Kind kind = Kind::Chat;
  std::vector<std::string> hush; // bots told to stay out ("Noah du nicht")
};

inline bool word_at(const std::string &low, size_t p) {
  return p == 0 || !std::isalnum((unsigned char)low[p - 1]);
}
// `stem` at the start of a word ("nenn" in "nenne", not "such" in "versuch").
inline bool stem(const std::string &low, const std::string &s) {
  for (size_t p = low.find(s); p != std::string::npos; p = low.find(s, p + 1))
    if (word_at(low, p)) return true;
  return false;
}
inline std::string flat(std::string s) {
  s = lower(trim(s));
  for (char &c : s)
    if (c == '?' || c == '!' || c == '.' || c == ',' || c == '"' || c == '\'') c = ' ';
  while (s.find("  ") != std::string::npos) replace_all(s, "  ", " ");
  return trim(s);
}

// A task the browser is for: a shop, a site, a search, an order to open a page.
inline bool browser_task(const std::string &text) {
  const std::string low = lower(text);
  if (low.find("amazon") != std::string::npos || low.find("ebay") != std::string::npos || low.find("otto.de") != std::string::npos) return true;
  if (low.find("http://") != std::string::npos || low.find("https://") != std::string::npos || low.find("www.") != std::string::npos) return true;
  if (stem(low, "webseite") || stem(low, "website") || stem(low, "webshop") || stem(low, "browser")) return true;
  if (stem(low, "bestell") || stem(low, "kauf")) return true;
  const bool such = stem(low, "such");
  if (such && (low.find(" auf ") != std::string::npos || low.find(" bei ") != std::string::npos || low.find(" mir ") != std::string::npos ||
               low.find(" nach ") != std::string::npos || low.find("google") != std::string::npos || low.find("web") != std::string::npos))
    return true;
  if (stem(low, "nenn") && (low.find(" auf ") != std::string::npos || low.find(" bei ") != std::string::npos || low.find("amazon") != std::string::npos ||
                            low.find("ebay") != std::string::npos || stem(low, "shop")))
    return true;
  if (stem(low, "offne") || stem(low, "öffne"))
    return low.find("seite") != std::string::npos || low.find("http") != std::string::npos || low.find("www.") != std::string::npos ||
           low.find("amazon") != std::string::npos || low.find("link") != std::string::npos || low.find("browser") != std::string::npos;
  return false;
}

// Shopping or search: goal_finish must name items whose links were on a page.
inline bool needs_items(const std::string &text) {
  const std::string low = lower(text);
  return low.find("amazon") != std::string::npos || low.find("ebay") != std::string::npos || stem(low, "kauf") || stem(low, "bestell") ||
         stem(low, "such") || stem(low, "nenn") || stem(low, "preis") || stem(low, "shop") || stem(low, "produkt");
}

// "Done?", "Und?", "Hallo" — a status question, not a new task.
inline bool status_question(const std::string &text) {
  const std::string f = flat(text);
  if (f.empty() || f.size() > 48) return false;
  static const char *exact[] = {"done", "und", "hallo", "hi", "hey", "na", "ok", "okay", "danke", "bitte", "fertig", "moin", "servus",
                                "und jetzt", "wie weit", "alles klar", "status", "update", "bist du da", "bist du fertig", "bist du schon fertig",
                                "laeufts", "läufts", "laeuft es", "läuft es"};
  for (const char *e : exact)
    if (f == e) return true;
  if (f.rfind("hallo ", 0) == 0 || f.rfind("hi ", 0) == 0 || f.rfind("hey ", 0) == 0 || f.rfind("bist du", 0) == 0 || f.rfind("wie weit", 0) == 0)
    return !browser_task(text);
  if (!f.empty() && text.find('?') != std::string::npos && f.size() < 32 && !browser_task(text) && !stem(f, "nenn") && !stem(f, "such") && !stem(f, "kauf"))
    return true;
  return false;
}

inline bool correction(const std::string &text) {
  const std::string low = lower(text);
  if (low.find("stattdessen") != std::string::npos || low.find("korrektur") != std::string::npos || low.find("meinte") != std::string::npos) return true;
  return stem(low, "nicht") && low.find("sondern") != std::string::npos;
}

inline bool stop_word(const std::string &text) {
  const std::string f = flat(text);
  static const char *exact[] = {"stopp", "stop", "abbruch", "abbrechen", "hor auf", "hör auf", "lass es", "aufhoren", "aufhören", "schluss"};
  for (const char *e : exact)
    if (f == e) return true;
  return false;
}

// `bots` is (id, name). A hush does not by itself change the kind.
inline Read classify(const std::string &text, const std::vector<std::pair<std::string, std::string>> &bots) {
  Read r;
  const std::string low = lower(text);
  for (auto &b : bots) {
    const std::string name = lower(b.second), id = lower(b.first);
    const bool named = (!name.empty() && stem(low, name)) || (!id.empty() && stem(low, id));
    if (!named) continue;
    if (low.find("du nicht") != std::string::npos || low.find("nicht du") != std::string::npos || low.find("halt dich") != std::string::npos ||
        low.find("misch dich") != std::string::npos || low.find("sei still") != std::string::npos || low.find("bleib still") != std::string::npos)
      r.hush.push_back(b.first);
  }
  if (stop_word(text)) r.kind = Kind::Stop;
  else if (status_question(text)) r.kind = Kind::Status;
  else if (correction(text)) r.kind = Kind::Correction;
  else if (browser_task(text)) r.kind = Kind::Task;
  else r.kind = Kind::Chat;
  return r;
}

inline bool active(const Val &g) { return g.isObj() && jstr(g, "state") == "active"; }

inline void begin(Val &g, const std::string &owner, const std::string &text) {
  const std::string body = trim(text);
  Val next = Val::object();
  next["owner"] = owner;
  next["id"] = random_hex(8); // new task, even when the same words are used again
  next["revision"] = 0;
  next["progress"] = "running";
  next["state"] = "active";
  next["text"] = body;
  next["corrections"] = Val::array();
  next["shopping"] = needs_items(body);
  // A request for two recommendations must never succeed with only one link.
  // Other shopping tasks retain the old one-item minimum.
  const std::string low = lower(body);
  const int needed = needs_items(body) && (stem(low, "zwei") || stem(low, "two") || low.find("2 produkte") != std::string::npos) ? 2 : 1;
  next["min_items"] = needs_items(body) ? needed : 0;
  next["done"] = needs_items(body) ? "Mindestens " + std::to_string(needed) +
       " unterschiedliche konkrete Artikel, jeder mit Namen und einem Link, der auf einer gesehenen Seite stand."
       : "Das, worum Chris gebeten hat, ist erledigt und in einem Satz gesagt.";
  next["version"] = 0;
  next["page"] = Val::object();
  next["candidates"] = Val::array();
  // The model must list its task-derived requirements before it can finish.
  next["requirements"] = Val::array();
  next["evidence_pages"] = Val::array();
  next["error"] = "";
  next["seen"] = "";
  next["summary"] = "";
  g = next;
}

inline void correct(Val &g, const std::string &text) {
  if (!active(g)) return;
  const std::string line = trim(text);
  if (line.empty()) return;
  g["corrections"].push(line);
  g["revision"] = jnum(g, "revision") + 1;
  g["progress"] = "running";
  g["text"] = jstr(g, "text") + "\nKorrektur: " + line;
  // A revised user requirement invalidates the old success contract.
  // Observed pages remain available as evidence; criteria must be re-derived.
  g["requirements"] = Val::array();
  if (needs_items(line)) g["shopping"] = true;
}

inline void end(Val &g, const std::string &state) {
  if (!g.isObj()) g = Val::object();
  g["state"] = state; // "done", "blocked" or "ended"
  g["progress"] = state;
}

// An execution plan is separate from the model's success criteria. It is
// persisted with the goal and never implicitly marks anything successful.
inline bool plan_ok(const Val *steps, std::string &why) {
  if (!steps || !steps->isArr() || steps->asArr().empty() || steps->asArr().size() > 12) {
    why = "Ein Plan benötigt 1 bis 12 Schritte."; return false;
  }
  std::set<std::string> ids;
  for (const auto &step : steps->asArr()) {
    const std::string id = trim(jstr(step, "id"));
    const std::string action = trim(jstr(step, "action"));
    const std::string done = trim(jstr(step, "done_when"));
    if (id.empty() || id.size() > 40 || action.size() < 3 || action.size() > 240 ||
        done.size() < 3 || done.size() > 240 || !ids.insert(id).second) {
      why = "Jeder Schritt braucht eindeutige id, action und done_when."; return false;
    }
  }
  return true;
}
inline bool plan_complete(const Val &g) {
  const Val *steps = g.find("plan");
  if (!steps || !steps->isArr() || jnum(g, "plan_revision", -1) != jnum(g, "revision")) return true;
  for (const auto &step : steps->asArr()) if (jstr(step, "state") != "done") return false;
  return true;
}

// ---- raw tool-call markup ---------------------------------------------------
// Gemma writes `<|tool_call>call:browser_click{n:28}<tool_call|>` into its
// thoughts. The cleaner drops that. Args may use bare keys (`n:28`) or JSON.
struct Rescue {
  std::vector<llm::ToolCall> calls;
  bool markup = false;
};

inline bool balanced_object(const std::string &s, size_t open, size_t &end) {
  if (open >= s.size() || s[open] != '{') return false;
  int depth = 0;
  bool str = false, esc = false;
  for (size_t i = open; i < s.size(); ++i) {
    const char c = s[i];
    if (str) {
      if (esc) esc = false;
      else if (c == '\\') esc = true;
      else if (c == '"') str = false;
      continue;
    }
    if (c == '"') str = true;
    else if (c == '{') ++depth;
    else if (c == '}') {
      if (--depth == 0) { end = i + 1; return true; }
    }
  }
  return false;
}

// `{n:28, text:hello world}` → a JSON object. Already-JSON is kept.
inline std::string bare_object(const std::string &body) {
  const Val parsed = jparse(body);
  if (parsed.isObj()) return body;
  if (body.size() < 2 || body.front() != '{') return "";
  Val obj = Val::object();
  size_t i = 1;
  auto skip = [&] { while (i < body.size() && std::isspace((unsigned char)body[i])) ++i; };
  while (i + 1 < body.size()) {
    skip();
    if (i < body.size() && body[i] == '}') break;
    std::string key;
    if (i < body.size() && body[i] == '"') {
      const size_t q = body.find('"', i + 1);
      if (q == std::string::npos) return "";
      key = body.substr(i + 1, q - i - 1);
      i = q + 1;
    } else {
      const size_t b = i;
      while (i < body.size() && (std::isalnum((unsigned char)body[i]) || body[i] == '_')) ++i;
      if (i == b) return "";
      key = body.substr(b, i - b);
    }
    skip();
    if (i >= body.size() || body[i] != ':') return "";
    ++i;
    skip();
    if (i >= body.size()) return "";
    Val value;
    if (body[i] == '"') {
      std::string v;
      ++i;
      while (i < body.size() && body[i] != '"') {
        if (body[i] == '\\' && i + 1 < body.size()) { v += body[i + 1]; i += 2; continue; }
        v += body[i++];
      }
      if (i >= body.size() || body[i] != '"') return "";
      ++i;
      value = v;
    } else if (body[i] == '{') {
      size_t e = 0;
      if (!balanced_object(body, i, e)) return "";
      const std::string nested = bare_object(body.substr(i, e - i));
      if (nested.empty()) return "";
      value = jparse(nested);
      i = e;
    } else {
      const size_t b = i;
      while (i < body.size() && body[i] != ',' && body[i] != '}') ++i;
      std::string tok = trim(body.substr(b, i - b));
      if (tok.empty()) return "";
      if (tok == "true") value = true;
      else if (tok == "false") value = false;
      else if (tok == "null") value = Val();
      else {
        char *end = nullptr;
        const double n = strtod(tok.c_str(), &end);
        if (end && *end == 0 && end != tok.c_str()) value = n;
        else value = tok;
      }
    }
    obj[key] = value;
    skip();
    if (i < body.size() && body[i] == ',') { ++i; continue; }
    break;
  }
  return obj.isObj() && obj.obj && !obj.obj->empty() ? mj::dump(obj) : "";
}

inline void push_call(Rescue &r, const std::string &name, const std::string &args) {
  if (name.empty() || args.empty()) return;
  for (auto &c : r.calls)
    if (c.name == name && c.arguments == args) return; // the same call twice in one chunk
  llm::ToolCall call;
  call.id = "call_rescue_" + random_hex(3);
  call.name = name;
  call.arguments = args;
  call.valid = true;
  r.calls.push_back(call);
}

inline Rescue rescue(const std::string &raw) {
  Rescue r;
  if (raw.find("<|tool_call>") != std::string::npos || raw.find("<tool_call|>") != std::string::npos || raw.find("call:") != std::string::npos ||
      raw.find("functions.") != std::string::npos)
    r.markup = true;
  for (size_t p = raw.find("call:"); p != std::string::npos; p = raw.find("call:", p + 5)) {
    size_t i = p + 5;
    if (i < raw.size() && raw[i] == ':') ++i; // "call:NAME"
    const size_t nb = i;
    while (i < raw.size() && (std::isalnum((unsigned char)raw[i]) || raw[i] == '_')) ++i;
    if (i == nb) continue;
    const std::string name = raw.substr(nb, i - nb);
    while (i < raw.size() && std::isspace((unsigned char)raw[i])) ++i;
    size_t end = 0;
    if (!balanced_object(raw, i, end)) continue; // incomplete: not a call
    const std::string args = bare_object(raw.substr(i, end - i));
    if (!args.empty()) push_call(r, name, args);
  }
  // gpt-oss: to=functions.shell … {"command":"ls"}
  for (size_t p = raw.find("functions."); p != std::string::npos; p = raw.find("functions.", p + 10)) {
    size_t i = p + 10;
    const size_t nb = i;
    while (i < raw.size() && (std::isalnum((unsigned char)raw[i]) || raw[i] == '_')) ++i;
    if (i == nb) continue;
    const std::string name = raw.substr(nb, i - nb);
    const size_t brace = raw.find('{', i);
    if (brace == std::string::npos || brace - i > 800) continue;
    size_t end = 0;
    if (!balanced_object(raw, brace, end)) continue;
    const std::string args = bare_object(raw.substr(brace, end - brace));
    if (!args.empty()) push_call(r, name, args);
  }
  return r;
}

inline bool type_ok(const std::string &want, const Val &v) {
  if (want == "string") return v.type == Val::STR;
  if (want == "boolean") return v.type == Val::BOOL;
  if (want == "integer") return v.type == Val::NUM && std::isfinite(v.num) && v.num == std::floor(v.num);
  if (want == "number") return v.type == Val::NUM && std::isfinite(v.num);
  if (want == "object") return v.isObj();
  if (want == "array") return v.isArr();
  return true;
}

// The call names an offered tool and its arguments match that tool's schema.
inline bool args_ok(const Val &tools, const llm::ToolCall &call, std::string &why) {
  const Val args = jparse(call.arguments);
  if (!args.isObj()) { why = "Argumente sind kein Objekt"; return false; }
  const Val *params = nullptr;
  bool offered = false;
  for (auto &def : tools.asArr()) {
    const Val *fn = def.find("function");
    if (!fn || jstr(*fn, "name") != call.name) continue;
    offered = true;
    params = fn->find("parameters");
    break;
  }
  if (!offered) { why = "Werkzeug wird nicht angeboten"; return false; }
  if (!params || !params->isObj()) return true;
  if (const Val *req = params->find("required"); req && req->isArr())
    for (auto &k : req->asArr())
      if (!args.find(k.asStr())) { why = "Pflichtfeld fehlt: " + k.asStr(); return false; }
  if (const Val *props = params->find("properties"); props && props->isObj())
    for (auto &kv : *args.obj) {
      const Val *spec = props->find(kv.first);
      if (!spec || !spec->isObj()) continue;
      const std::string typ = jstr(*spec, "type");
      if (!typ.empty() && !type_ok(typ, kv.second)) { why = kv.first + " hat den falschen Typ"; return false; }
      if (const Val *en = spec->find("enum"); en && en->isArr() && typ == "string") {
        bool hit = false;
        for (auto &x : en->asArr()) hit = hit || x.asStr() == kv.second.asStr();
        if (!hit) { why = kv.first + " ist keiner der erlaubten Werte"; return false; }
      }
    }
  return true;
}

// Local models sometimes write a complete tool call as a single JSON
// document in ordinary text, despite tool_choice=required. Promote only an
// explicit, standalone, schema-valid call. Never execute arbitrary prose,
// JSON examples embedded in prose or unrecognized actions.
inline bool text_tool_call(std::string raw, const Val &offered, llm::ToolCall &call) {
  raw = trim(raw);
  if (starts_with(raw, "```")) {
    const size_t first = raw.find('\n');
    if (first == std::string::npos) return false;
    const size_t close = raw.find("```", first + 1);
    if (close == std::string::npos || !trim(raw.substr(close + 3)).empty()) return false;
    raw = trim(raw.substr(first + 1, close - first - 1));
  }
  if (raw.size() < 10 || raw.size() > 16000 || raw.front() != '{' || raw.back() != '}') return false;
  const Val obj = jparse(raw);
  if (!obj.isObj()) return false;
  std::string name = jstr(obj, "name", jstr(obj, "tool", jstr(obj, "function")));
  Val args = Val::object();
  if (!name.empty()) {
    const Val *arg = obj.find("arguments");
    if (!arg) arg = obj.find("args");
    if (!arg) arg = obj.find("parameters");
    if (!arg) return false;
    if (arg->isObj()) args = *arg;
    else if (arg->type == Val::STR) args = jparse(arg->str);
    else return false;
  } else {
    name = jstr(obj, "action");
    if (name.empty()) return false;
    for (const auto &kv : *obj.obj)
      if (kv.first != "action") args[kv.first] = kv.second;
  }
  if (!args.isObj()) return false;
  // Textual JSON is not native tool calling. Recover only read-only page
  // observations and server-validated goal bookkeeping; never a shell,
  // click, form submit, purchase, plugin write or other side effect.
  static const std::set<std::string> safe = {
      "goal_requirements", "goal_plan", "goal_step", "goal_finish",
      "goal_blocked", "browser_look", "browser_read"};
  if (!safe.count(name)) return false;
  llm::ToolCall parsed;
  parsed.id = "call_json_" + random_hex(5);
  parsed.name = name;
  parsed.arguments = mj::dump(args);
  parsed.valid = true;
  std::string why;
  if (!args_ok(offered, parsed, why)) return false;
  call = parsed;
  return true;
}

// ---- page memory ------------------------------------------------------------
inline void absorb(Val &g, const Val &page, int version) {
  if (!active(g) || !page.isObj()) return;
  Val p = Val::object();
  p["url"] = jstr(page, "url");
  p["title"] = jstr(page, "title");
  p["scroll"] = jnum(page, "scroll");
  p["version"] = version;
  p["blocked"] = jbool(page, "blocked");
  p["text"] = clip(jstr(page, "text"), 2500);
  if (const Val *els = page.find("elements"); els && els->isArr()) p["elements"] = *els;
  else if (const Val *old = g.find("page"); old && jstr(*old, "url") == jstr(page, "url") && old->find("elements"))
    p["elements"] = *old->find("elements");
  else p["elements"] = Val::array();
  if (const Val *links = page.find("links"); links && links->isArr()) p["links"] = *links;
  else if (const Val *old = g.find("page"); old && jstr(*old, "url") == jstr(page, "url") && old->find("links"))
    p["links"] = *old->find("links");
  else p["links"] = Val::array();
  g["page"] = p;
  g["version"] = version;
  std::string add = jstr(page, "url") + "\n" + jstr(page, "title") + "\n" + clip(jstr(page, "text"), 2000);
  Val cands = Val::array();
  if (const Val *els = page.find("elements"); els && els->isArr())
    for (auto &e : els->asArr()) {
      const std::string href = jstr(e, "href"), price = jstr(e, "price"), label = jstr(e, "label");
      add += "\n" + label + " " + href + " " + price;
      const bool product = !price.empty() || href.find("/dp/") != std::string::npos || href.find("/gp/") != std::string::npos;
      if (product && cands.arr->size() < 12) {
        Val c = Val::object();
        c["n"] = jnum(e, "n");
        c["name"] = label;
        if (!price.empty()) c["price"] = price;
        if (!href.empty()) c["url"] = href;
        cands.push(c);
      }
    }
  // The browser sends two independent kinds of evidence: current viewport
  // click targets and actual rendered DOM hyperlinks (possibly below fold).
  // Record both. Never infer a URL from a title, price or shop-specific path.
  if (const Val *links = p.find("links"); links && links->isArr()) {
    std::set<std::string> recorded;
    for (const auto &c : cands.asArr()) recorded.insert(jstr(c, "url"));
    size_t added = 0;
    for (const auto &link : links->asArr()) {
      const std::string href = jstr(link, "url"), label = jstr(link, "name");
      if ((!starts_with(href, "https://") && !starts_with(href, "http://")) || href.size() > 700) continue;
      if (added++ < 48) add += "\n" + label + " " + href + " " + jstr(link, "price");
      if (cands.asArr().size() < 12 && !label.empty() && recorded.insert(href).second) {
        Val c = Val::object();
        c["name"] = label;
        c["url"] = href;
        if (!jstr(link, "price").empty()) c["price"] = jstr(link, "price");
        cands.push(c);
      }
    }
  }
  g["candidates"] = cands;
  std::string seen = jstr(g, "seen") + "\n" + add;
  if (seen.size() > 80000) seen = seen.substr(seen.size() - 80000);
  g["seen"] = seen;
  // Keep bounded, provenance-bearing evidence separate from model-facing
  // summaries. URL and quote must come from the SAME observed page.
  Val proof_page = Val::object();
  proof_page["url"] = jstr(page, "url");
  std::string visible = clip(jstr(page, "text"), 6000);
  if (const Val *els = page.find("elements"); els && els->isArr())
    for (const auto &e : els->asArr()) {
      visible += "\n" + clip(jstr(e, "label"), 240);
      if (!jstr(e, "price").empty()) visible += " " + clip(jstr(e, "price"), 80);
    }
  // Preserve clickable URLs with their observed labels/prices on EACH
  // snapshot. A later navigation must not erase earlier result links.
  Val links = Val::array();
  std::set<std::string> known;
  const auto append_link = [&](const std::string &href, const std::string &label, const std::string &price) {
    if ((!starts_with(href, "https://") && !starts_with(href, "http://")) ||
        href.size() > 700 || links.asArr().size() >= 64 || !known.insert(href).second) return;
    Val link = Val::object();
    link["url"] = href;
    link["name"] = clip(label, 180);
    link["price"] = clip(price, 60);
    links.push(link);
  };
  if (const Val *dom = p.find("links"); dom && dom->isArr())
    for (const auto &link : dom->asArr()) {
      append_link(jstr(link, "url"), jstr(link, "name"), jstr(link, "price"));
      if (links.asArr().size() >= 48) break;
    }
  if (const Val *els = p.find("elements"); els && els->isArr())
    for (const auto &e : els->asArr())
      append_link(jstr(e, "href"), jstr(e, "label"), jstr(e, "price"));
  for (const auto &link : links.asArr()) {
    visible += "\n" + jstr(link, "name");
    if (!jstr(link, "price").empty()) visible += " " + jstr(link, "price");
  }
  proof_page["text"] = clip(visible, 16000);
  proof_page["links"] = links;
  if (!g["evidence_pages"].isArr()) g["evidence_pages"] = Val::array();
  g["evidence_pages"].push(proof_page);
  auto &pages = *g["evidence_pages"].arr;
  if (pages.size() > 24) pages.erase(pages.begin(), pages.begin() + (pages.size() - 24));
}

// Pure validators: no model trust is needed to check that a proof quotes the
// actual visible contents of the claimed browser page.
inline bool requirements_ok(const Val *requirements, std::string &why) {
  if (!requirements || !requirements->isArr() || requirements->asArr().empty() || requirements->asArr().size() > 16) {
    why = "1 bis 16 überprüfbare Anforderungen sind nötig."; return false;
  }
  std::set<std::string> ids;
  for (const auto &r : requirements->asArr()) {
    const std::string id = trim(jstr(r, "id")), need = trim(jstr(r, "need"));
    if (!r.isObj() || id.empty() || id.size() > 40 || need.size() < 4 || need.size() > 300 || !ids.insert(id).second) {
      why = "Jede Anforderung braucht eine eindeutige id und einen verständlichen Text."; return false;
    }
  }
  return true;
}
inline bool proofs_ok(const Val &g, const Val *proofs, std::string &why) {
  const Val *required = g.find("requirements");
  if (!requirements_ok(required, why)) { why = "Lege zuerst mit goal_requirements alle Erfolgskriterien fest."; return false; }
  if (!proofs || !proofs->isArr() || proofs->asArr().size() != required->asArr().size()) {
    why = "Jede Anforderung braucht genau einen Seitenbeleg (id, url, quote)."; return false;
  }
  std::set<std::string> covered;
  for (const auto &p : proofs->asArr()) {
    const std::string id = trim(jstr(p, "id")), url = trim(jstr(p, "url")), quote = trim(jstr(p, "quote"));
    if (id.empty() || url.empty() || quote.size() < 6 || quote.size() > 500 || !covered.insert(id).second) {
      why = "Ungültiger oder doppelter Beleg (id, url, quote)."; return false;
    }
    bool known = false, found = false;
    for (const auto &r : required->asArr()) if (jstr(r, "id") == id) known = true;
    if (const Val *pages = g.find("evidence_pages"); pages && pages->isArr())
      for (const auto &page : pages->asArr())
        if (jstr(page, "url") == url && jstr(page, "text").find(quote) != std::string::npos) found = true;
    if (!known || !found) {
      why = "Unbelegte Anforderung " + clip(id, 60) + ": Textstelle und URL müssen auf derselben gesehenen Seite stehen.";
      return false;
    }
  }
  return true;
}

inline bool has_element(const Val &g, long n) {
  const Val *page = g.find("page");
  if (!page || !page->isObj()) return true; // nothing seen yet: the browser still has the numbers
  const Val *els = page->find("elements");
  if (!els || !els->isArr() || els->asArr().empty()) return true;
  for (auto &e : els->asArr())
    if ((long)jnum(e, "n") == n) return true;
  return false;
}

inline bool url_seen(const Val &g, std::string url) {
  url = lower(trim(url));
  while (!url.empty() && url.back() == '/') url.pop_back();
  if (url.size() < 8 || url.compare(0, 4, "http") != 0) return false;
  const std::string seen = lower(jstr(g, "seen"));
  if (seen.find(url) != std::string::npos) return true;
  const auto q = url.find('?');
  return q != std::string::npos && seen.find(url.substr(0, q)) != std::string::npos;
}

// Prepare a small, provenance-labelled finishing context instead of forcing
// another full browser exploration when a CPU model replies without a tool.
// The model still chooses the answer; these are untrusted observations only.
inline std::string finish_context(const Val &g) {
  std::string out = "OFFENER AUFTRAG: " + clip(jstr(g, "text"), 500) + "\n";
  if (const Val *req = g.find("requirements"); req && req->isArr() && !req->asArr().empty())
    out += "Erfolgskriterien: " + clip(mj::dump(*req), 900) + "\n";
  else out += "Zuerst goal_requirements mit allen Nutzerkriterien aufrufen.\n";
  if (const Val *plan = g.find("plan"); plan && plan->isArr() && !plan->asArr().empty())
    out += "Planstand: " + clip(mj::dump(*plan), 550) + "\n";
  if (const Val *pages = g.find("evidence_pages"); pages && pages->isArr()) {
    const size_t start = pages->asArr().size() > 3 ? pages->asArr().size() - 3 : 0;
    for (size_t i = start; i < pages->asArr().size(); ++i) {
      const Val &p = pages->asArr()[i];
      out += "QUELLE " + std::to_string(i + 1) + ": " + clip(jstr(p, "url"), 260) +
             "\nSICHTBARER TEXT (Zitat exakt daraus): " + clip(jstr(p, "text"), 600) + "\n";
      if (const Val *links = p.find("links"); links && links->isArr()) {
        int count = 0;
        for (const auto &link : links->asArr()) {
          if (++count > 4) break;
          out += "LINK " + clip(jstr(link, "name"), 100) + " | " +
                 clip(jstr(link, "price"), 35) + " | " + clip(jstr(link, "url"), 200) + "\n";
        }
      }
    }
  }
  const std::string instruction =
      "\nNächster Schritt: exakt EINEN echten Werkzeugaufruf ausführen. Ist das Ergebnis belegt, "
      "goal_finish mit proofs (id, wörtliches quote, URL derselben Quelle), und wenn konkrete "
      "Links gefordert sind mit items (name, url, price). Fehlen Kriterien, goal_requirements; "
      "fehlt eine überprüfte Ziel-URL, das zugehörige Link-Element öffnen und die Ziel-URL "
      "beobachten. Keine URL oder Textstelle erfinden.";
  return clip(out, 4000) + instruction;
}

// A failed or missing tool call must never discard witnessed clickable links.
// Returning them is an explicitly provisional recovery, never a false "done".
inline std::string provisional_sources(const Val &g) {
  std::string out = "Abschluss noch nicht verifiziert. Diese tatsächlich beobachteten Links bleiben verfügbar:";
  std::set<std::string> used;
  int n = 0;
  const auto add = [&](const Val &c, std::string &dest, std::set<std::string> &unique, int &count) {
    const std::string url = trim(jstr(c, "url"));
    if ((!starts_with(url, "https://") && !starts_with(url, "http://")) ||
        !unique.insert(url).second) return;
    dest += "\n- " + clip(jstr(c, "name"), 110) + " — " + clip(url, 300);
    if (!jstr(c, "price").empty()) dest += " (" + clip(jstr(c, "price"), 45) + ")";
    ++count;
  };
  if (const Val *cs = g.find("candidates"); cs && cs->isArr())
    for (const auto &c : cs->asArr()) {
      if (n >= 5) break;
      add(c, out, used, n);
    }
  if (const Val *pages = g.find("evidence_pages"); pages && pages->isArr())
    for (auto it = pages->asArr().rbegin(); it != pages->asArr().rend() && n < 5; ++it)
      if (const Val *links = it->find("links"); links && links->isArr())
        for (const auto &link : links->asArr()) {
          if (n >= 5) break;
          add(link, out, used, n);
        }
  if (n == 0) {
    const Val *page = g.find("page");
    const std::string source = page ? jstr(*page, "url") : "";
    if (starts_with(source, "https://") || starts_with(source, "http://"))
      out += "\n- Gesehene Seite: " + clip(source, 380);
    else out += "\n- Keine überprüfbare Ziel-URL gespeichert.";
  }
  out += "\nDer Auftrag bleibt offen; die Auswahl ist noch nicht als Ergebnis bestätigt.";
  return out;
}

inline bool login_page(const Val &g) {
  const Val *page = g.find("page");
  if (!page || !page->isObj()) return false;
  if (jbool(*page, "blocked")) return true;
  const std::string title = lower(jstr(*page, "title"));
  if (title.find("anmeld") != std::string::npos || title.find("sign in") != std::string::npos || title.find("login") != std::string::npos) return true;
  if (const Val *els = page->find("elements"))
    for (auto &e : els->asArr())
      if (jbool(e, "password")) return true;
  return false;
}

// The lines appended at the end of a request. Not part of the stored log.
inline std::string tail(const Val &g, bool closing) {
  if (closing)
    return "(Notiz des Raums, nicht von Chris: Der Auftrag ist abgeschlossen. Schreib Chris jetzt ein bis zwei Sätze auf Deutsch mit dem Ergebnis. "
           "Kein Werkzeug, keine neuen Angaben.)";
  if (!active(g)) return "";
  std::string s = "AKTIVER AUFTRAG: " + jstr(g, "text") + "\nERFOLG: " + jstr(g, "done") + "\n";
  if (const Val *page = g.find("page"); page && page->isObj() && !jstr(*page, "url").empty()) {
    s += "BISHER: Seite-Version " + std::to_string((long)jnum(*page, "version")) + ", " + jstr(*page, "title") + ", " + jstr(*page, "url");
    if (jnum(*page, "scroll") > 0) s += ", gescrollt " + std::to_string((long)jnum(*page, "scroll")) + " px";
    s += "\n";
    if (const Val *els = page->find("elements"); els && els->isArr()) {
      int n = 0;
      for (auto &e : els->asArr()) {
        if (n++ >= 40) break;
        s += std::to_string((long)jnum(e, "n")) + " " + jstr(e, "kind") + " \"" + clip(jstr(e, "label"), 80) + "\"";
        if (!jstr(e, "price").empty()) s += " " + jstr(e, "price");
        if (!jstr(e, "href").empty()) s += " " + jstr(e, "href");
        s += "\n";
      }
    }
  }
  if (const Val *cs = g.find("candidates"); cs && cs->isArr() && !cs->asArr().empty()) {
    s += "Kandidaten:\n";
    for (auto &c : cs->asArr())
      s += "- " + jstr(c, "name") + (jstr(c, "price").empty() ? "" : " " + jstr(c, "price")) + (jstr(c, "url").empty() ? "" : " " + jstr(c, "url")) + "\n";
  }
  if (!jstr(g, "error").empty()) s += "Letzter Fehler: " + clip(jstr(g, "error"), 300) + "\n";
  s += "Regel: Solange dieser Auftrag offen ist, tust du jetzt den nächsten Werkzeugschritt. Ein Plan oder eine Ankündigung ist kein Ergebnis und "
       "beendet nichts. Beende nur mit goal_finish oder goal_blocked. Erfinde keine Produktdaten und keine Links: nur, was hier oder auf einer "
       "gesehenen Seite stand. Nach goal_finish ein bis zwei Sätze auf Deutsch an Chris.";
  return s;
}

// ---- compact ----------------------------------------------------------------
// Token estimate, not bytes. Older page dumps go first. The goal text, every
// correction and the last two tool pairs stay. Never only the system line and a nudge.
inline size_t tokens_of(const Val &m) { return mj::dump(m).size() / 4 + 1; }

inline int shrink(Val &msgs, const Val &g, size_t budget) {
  if (!msgs.isArr() || msgs.arr->size() < 4) return 0;
  auto &a = *msgs.arr;
  std::vector<size_t> pairs;
  for (size_t i = 1; i < a.size(); ++i)
    if (jstr(a[i], "role") == "assistant" && a[i].find("tool_calls") && a[i].find("tool_calls")->isArr()) pairs.push_back(i);
  size_t tail_from = a.size();
  if (!pairs.empty()) tail_from = pairs.size() >= 2 ? pairs[pairs.size() - 2] : pairs.back();
  auto anchored = [&](const Val &m) {
    const std::string c = jstr(m, "content");
    std::string text = jstr(g, "text");
    const auto nl = text.find('\n');
    if (nl != std::string::npos) text = text.substr(0, nl);
    const size_t n = std::min(text.size(), (size_t)80);
    if (n >= 16 && c.find(text.substr(0, n)) != std::string::npos) return true;
    if (const Val *cs = g.find("corrections"))
      for (auto &x : cs->asArr())
        if (x.asStr().size() >= 8 && c.find(x.asStr()) != std::string::npos) return true;
    return false;
  };
  auto page_dump = [](const std::string &c) {
    return c.size() >= 400 && (c.find("Seite-Version:") != std::string::npos || (c.find("URL:") != std::string::npos && c.find("Elemente") != std::string::npos));
  };
  int changed = 0;
  for (size_t i = 1; i < tail_from && i < a.size(); ++i) {
    if (jstr(a[i], "role") != "tool") continue;
    const std::string c = jstr(a[i], "content");
    if (!page_dump(c)) continue;
    a[i]["content"] = "Ältere Seite, verworfen. Es gilt die letzte Seite im Auftrag.";
    ++changed;
  }
  auto shielded = [&](size_t i) {
    if (i == 0 || i >= a.size()) return true;
    if (i >= tail_from) return true;
    return anchored(a[i]);
  };
  auto total = [&] {
    size_t t = 0;
    for (auto &m : a) t += tokens_of(m);
    return t;
  };
  while (a.size() > 3 && total() > budget) {
    size_t i = 1;
    while (i < a.size() && shielded(i)) ++i;
    if (i >= a.size()) break;
    size_t end = i + 1;
    if (jstr(a[i], "role") == "assistant" && a[i].find("tool_calls")) {
      while (end < a.size() && jstr(a[end], "role") == "tool") ++end;
      bool hit = false;
      for (size_t k = i; k < end; ++k)
        if (shielded(k)) hit = true;
      if (hit) break;
    }
    int real = 0;
    for (size_t k = 1; k < a.size(); ++k) {
      if (k >= i && k < end) continue;
      if (jstr(a[k], "content").find("(Notiz des Raums") == 0) continue;
      ++real;
    }
    if (real < 1) break;
    a.erase(a.begin() + (long)i, a.begin() + (long)end);
    changed += (int)(end - i);
    if (tail_from >= end) tail_from -= end - i;
    else if (tail_from > i) tail_from = i;
  }
  return changed;
}

} // namespace mag::goal
