// Chris's payment cards, sealed. The number, the name and the expiry live in
// STATE/pay/cards.enc as one AES-256-GCM sealed JSON (key STATE/pay/vault.key,
// 0600, read only by this process). The security code is never stored: Chris
// gives it on the payment card each time. What anybody but the room sees of
// a card is its brand and last four digits; a model sees nothing but that.
// At a checkout the room types the card into the page of the bot's browser
// (Api::fill_card), after the purchase was allowed (consent, kind buy).
//
// Logins, the same way: every login Chris types into a login card is kept,
// per site and account, sealed in STATE/pay/logins.enc (whether it worked or
// not), and offered again on the next login card of that site ("use the
// saved login?"); the room types it in. A model never sees a password.
#pragma once
#include "assist.hpp"

namespace mag {

class Vault {
public:
  explicit Vault(Room &room) : room_(room) {
    std::error_code ec;
    std::filesystem::create_directories(dir(), ec);
    ::chmod(dir().c_str(), 0700);
  }

  // Brand, last four digits, expiry and name of each card; never the number.
  Val list() {
    std::lock_guard<std::mutex> lk(mu_);
    Val out = Val::array();
    const Val all = load();
    for (auto &c : all.asArr()) {
      Val one = Val::object();
      one["id"] = jstr(c, "id");
      one["brand"] = brand(jstr(c, "number"));
      one["last4"] = jstr(c, "number").substr(jstr(c, "number").size() - 4);
      one["exp"] = jstr(c, "exp");
      one["name"] = jstr(c, "name");
      out.push(one);
    }
    return out;
  }

  // "" with the card kept, or why not.
  std::string add(std::string number, std::string exp, std::string name) {
    std::string digits;
    for (char ch : number) if (ch >= '0' && ch <= '9') digits += ch;
    std::fill(number.begin(), number.end(), '\0');
    if (digits.size() < 12 || digits.size() > 19 || !luhn(digits)) return "Die Kartennummer stimmt nicht.";
    int month = 0, year = 0;
    if (sscanf(exp.c_str(), "%d/%d", &month, &year) != 2 || month < 1 || month > 12) return "Ablauf als MM/JJ.";
    if (year < 100) year += 2000;
    char when[8];
    snprintf(when, sizeof when, "%02d/%02d", month, year % 100);
    std::lock_guard<std::mutex> lk(mu_);
    Val all = load();
    if (all.asArr().size() >= 10) return "Höchstens zehn Karten.";
    Val c = Val::object();
    c["id"] = random_hex(6);
    c["number"] = digits;
    c["exp"] = std::string(when);
    c["name"] = clip(trim(name), 80, "");
    all.push(c);
    std::fill(digits.begin(), digits.end(), '\0');
    return save(all) ? "" : "Die Karte ließ sich nicht sicher speichern.";
  }

  bool remove(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    Val all = load(), kept = Val::array();
    bool found = false;
    for (auto &c : all.asArr()) {
      if (jstr(c, "id") == id) found = true;
      else kept.push(c);
    }
    return found && save(kept);
  }

  // The whole card, for typing it into a page; null when there is none.
  Val open(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    const Val all = load();
    for (auto &c : all.asArr())
      if (jstr(c, "id") == id) return c;
    return Val();
  }

  // ---- logins ---------------------------------------------------------------
  // amazon.de from "https://www.amazon.de/ap/signin?…", "Amazon.de" or "www.amazon.de".
  static std::string site_of(std::string s) {
    s = lower(trim(s));
    if (const size_t at = s.find("://"); at != std::string::npos) s = s.substr(at + 3);
    s = s.substr(0, s.find_first_of("/?#: "));
    if (starts_with(s, "www.")) s = s.substr(4);
    return s;
  }

  // Site and account of each login; never the password.
  Val logins(const std::string &site = "") {
    std::lock_guard<std::mutex> lk(mu_);
    Val out = Val::array();
    const Val all = load("logins.enc");
    const std::string want = site_of(site);
    for (auto &l : all.asArr()) {
      if (!want.empty() && jstr(l, "site") != want && !ends_with(want, "." + jstr(l, "site")) && !ends_with(jstr(l, "site"), "." + want)) continue;
      Val one = Val::object();
      one["id"] = jstr(l, "id");
      one["site"] = jstr(l, "site");
      one["user"] = jstr(l, "user");
      one["saved"] = jnum(l, "saved");
      out.push(one);
    }
    return out;
  }

  // Kept (the same site and account is replaced); returns its id, "" if it could not be kept.
  std::string keep_login(const std::string &site_in, const std::string &user, const std::string &password) {
    const std::string site = site_of(site_in);
    if (site.empty() || (user.empty() && password.empty())) return "";
    std::lock_guard<std::mutex> lk(mu_);
    Val all = load("logins.enc"), kept = Val::array();
    std::string id;
    for (auto &l : all.asArr()) {
      if (jstr(l, "site") == site && jstr(l, "user") == user) { id = jstr(l, "id"); continue; }
      kept.push(l);
    }
    Val l = Val::object();
    l["id"] = id.empty() ? random_hex(6) : id;
    l["site"] = site;
    l["user"] = user;
    l["password"] = password;
    l["saved"] = (double)now_s();
    kept.push(l);
    return save(kept, "logins.enc") ? jstr(l, "id") : "";
  }

  // Changed by Chris in the settings (an empty password keeps the old one).
  bool change_login(const std::string &id, const std::string &user, const std::string &password) {
    std::lock_guard<std::mutex> lk(mu_);
    Val all = load("logins.enc");
    bool found = false;
    for (auto &l : *all.arr)
      if (jstr(l, "id") == id) {
        found = true;
        if (!user.empty()) l["user"] = user;
        if (!password.empty()) l["password"] = password;
        l["saved"] = (double)now_s();
      }
    return found && save(all, "logins.enc");
  }

  bool remove_login(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    Val all = load("logins.enc"), kept = Val::array();
    bool found = false;
    for (auto &l : all.asArr()) {
      if (jstr(l, "id") == id) found = true;
      else kept.push(l);
    }
    return found && save(kept, "logins.enc");
  }

  // The whole login, for typing it into a page (or showing it to Chris); null when there is none.
  Val open_login(const std::string &id) {
    std::lock_guard<std::mutex> lk(mu_);
    const Val all = load("logins.enc");
    for (auto &l : all.asArr())
      if (jstr(l, "id") == id) return l;
    return Val();
  }

  static std::string brand(const std::string &n) {
    if (n.empty()) return "Karte";
    if (n[0] == '4') return "Visa";
    if (n.compare(0, 2, "34") == 0 || n.compare(0, 2, "37") == 0) return "American Express";
    const int two = atoi(n.substr(0, 2).c_str()), four = atoi(n.substr(0, 4).c_str());
    if ((two >= 51 && two <= 55) || (four >= 2221 && four <= 2720)) return "Mastercard";
    return "Karte";
  }

private:
  Room &room_;
  std::mutex mu_;
  std::string key_;

  std::string dir() const { return room_.cfg.state_dir + "/pay"; }
  const std::string &key() {
    if (key_.size() == 32) return key_;
    const std::string file = dir() + "/vault.key";
    key_ = slurp(file);
    if (key_.size() != 32) {
      key_ = random_bytes(32);
      if (!write_atomic(file, key_, 0600)) logf("vault: the key could not be written");
    }
    return key_;
  }
  Val load(const std::string &file = "cards.enc") {
    const std::string sealed = trim(slurp(dir() + "/" + file));
    if (sealed.empty()) return Val::array();
    const Val all = jparse(gcm_open(key(), sealed));
    return all.isArr() ? all : Val::array();
  }
  bool save(const Val &all, const std::string &file = "cards.enc") {
    const std::string sealed = gcm_seal(key(), mj::dump(all));
    return !sealed.empty() && write_atomic(dir() + "/" + file, sealed + "\n", 0600);
  }
  static bool luhn(const std::string &digits) {
    int sum = 0;
    bool twice = false;
    for (auto it = digits.rbegin(); it != digits.rend(); ++it) {
      int d = *it - '0';
      if (twice && (d *= 2) > 9) d -= 9;
      sum += d;
      twice = !twice;
    }
    return sum % 10 == 0;
  }
};

} // namespace mag
