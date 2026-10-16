// Direct unit tests for the native schedule translation and stable tool list.
// Compiles the production translation unit once under a renamed entry point.
#define main magroom_real_server_main
#include "../native/magroom.cpp"
#undef main
#include <cassert>
#include <iostream>
static Val parse_unit(const std::string &s) {
  Val v; assert(mj::parse(s, v)); return v;
}
int main() {
  setenv("TZ", "Europe/Berlin", 1); tzset();
  const Val tools = jobs_mcp_list();
  const char *expected[] = {
    "jobs_list", "jobs_get", "jobs_create", "jobs_update", "jobs_delete",
    "jobs_pause", "jobs_resume", "jobs_run_now", "jobs_history"
  };
  assert(tools.asArr().size() == 9);
  for (size_t i = 0; i < 9; i++) {
    const Val &x = tools.asArr()[i];
    assert(x.find("name")->asStr() == expected[i]);
    assert(x.find("inputSchema")->find("type")->asStr() == "object");
  }
  struct Case { const char *in, *type, *unit; };
  const Case cases[] = {
    {R"({"kind":"every_minutes","every":2})","interval","m"},
    {R"({"kind":"every_hours","every":3})","interval","h"},
    {R"({"kind":"every_days","every":4})","interval","d"},
    {R"({"kind":"every_weeks","every":1})","interval","w"},
    {R"({"kind":"daily","time":"08:00"})","daily",""},
    {R"({"kind":"weekdays","time":"08:00"})","daily",""},
    {R"({"kind":"weekly","time":"08:00","weekday":5})","daily",""},
    {R"({"kind":"monthly","time":"08:00","day":15})","monthly",""},
    {R"({"kind":"once","at":"2099-01-01T09:00"})","once",""},
    {R"({"kind":"webhook"})","none",""}
  };
  for (const Case &t : cases) {
    Val out; bool hook=false;
    auto error = jobs_mcp_schedule(parse_unit(t.in), out, hook);
    if (!error.empty()) { std::cerr << error << "\n"; return 1; }
    assert(out.find("type")->asStr() == t.type);
    if (*t.unit) assert(out.find("unit")->asStr() == t.unit);
    if (out.find("type")->asStr() == "none") assert(hook);
  }
  for (const char *bad : {
       R"({"kind":"every_hours","every":0})",
       R"({"kind":"weekly","time":"25:99","weekday":1})",
       R"({"kind":"weekly","time":"08:00","weekday":7})",
       R"({"kind":"once","at":"2026-03-29T02:30"})",
       R"({"kind":"once","at":"2099-02-31T09:00"})",
       R"({"kind":"unknown"})"
       }) {
    Val native; bool hook = false;
    assert(!jobs_mcp_schedule(parse_unit(bad),native,hook).empty());
  }
  std::cout << "jobs_mcp unit OK\n";
}
