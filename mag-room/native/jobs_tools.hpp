// Stable OpenAI function schemas projected to MCP tools/list. Never reorder.
// All room bots (including chat-only) receive the same jobs tools.
#pragma once
static const char *JOBS_TOOL_DEFS[] = {
  R"MAGJ({"type":"function","function":{"name":"jobs_list","description":"List all jobs including status. Example: {}.","parameters":{"type":"object","properties":{},"required":[],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_get","description":"Read one job. Example: {\"id\":\"abc123\"}.","parameters":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80}},"required":["id"],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_create","description":"Create a scheduled job. Use a unique stable idempotency_key. Example: {\"name\":\"Brief\",\"prompt\":\"Send briefing\",\"schedule\":{\"kind\":\"daily\",\"time\":\"08:00\"},\"idempotency_key\":\"brief-2026-10-16\"}.","parameters":{"type":"object","properties":{"name":{"type":"string","maxLength":120},"prompt":{"type":"string","maxLength":16000},"schedule":{"type":"object","properties":{"kind":{"type":"string","enum":["every_minutes","every_hours","every_days","every_weeks","daily","weekdays","weekly","monthly","once","webhook"]},"every":{"type":"integer","minimum":1,"maximum":10000},"time":{"type":"string","description":"HH:MM, Europe/Berlin"},"weekday":{"type":"integer","minimum":0,"maximum":6,"description":"0=Sun..6=Sat"},"day":{"type":"integer","minimum":1,"maximum":31},"at":{"type":"string","description":"ISO 8601 date-time; Europe/Berlin"}},"required":["kind"],"additionalProperties":false},"idempotency_key":{"type":"string","minLength":8,"maxLength":128},"paused":{"type":"boolean"}},"required":["name","prompt","schedule","idempotency_key"],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_update","description":"Update only supplied fields. Example: {\"id\":\"abc123\",\"name\":\"New title\"}.","parameters":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80},"name":{"type":"string"},"prompt":{"type":"string"},"schedule":{"type":"object","properties":{"kind":{"type":"string","enum":["every_minutes","every_hours","every_days","every_weeks","daily","weekdays","weekly","monthly","once","webhook"]},"every":{"type":"integer","minimum":1,"maximum":10000},"time":{"type":"string","description":"HH:MM, Europe/Berlin"},"weekday":{"type":"integer","minimum":0,"maximum":6,"description":"0=Sun..6=Sat"},"day":{"type":"integer","minimum":1,"maximum":31},"at":{"type":"string","description":"ISO 8601 date-time; Europe/Berlin"}},"required":["kind"],"additionalProperties":false},"paused":{"type":"boolean"}},"required":["id"],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_delete","description":"Delete own job only. User-owned/other-bot jobs require human UI confirmation. Example: {\"id\":\"abc123\"}.","parameters":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80}},"required":["id"],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_pause","description":"Pause a job. Example: {\"id\":\"abc123\"}.","parameters":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80}},"required":["id"],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_resume","description":"Resume a paused job. Example: {\"id\":\"abc123\"}.","parameters":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80}},"required":["id"],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_run_now","description":"Queue a job immediately. Example: {\"id\":\"abc123\"}.","parameters":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80}},"required":["id"],"additionalProperties":false}}})MAGJ",
  R"MAGJ({"type":"function","function":{"name":"jobs_history","description":"Read the last runs of a job. Example: {\"id\":\"abc123\"}.","parameters":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80}},"required":["id"],"additionalProperties":false}}})MAGJ"
};
static constexpr size_t JOBS_TOOL_COUNT = sizeof(JOBS_TOOL_DEFS)/sizeof(JOBS_TOOL_DEFS[0]);
static bool is_jobs_tool(const std::string &name) {
  for (const char *spec : JOBS_TOOL_DEFS) {
    mj::Val v; if (!mj::parse(spec, v)) continue;
    const mj::Val *fn = v.find("function");
    if (fn && fn->find("name") && fn->find("name")->asStr() == name) return true;
  }
  return false;
}
static std::string jobs_dispatch_tool(int bot_idx, const std::string &name, const Val &args);
