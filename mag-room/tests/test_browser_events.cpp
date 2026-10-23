// Live highlight events: box/centre mapping and that a masked field never
// carries its value.
#include "../native/browser_events.hpp"
#include <cstdlib>
#include <iostream>
#define assert(c) do { if (!(c)) { std::cerr << "FAIL line " << __LINE__ << ": " #c "\n"; std::exit(1); } } while (0)

int main() {
  mj::Val box = mj::Val::object();
  box["x"] = 100; box["y"] = 40; box["w"] = 200; box["h"] = 30; box["password"] = true;
  auto e = browser_action_val("ali", "type", nullptr, "", &box, true);
  const std::string d = mj::dump(e);
  assert(e["masked"].asBool(false));
  assert(e["x"].asNum() == 200 && e["y"].asNum() == 55);
  assert(e["box"]["w"].asNum() == 200);
  assert(d.find("Hunter2") == std::string::npos);
  assert(e["text"].asStr() == "••••••••");
  auto t = browser_action_val("ali", "type", nullptr, "hallo welt", &box, false);
  assert(t["text"].asStr() == "hallo welt" && !t.find("masked"));
  mj::Val pos = mj::Val::object(); pos["x"] = 5; pos["y"] = 6;
  auto c = browser_action_val("ali", "click", &pos, "", nullptr, false);
  assert(c["x"].asNum() == 5 && !c.find("box") && !c.find("text"));
  std::cout << "browser events OK\n";
}
