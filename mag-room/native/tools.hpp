// The bots' tools: a short, fixed list (stable order, so the prompt prefix
// stays cacheable). Each call returns the text the model reads; what the
// user sees (real command, live output, runtime) is the step in the message.
#pragma once
#include "room.hpp"
#include "vm.hpp"

namespace mag::tools {

struct Ctx {
  Room &room;
  vm::Guests &guests;
  Val bot;                 // bot record (id, name, vm, ...)
  std::string message_id;  // the bot message this call belongs to
  const Cancel *cancel = nullptr;
  bool web = true;         // room setting: internet tools allowed
  std::function<void(const std::string &)> output;       // live output of the step
  std::function<void(const Val &)> attach;               // add a file to the bot message
};

struct Result {
  std::string text; // goes back to the model
  bool ok = true;
};

inline Val fn(const char *name, const char *description, const char *params_json) {
  Val f = Val::object();
  f["name"] = name;
  f["description"] = description;
  f["parameters"] = jparse(params_json);
  Val t = Val::object();
  t["type"] = "function";
  t["function"] = f;
  return t;
}

// Same list, same order, for every request of a bot.
inline Val schemas(bool has_vm) {
  Val list = Val::array();
  if (has_vm) {
    list.push(fn("shell",
                 "Run a bash command as user bot in your own Linux VM (home /home/bot). Output is shown live. "
                 "Use it to inspect, build, test, download, use git.",
                 R"({"type":"object","properties":{"command":{"type":"string","description":"bash command or script"},"timeout_s":{"type":"integer","description":"limit in seconds, default 300, at most 1800"}},"required":["command"]})"));
    list.push(fn("write_file",
                 "Create or replace a text file in your VM (relative paths are below /home/bot). Prefer this over heredocs.",
                 R"({"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]})"));
    list.push(fn("browser_open", "Open a URL in your own VM browser (real Chrome, persistent profile).",
                 R"({"type":"object","properties":{"url":{"type":"string"}},"required":["url"]})"));
    list.push(fn("browser_read", "Read URL, title and visible text of the current page.",
                 R"({"type":"object","properties":{}})"));
    list.push(fn("browser_click", "Click an element by CSS selector or by its exact visible text.",
                 R"({"type":"object","properties":{"selector":{"type":"string"},"text":{"type":"string"}}})"));
    list.push(fn("browser_type", "Type text into the focused field or into the field given by a CSS selector.",
                 R"({"type":"object","properties":{"text":{"type":"string"},"selector":{"type":"string"},"enter":{"type":"boolean","description":"press Enter afterwards"}},"required":["text"]})"));
    list.push(fn("browser_scroll", "Scroll the page vertically; positive dy scrolls down.",
                 R"({"type":"object","properties":{"dy":{"type":"integer"}}})"));
    list.push(fn("browser_back", "Go back one page.", R"({"type":"object","properties":{}})"));
    list.push(fn("browser_screenshot", "Take a screenshot of your browser and post it in the chat.",
                 R"({"type":"object","properties":{}})"));
    list.push(fn("share_file", "Post a file from your VM in the chat so Chris can open or download it.",
                 R"({"type":"object","properties":{"path":{"type":"string"},"name":{"type":"string"}},"required":["path"]})"));
  }
  list.push(fn("web_search", "Search the web (Brave) for current facts; returns sources with excerpts.",
               R"({"type":"object","properties":{"query":{"type":"string"}},"required":["query"]})"));
  list.push(fn("read_image", "Read the text in an image from the chat (OCR). You cannot see images otherwise.",
               R"({"type":"object","properties":{"name":{"type":"string","description":"file name of the image"}},"required":["name"]})"));
  list.push(fn("remember", "Store one durable fact in your long-term memory (kept when the room is emptied).",
               R"({"type":"object","properties":{"fact":{"type":"string"}},"required":["fact"]})"));
  return list;
}

// Short label of a call for the step card.
inline std::string title(const std::string &name, const Val &args) {
  auto one = [](std::string s, size_t max) {
    for (auto &c : s) if (c == '\n' || c == '\r' || c == '\t') c = ' ';
    return clip(trim(s), max, "…");
  };
  if (name == "shell") return "$ " + one(jstr(args, "command"), 140);
  if (name == "write_file") return "Schreibt " + one(jstr(args, "path"), 100);
  if (name == "browser_open") return "Öffnet " + one(jstr(args, "url"), 120);
  if (name == "browser_read") return "Liest die Seite";
  if (name == "browser_click") return "Klickt " + one(jstr(args, "selector", jstr(args, "text")), 80);
  if (name == "browser_type") return "Tippt " + one(jstr(args, "text"), 60);
  if (name == "browser_scroll") return "Scrollt";
  if (name == "browser_back") return "Zurück";
  if (name == "browser_screenshot") return "Screenshot";
  if (name == "share_file") return "Teilt " + one(jstr(args, "path"), 100);
  if (name == "web_search") return "Sucht: " + one(jstr(args, "query"), 120);
  if (name == "read_image") return "Liest Bild " + one(jstr(args, "name"), 80);
  if (name == "remember") return "Merkt sich etwas";
  return name;
}

// Head and tail of a long output, with the place where the rest can be read.
inline std::string for_model(const std::string &out, const std::string &log_path, size_t budget = 6000) {
  const std::string clean = mj::utf8_fix(out);
  if (clean.size() <= budget) return clean;
  std::string note = "\n[… " + std::to_string(clean.size() - budget) + " Bytes ausgelassen";
  if (!log_path.empty()) note += "; vollständig in " + log_path;
  note += " …]\n";
  return mj::utf8_cut(clean, budget / 2) + note + tail(clean, budget / 2);
}

inline Result fail(const std::string &text) { return {"Fehler: " + text, false}; }

inline Result browser_result(const http::ClientResult &r, size_t budget = 6000) {
  if (!r.error.empty()) return fail("Browser nicht erreichbar: " + r.error);
  if (r.status != 200) return fail("Browser HTTP " + std::to_string(r.status) + ": " + clip(jstr(jparse(r.body), "error", r.body), 300));
  Val v = jparse(r.body);
  if (v.isObj()) {
    // Transport details are of no use to the model.
    Val slim = Val::object();
    for (auto &kv : *v.obj)
      if (kv.first != "udp_transport" && kv.first != "udp_port") slim[kv.first] = kv.second;
    return {clip(mj::dump(slim), budget, " …[gekürzt]"), true};
  }
  return {clip(r.body, budget, " …[gekürzt]"), true};
}

inline Result web_search(const Ctx &ctx, const std::string &query) {
  if (!ctx.web) return fail("Internet ist in den Einstellungen ausgeschaltet.");
  const std::string key = ctx.room.cfg.brave_key;
  if (key.empty()) return fail("Websuche ist nicht eingerichtet (kein Brave-Schlüssel).");
  if (trim(query).empty() || query.size() > 600) return fail("Suchanfrage fehlt oder ist zu lang.");
  http::ClientOptions opt;
  opt.direct = false; // through the audit web proxy of the unit environment
  opt.timeout_s = 30;
  opt.cancel = ctx.cancel;
  opt.headers = {{"Accept", "application/json"}, {"X-Subscription-Token", key}};
  const auto res = http::fetch("https://api.search.brave.com/res/v1/llm/context?q=" + url_encode(query) +
                                   "&country=DE&search_lang=de&maximum_number_of_urls=5&maximum_number_of_snippets=15",
                               opt);
  if (!res.ok()) return fail("Suche nicht erreichbar (" + (res.error.empty() ? "HTTP " + std::to_string(res.status) : res.error) + ").");
  Val v = jparse(res.body);
  const Val *grounding = v.find("grounding");
  const Val *generic = grounding ? grounding->find("generic") : nullptr;
  if (!generic || !generic->isArr() || generic->asArr().empty()) return fail("Keine Treffer.");
  std::string out;
  int n = 0;
  for (auto &hit : generic->asArr()) {
    const std::string url = jstr(hit, "url");
    if (!starts_with(url, "http")) continue;
    out += "## " + clip(jstr(hit, "title"), 200) + "\n" + clip(url, 500) + "\n";
    if (const Val *snips = hit.find("snippets"); snips && snips->isArr()) {
      size_t used = 0;
      for (auto &s : snips->asArr()) {
        if (used > 1400) break;
        const std::string text = clip(s.asStr(), 700, "…");
        out += text + "\n";
        used += text.size();
      }
    }
    out += "\n";
    if (++n >= 5) break;
  }
  if (out.empty()) return fail("Keine Treffer.");
  return {clip(out, 7000, "\n…[gekürzt]"), true};
}

inline Result read_image(const Ctx &ctx, const std::string &name) {
  // Newest attachment whose name matches (exact first, then case-insensitive part).
  std::string stored;
  const std::string want = lower(trim(name));
  const auto all = ctx.room.all();
  for (int pass = 0; pass < 2 && stored.empty(); ++pass)
    for (auto it = all.rbegin(); it != all.rend() && stored.empty(); ++it)
      if (const Val *atts = it->find("attachments"); atts && atts->isArr())
        for (auto &a : atts->asArr()) {
          if (jstr(a, "kind") != "image") continue;
          const std::string n = lower(jstr(a, "name"));
          if (pass == 0 ? n == want : (!want.empty() && n.find(want) != std::string::npos)) { stored = jstr(a, "file"); break; }
        }
  const std::string path = ctx.room.file_path(stored);
  if (path.empty() || !exists(path)) return fail("Kein Bild mit diesem Namen im Chat.");
  RunOptions opt;
  opt.timeout_s = 120;
  opt.cancel = ctx.cancel;
  const RunResult r = run({"tesseract", path, "stdout", "-l", "deu+eng", "--psm", "3"}, opt);
  if (r.code != 0) return fail("Texterkennung fehlgeschlagen.");
  const std::string text = trim(r.out);
  return {text.empty() ? "(Kein Text im Bild erkannt.)" : clip(text, 8000, "\n…[gekürzt]"), true};
}

inline Result call(const std::string &name, const Val &args, Ctx &ctx) {
  const std::string vm = jstr(ctx.bot, "vm");
  const bool has_vm = vm::valid_name(vm);
  auto need_vm = [&]() -> bool { return has_vm; };

  if (name == "shell") {
    if (!need_vm()) return fail("Du hast keine eigene VM.");
    const std::string command = jstr(args, "command");
    if (trim(command).empty()) return fail("Kein Befehl angegeben.");
    int timeout = (int)jnum(args, "timeout_s", 300);
    timeout = std::max(5, std::min(timeout, std::min(1800, ctx.room.cfg.tool_timeout_s * 2)));
    const auto r = ctx.guests.exec(vm, command, timeout, ctx.cancel, ctx.output);
    if (r.failed_to_start) return fail(r.out);
    if (r.cancelled) return {"abgebrochen (Stop)\n" + for_model(r.out, r.log_path, 2000), false};
    if (r.timed_out)
      return {"Zeitlimit von " + std::to_string(timeout) + " s überschritten; der Befehl wurde beendet.\n" +
                  for_model(r.out, r.log_path, 3000), false};
    return {"exit=" + std::to_string(r.code) + "\n" + for_model(r.out, r.log_path), r.code == 0};
  }
  if (name == "write_file") {
    if (!need_vm()) return fail("Du hast keine eigene VM.");
    const std::string path = jstr(args, "path");
    const Val *content = args.find("content");
    if (path.empty() || !content || content->type != Val::STR) return fail("path und content sind nötig.");
    const auto r = ctx.guests.write(vm, path, content->str, false, ctx.cancel);
    if (r.cancelled) return {"abgebrochen (Stop)", false};
    if (r.code != 0) return fail("Schreiben fehlgeschlagen: " + clip(trim(r.out), 600));
    return {clip(trim(r.out), 400), true};
  }
  if (starts_with(name, "browser_")) {
    if (!need_vm()) return fail("Du hast keine eigene VM.");
    if (!ctx.web && name == "browser_open") return fail("Internet ist in den Einstellungen ausgeschaltet.");
    if (name == "browser_open") {
      const std::string url = jstr(args, "url");
      if (url.empty()) return fail("url fehlt.");
      return browser_result(ctx.guests.browser(vm, "GET", "/navigate?url=" + url_encode(url), "", 60, ctx.cancel), 1500);
    }
    if (name == "browser_read") return browser_result(ctx.guests.browser(vm, "GET", "/read", "", 30, ctx.cancel), 9000);
    if (name == "browser_back") return browser_result(ctx.guests.browser(vm, "GET", "/back", "", 30, ctx.cancel), 1500);
    if (name == "browser_click") {
      Val body = Val::object();
      if (!jstr(args, "selector").empty()) body["selector"] = jstr(args, "selector");
      else if (!jstr(args, "text").empty()) body["text"] = jstr(args, "text");
      else return fail("selector oder text ist nötig.");
      return browser_result(ctx.guests.browser(vm, "POST", "/click", mj::dump(body), 45, ctx.cancel), 1500);
    }
    if (name == "browser_type") {
      Val body = Val::object();
      body["text"] = jstr(args, "text");
      if (!jstr(args, "selector").empty()) body["selector"] = jstr(args, "selector");
      body["enter"] = jbool(args, "enter");
      return browser_result(ctx.guests.browser(vm, "POST", "/type", mj::dump(body), 60, ctx.cancel), 1500);
    }
    if (name == "browser_scroll") {
      Val body = Val::object();
      body["dy"] = std::to_string((int)jnum(args, "dy", 600)); // text: older controllers ignore a number
      return browser_result(ctx.guests.browser(vm, "POST", "/scroll", mj::dump(body), 30, ctx.cancel), 1500);
    }
    if (name == "browser_screenshot") {
      const auto r = ctx.guests.browser(vm, "GET", "/screenshot.png", "", 60, ctx.cancel);
      if (!r.ok() || r.body.size() < 100) return fail("Kein Screenshot erhalten.");
      Val att = ctx.room.store_file("browser.png", r.body, jstr(ctx.bot, "id"));
      if (!att.isObj()) return fail("Screenshot konnte nicht gespeichert werden.");
      if (ctx.attach) ctx.attach(att);
      return {"Screenshot im Chat gepostet (" + jstr(att, "name") + ").", true};
    }
    return fail("Unbekanntes Browser-Werkzeug.");
  }
  if (name == "share_file") {
    if (!need_vm()) return fail("Du hast keine eigene VM.");
    const std::string path = jstr(args, "path");
    if (path.empty()) return fail("path fehlt.");
    std::string label = jstr(args, "name");
    if (label.empty()) label = path.substr(path.find_last_of('/') == std::string::npos ? 0 : path.find_last_of('/') + 1);
    // The VM uploads to the bridge with its own token; the bridge attaches the
    // file to this bot's current message.
    const std::string script = "set -e\nf=$(readlink -f -- " + shell_quote(path) + ")\ntest -f \"$f\"\n" +
                               "curl -fsS --max-time 120 -X POST --data-binary @\"$f\" " +
                               shell_quote(ctx.guests.bridge("/ingest?token=" + ctx.guests.token(vm) + "&name=" + url_encode(label)));
    const auto r = ctx.guests.exec(vm, script, 150, ctx.cancel, {});
    if (r.cancelled) return {"abgebrochen (Stop)", false};
    if (r.code != 0) return fail("Teilen fehlgeschlagen (Datei vorhanden und kleiner als 64 MB?): " + clip(trim(r.out), 300));
    return {"Datei im Chat gepostet: " + label, true};
  }
  if (name == "web_search") return web_search(ctx, jstr(args, "query"));
  if (name == "read_image") return read_image(ctx, jstr(args, "name"));
  if (name == "remember") {
    const std::string fact = clip(trim(jstr(args, "fact")), 500);
    if (fact.empty()) return fail("fact fehlt.");
    bool duplicate = false;
    ctx.room.update_bot(jstr(ctx.bot, "id"), [&](Val &b) {
      Val &mem = b["memory"];
      if (!mem.isArr()) mem = Val::array();
      for (auto &m : mem.asArr())
        if (jstr(m, "text") == fact) duplicate = true;
      if (duplicate) return;
      Val entry = Val::object();
      entry["ts"] = (double)now_s();
      entry["text"] = fact;
      mem.push(entry);
      while (mem.arr->size() > 60) mem.arr->erase(mem.arr->begin());
    });
    return {duplicate ? "Schon gemerkt." : "Gemerkt.", true};
  }
  return fail("Unbekanntes Werkzeug: " + name);
}

} // namespace mag::tools
