// Bot web pages. A finished HTML page of a bot lands in its VM's www folder,
// is published on the bot's own tailnet site and runs in the chat as a live
// preview framed from that site. The site's address follows the bot's name
// (lena.<zone>); earlier names and the VM's name (agent3.<zone>) redirect.
#pragma once
#include "vm.hpp"

namespace mag::site {

struct Block {
  std::string lang, code;
};

// Closed fenced code blocks of a markdown text, in order.
inline std::vector<Block> fenced(const std::string &text) {
  std::vector<Block> out;
  std::istringstream in(text);
  std::string line;
  bool open = false;
  char mark = 0;
  size_t width = 0, indent = 0;
  Block cur;
  while (std::getline(in, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    const size_t lead = std::min(line.find_first_not_of(" \t"), line.size());
    size_t run = 0;
    if (lead < line.size() && (line[lead] == '`' || line[lead] == '~'))
      while (lead + run < line.size() && line[lead + run] == line[lead]) ++run;
    if (!open) {
      if (run < 3) continue;
      const std::string info = trim(line.substr(lead + run));
      if (line[lead] == '`' && info.find('`') != std::string::npos) continue; // inline code, not a fence
      open = true;
      mark = line[lead];
      width = run;
      indent = lead;
      cur = Block();
      cur.lang = lower(info.substr(0, info.find_first_of(" \t{,")));
    } else if (run >= width && line[lead] == mark && trim(line.substr(lead + run)).empty()) {
      open = false;
      out.push_back(cur);
    } else {
      size_t cut = 0;
      while (cut < indent && cut < line.size() && (line[cut] == ' ' || line[cut] == '\t')) ++cut;
      cur.code += line.substr(cut) + "\n";
    }
  }
  return out;
}

// Does the text begin (after comments) like a complete HTML document?
inline bool is_document(const std::string &code) {
  size_t i = 0;
  for (;;) {
    while (i < code.size() && isspace((unsigned char)code[i])) ++i;
    if (code.compare(i, 4, "<!--") != 0) break;
    const size_t end = code.find("-->", i + 4);
    if (end == std::string::npos) return false;
    i = end + 3;
  }
  const std::string head = lower(code.substr(i, 14));
  return starts_with(head, "<!doctype html") || starts_with(head, "<html>") || starts_with(head, "<html ") ||
         starts_with(head, "<html\n");
}

// A block the chat shows as a running page.
inline bool is_page(const Block &b) {
  if (trim(b.code).empty()) return false;
  return b.lang == "html" || b.lang == "htm" || b.lang == "xhtml" || is_document(b.code);
}

// The file that is published: a fragment becomes a document, and a page that
// imports the bare module name "three" gets the import map browsers need.
inline std::string document(const std::string &code) {
  std::string doc = is_document(code)
                        ? code
                        : "<!doctype html>\n<html lang=\"de\">\n<head>\n<meta charset=\"utf-8\">\n"
                          "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n</head>\n<body>\n" +
                              code + "\n</body>\n</html>\n";
  const std::string low = lower(doc);
  const bool bare_three = low.find("'three'") != std::string::npos || low.find("\"three\"") != std::string::npos ||
                          low.find("'three/") != std::string::npos || low.find("\"three/") != std::string::npos;
  if (!bare_three || low.find("importmap") != std::string::npos) return doc;
  static const std::string map =
      "\n<script type=\"importmap\">{\"imports\":{\"three\":\"https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js\","
      "\"three/addons/\":\"https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/\"}}</script>";
  for (const char *tag : {"<head", "<html"}) {
    const size_t at = low.find(tag);
    const size_t close = at == std::string::npos ? at : low.find('>', at);
    if (close != std::string::npos) return doc.insert(close + 1, map);
  }
  return map + "\n" + doc;
}

inline std::string title(const std::string &html) {
  const std::string low = lower(html);
  const size_t open = low.find("<title");
  const size_t start = open == std::string::npos ? open : low.find('>', open);
  const size_t end = start == std::string::npos ? start : low.find("</title", start);
  if (end == std::string::npos) return "";
  std::string out;
  for (char c : html.substr(start + 1, end - start - 1)) {
    const bool space = isspace((unsigned char)c);
    if (space && (out.empty() || out.back() == ' ')) continue;
    out += space ? ' ' : c;
  }
  return mj::utf8_cut(trim(out), 80);
}

// File name part from a title: a-z, 0-9 and single hyphens.
inline std::string slug(const std::string &text, size_t max = 40) {
  static const std::pair<const char *, const char *> umlauts[] = {{"ä", "ae"}, {"ö", "oe"}, {"ü", "ue"}, {"ß", "ss"},
                                                                  {"Ä", "ae"}, {"Ö", "oe"}, {"Ü", "ue"}};
  std::string s = text;
  for (auto &u : umlauts) replace_all(s, u.first, u.second);
  std::string out;
  for (unsigned char c : s) {
    if (isalnum(c) && c < 128) out += (char)tolower(c);
    else if (!out.empty() && out.back() != '-') out += '-';
    if (out.size() >= max) break;
  }
  while (!out.empty() && out.back() == '-') out.pop_back();
  return out.empty() ? "seite" : out;
}

// Fingerprint of a code block that the page computes the same way (FNV-1a
// over the bytes without whitespace), so it finds the block a page came from.
inline std::string sig(const std::string &code) {
  uint32_t h = 0x811c9dc5u;
  for (unsigned char c : code) {
    if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') continue;
    h = (h ^ c) * 0x01000193u;
  }
  char buf[12];
  snprintf(buf, sizeof buf, "%08x", h);
  return buf;
}

inline std::string zone() { return env("MAG_BOTS_ZONE", "bot.stardenburg.de"); }
// Where the publisher keeps the sites, one folder per VM.
inline std::string www_root() { return env("MAG_BOTS_WWW", "/var/lib/mag-bots/www"); }

// The name is the address: "Lena" lives at lena.<zone>.
inline std::string slug_of(const Val &bot) { return lower(jstr(bot, "name")); }

// A bot name: 2 to 24 letters, digits and hyphens, starting with a letter,
// so it is a host name as it stands. Returns "" or what is wrong with it.
inline std::string name_problem(const std::string &name) {
  if (name.size() < 2 || name.size() > 24) return "Der Name braucht 2 bis 24 Zeichen.";
  for (unsigned char c : name)
    if (!(isalnum(c) && c < 128) && c != '-') return "Erlaubt sind Buchstaben (ohne Umlaute), Ziffern und Bindestrich.";
  if (!isalpha((unsigned char)name[0]) || name.back() == '-') return "Der Name beginnt mit einem Buchstaben und endet nicht mit einem Bindestrich.";
  const std::string slug = lower(name);
  static const std::set<std::string> reserved = {"chris", "alle", "beide", "bot", "bots", "idol", "system", "mag", "www", "raum", "room", "api"};
  if (reserved.count(slug) || vm::valid_name(slug)) return "Dieser Name ist reserviert.";
  return "";
}

// Address of a bot's site ("" without a VM: there is nothing to serve).
inline std::string base_url(const Val &bot) {
  return vm::valid_name(jstr(bot, "vm")) ? "https://" + slug_of(bot) + "." + zone() : "";
}

// Which bot a site name means. `current` says whether it is the bot's name
// of today; everything else that once pointed to the bot (an earlier name,
// its id, its VM) is answered with a redirect to the current address.
inline Val resolve(const Val &bots, const std::string &name, bool &current) {
  current = true;
  for (auto &b : bots.asArr())
    if (slug_of(b) == name) return b;
  current = false;
  for (auto &b : bots.asArr())
    if (const Val *old = b.find("aliases"); old && old->isArr())
      for (auto &a : old->asArr())
        if (a.asStr() == name) return b;
  for (auto &b : bots.asArr())
    if (jstr(b, "id") == name || (vm::valid_name(name) && (jstr(b, "vm") == name || jstr(b, "vm_lost") == name))) return b;
  return Val();
}

// Path below the VM's www folder, "" when the file is not part of the site.
inline std::string rel_path(const std::string &guest_path) {
  const std::string root = env("MAG_ROOM_GUEST_HOME", "/home/bot") + "/www/";
  return starts_with(guest_path, root) ? guest_path.substr(root.size()) : "";
}

// Publishes the VM's www folder on the bot's site. Returns "" or the reason
// it failed. One upload at a time: the publisher swaps the whole site.
inline std::string publish(vm::Guests &guests, const std::string &vm, const Cancel *cancel = nullptr) {
  static std::mutex mu;
  std::lock_guard<std::mutex> lk(mu);
  static const std::string script =
      "home=\"${HOME:-/home/bot}\"\n"
      "mkdir -p \"$home/www\" || exit 1\n"
      "[ -f \"$home/www/index.html\" ] || printf '%s\\n' '<!doctype html><meta charset=\"utf-8\"><title>Bot</title><p>Noch keine Startseite.</p>' > \"$home/www/index.html\"\n"
      "[ -f \"$home/publish-site\" ] || { echo 'Diese VM hat keine Website (publish-site fehlt).'; exit 3; }\n"
      "bash \"$home/publish-site\"\n";
  const auto r = guests.exec(vm, script, 90, cancel, {});
  if (r.code == 0 && !r.cancelled && !r.timed_out) return "";
  const std::string why = clip(trim(r.out), 300);
  return why.empty() ? "Veröffentlichen fehlgeschlagen" : why;
}

// One entry of a message's `pages`.
inline Val page(const std::string &bot, const std::string &path, const std::string &title) {
  Val p = Val::object();
  p["bot"] = bot;
  p["path"] = path;
  p["title"] = title.empty() ? path : title;
  p["v"] = (double)now_ms();
  return p;
}

} // namespace mag::site
