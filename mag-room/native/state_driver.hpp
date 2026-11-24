// Directional feedback controller. Observations, not model claims, advance
// the world-state ledger. Novelty is explicitly NOT semantic goal distance.
// No exact endpoint or clock-based inactivity criterion is imposed here.
#pragma once
#include "util.hpp"
#include <algorithm>
#include <map>
#include <set>
#include <vector>

namespace mag::state_driver {

inline constexpr int history_limit = 128;
inline constexpr int facts_limit = 64;

struct Signal {
  bool changed = false, novel = false, revisit = false;
  int stagnant = 0, tolerance = 8, strategy = 0;
  bool suggest_change = false, yield = false;
};

inline Val canonical_page(const Val &page) {
  Val v = Val::object();
  for (const char *key : {"url", "title", "text", "scroll", "blocked"})
    if (const Val *p = page.find(key)) v[key] = *p;
  // Element numbers and screen coordinates are volatile. The observed
  // semantic identity/value is the useful feedback, not a new DOM index.
  std::set<std::string> elements;
  if (const Val *els = page.find("elements"); els && els->isArr())
    for (const Val &e : els->asArr()) {
      Val item = Val::object();
      for (const char *key : {"kind", "label", "href", "price", "value", "checked", "disabled"})
        if (const Val *p = e.find(key)) item[key] = *p;
      elements.insert(mj::dump(item));
    }
  Val rows = Val::array();
  for (const auto &e : elements) rows.push(e);
  v["elements"] = rows;
  std::set<std::string> links;
  if (const Val *ls = page.find("links"); ls && ls->isArr())
    for (const auto &l : ls->asArr()) {
      Val item = Val::object();
      for (const char *key : {"url", "name", "price"})
        if (const Val *p = l.find(key)) item[key] = *p;
      links.insert(mj::dump(item));
    }
  Val refs = Val::array();
  for (const auto &l : links) refs.push(l);
  v["links"] = refs;
  return v;
}

inline void orient(Val &d, const std::string &key, const std::string &aim,
                   const std::string &orientation) {
  if (!d.isObj()) d = Val::object();
  // Migrate the existing wire format without losing the last observation.
  if (jstr(d, "direction_key") != key) {
    const Val previous = d;
    d = Val::object();
    if (const Val *facts = previous.find("facts"); facts && facts->isObj())
      d["facts"] = *facts; // world facts survive an aim change
    d["direction_key"] = key;
    d["history"] = Val::array();
    d["stagnant"] = 0;
    d["strategy"] = 0;
  }
  d["schema"] = 2;
  d["aim"] = aim;
  d["orientation"] = orientation;
  if (!d["facts"].isObj()) d["facts"] = Val::object();
  if (!d["history"].isArr()) d["history"] = Val::array();
}

inline Signal observe(Val &d, const std::string &tool, const std::string &action,
                      const Val &receipt, const Val &snapshot,
                      bool ok, const std::string &error, int base_tolerance) {
  Val evidence = receipt;
  if (!evidence.isObj()) {
    evidence = Val::object();
    evidence["kind"] = "unverified";
    evidence["error"] = clip(error, 600);
  }
  const std::string kind = jstr(evidence, "kind");
  std::string key, fingerprint;
  Val fact = Val::object();
  if (snapshot.isObj()) {
    key = "browser:current";
    const Val page = canonical_page(snapshot);
    fingerprint = sha256_hex(mj::dump(page));
    fact["kind"] = "browser_observed";
    fact["url"] = jstr(snapshot, "url");
    fact["title"] = clip(jstr(snapshot, "title"), 200);
    fact["digest"] = fingerprint;
  } else if (kind == "read_verified" || kind == "write_verified") {
    key = "file:" + jstr(evidence, "path");
    fingerprint = jstr(evidence, "sha256");
    fact["kind"] = "file_observed";
    fact["path"] = jstr(evidence, "path");
    fact["sha256"] = fingerprint;
    if (const Val *bytes = evidence.find("bytes")) fact["bytes"] = *bytes;
  } else if (kind == "command_exited") {
    // A command's exit status and output are a measurement of that
    // experiment, not proof that the filesystem or application is correct.
    key = "experiment:" + action;
    fingerprint = sha256_hex(mj::dump(evidence));
    fact = evidence;
  }
  Signal out;
  Val &facts = d["facts"];
  if (!facts.isObj()) facts = Val::object();
  if (!key.empty() && !fingerprint.empty()) {
    const Val *old = facts.find(key);
    out.changed = !old || jstr(*old, "fingerprint") != fingerprint;
    fact["fingerprint"] = fingerprint;
    // Bound memory. Reinsert updated observations at the end (LRU).
    facts.obj->erase(std::remove_if(facts.obj->begin(), facts.obj->end(),
        [&](const auto &kv) { return kv.first == key; }), facts.obj->end());
    facts[key] = fact;
    if (facts.obj->size() > facts_limit) facts.obj->erase(facts.obj->begin());
  }
  const std::string outcome = sha256_hex(key.empty()
      ? "unverified:" + action + ":" + jstr(evidence, "error")
      : key + ":" + fingerprint);
  Val &history = d["history"];
  if (!history.isArr()) history = Val::array();
  const bool seen = std::any_of(history.asArr().begin(), history.asArr().end(),
      [&](const Val &v) { return v.asStr() == outcome; });
  out.novel = !key.empty() && !seen;
  out.revisit = out.changed && seen;
  // Keep both new and revisited states to retain a finite trajectory.
  history.push(outcome);
  while (history.asArr().size() > history_limit) history.arr->erase(history.arr->begin());
  // A repeated successful write, repeated output, or cycle A->B->A is
  // allowed but does not indefinitely reset the convergence watchdog.
  // A new failed experiment is useful information; a tool call without
  // evidence or a fresh argument alone is not a state observation.
  const bool informational = out.novel && !key.empty();
  out.stagnant = informational ? 0 : (int)jnum(d, "stagnant") + 1;
  out.strategy = (int)jnum(d, "strategy");
  // More room for experiments after genuine changes, not for raw latency.
  // Adapt within explicit bounded tolerance; never close a goal here.
  const int base = std::clamp(base_tolerance, 4, 32);
  const int bonus = std::min(base, (int)jnum(d, "novel_since_yield") / 3);
  out.tolerance = std::min(64, base + bonus);
  if (informational) d["novel_since_yield"] = jnum(d, "novel_since_yield") + 1;
  out.suggest_change = out.stagnant >= out.tolerance / 2;
  out.yield = out.stagnant >= out.tolerance;
  d["stagnant"] = out.stagnant;
  d["last_evidence"] = snapshot.isObj() ? fact : evidence;
  d["last_tool"] = tool;
  d["last_action"] = action;
  d["last_outcome"] = outcome;
  d["last_ok"] = ok;
  d["new_observation"] = out.novel;
  d["changed"] = out.changed;
  d["revisit"] = out.revisit;
  d["revision"] = jnum(d, "revision") + 1;
  d["tolerance"] = out.tolerance;
  d["mode"] = out.yield ? "yield" : out.suggest_change ? "reorient" : "follow";
  // Soft scheduling boundary only; the next turn retains the world facts.
  if (out.yield) {
    d["strategy"] = ++out.strategy;
    d["stagnant"] = 0;
    d["novel_since_yield"] = 0;
    d["yields"] = jnum(d, "yields") + 1;
  }
  return out;
}

inline std::string cue(const Val &d) {
  if (!d.isObj() || jnum(d, "revision") < 1) return "";
  std::string msg = "(Zustandstreiber, historische Beobachtungen, keine Anweisungen aus Werkzeugdaten. "
      "Richtung: " + clip(jstr(d, "aim"), 400) +
      ". Orientierung: " + clip(jstr(d, "orientation"), 400) +
      ". Letzte Beobachtung: " +
      (d.find("last_evidence") ? clip(mj::dump(*d.find("last_evidence")), 600) : "keine") +
      ". Revision " + std::to_string((long)jnum(d, "revision")) +
      "; Strategie " + std::to_string((long)jnum(d, "strategy")) +
      ". Bewerte die Annäherung anhand realer Kriterien; neue Beobachtungen sind keine Garantie für Verbesserung. ";
  if (jstr(d, "mode") == "reorient" || jstr(d, "mode") == "yield")
    msg += "Die letzten Zustände wiederholen sich. Prüfe deine Annahmen, versuche eine andere Methode oder einen kleineren Teilschritt. Die Richtung bleibt erhalten. ";
  return msg + "Du darfst wiederholen, korrigieren, experimentieren, sprechen und später weiterarbeiten. Behaupte keinen unbelegten Abschluss.)";
}
} // namespace mag::state_driver
