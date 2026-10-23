// Live-highlight event for the room client (pure, unit-tested in
// mag-room/tests/test_browser_events.cpp). box = target element in viewport
// CSS px from mag-browser /locate. A masked (password/secret) field never
// carries its value: the text is a fixed run of dots.
#pragma once
#include "json.hpp"

inline mj::Val browser_action_val(const std::string &bot, const std::string &action,
                                  const mj::Val *pos, const std::string &shown_text,
                                  const mj::Val *box, bool masked) {
  mj::Val e = mj::Val::object();
  e["type"] = "browser_action";
  e["bot"] = bot;
  e["action"] = action;
  if (box && box->isObj() && box->find("w")) {
    mj::Val b = mj::Val::object();
    for (const char *k : {"x", "y", "w", "h"}) b[k] = box->find(k) ? box->find(k)->asNum() : 0;
    e["box"] = b;
    e["x"] = b["x"].asNum() + b["w"].asNum() / 2;
    e["y"] = b["y"].asNum() + b["h"].asNum() / 2;
  }
  if (pos && pos->isObj() && pos->find("x") && pos->find("y")) {
    e["x"] = pos->find("x")->asNum();
    e["y"] = pos->find("y")->asNum();
  }
  if (masked) {
    e["masked"] = true;
    e["text"] = std::string("••••••••");
  } else if (!shown_text.empty()) {
    e["text"] = shown_text.substr(0, 400);
  }
  return e;
}
