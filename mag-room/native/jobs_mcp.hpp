// In-process MCP jobs server / DeepSeek adapter.
// Include AFTER jobs.hpp, before routes.hpp. One Job API and one scheduler.
#pragma once
#include <cmath>

static bool jobs_mcp_safe_id(const std::string &id) {
  if (id.empty() || id.size() > 80) return false;
  for (unsigned char c : id)
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '-' || c == '_')) return false;
  return true;
}
static const Val *jobs_mcp_field(const Val &a, const char *key) {
  return a.isObj() ? a.find(key) : nullptr;
}
static std::string jobs_mcp_string(const Val &a, const char *key) {
  const Val *v = jobs_mcp_field(a, key);
  return v ? v->asStr() : "";
}
static bool jobs_mcp_int(const Val *v, int lo, int hi, int &out) {
  if (!v || v->type != Val::NUM || !std::isfinite(v->num) ||
      std::floor(v->num) != v->num || v->num < lo || v->num > hi) return false;
  out = (int)v->num; return true;
}
static bool jobs_mcp_time(const std::string &s) {
  if (s.size() != 5 || s[2] != ':') return false;
  int h = 0, m = 0;
  if (sscanf(s.c_str(), "%2d:%2d", &h, &m) != 2) return false;
  for (int i : {0,1,3,4}) if (s[i] < '0' || s[i] > '9') return false;
  return h <= 23 && m <= 59;
}
// Bot-facing schedule.kind -> existing native schedule.type (UI keeps its
// established wire format). Never introduce another scheduler.
static std::string jobs_mcp_schedule(const Val &spec, Val &native, bool &webhook) {
  if (!spec.isObj()) return "schedule muss ein Objekt sein";
  const std::string kind = jobs_mcp_string(spec, "kind");
  native = Val::object();
  webhook = false;
  if (kind == "webhook") {
    native["type"] = "none"; webhook = true; return "";
  }
  if (kind == "every_minutes" || kind == "every_hours" ||
      kind == "every_days" || kind == "every_weeks") {
    int every = 0;
    if (!jobs_mcp_int(jobs_mcp_field(spec, "every"), 1, 10000, every))
      return "schedule.every muss 1..10000 sein";
    native["type"] = "interval"; native["every"] = every;
    native["unit"] = kind == "every_minutes" ? "m" :
                     kind == "every_hours" ? "h" :
                     kind == "every_days" ? "d" : "w";
    return "";
  }
  if (kind == "daily" || kind == "weekdays" || kind == "weekly" ||
      kind == "monthly") {
    const std::string time = jobs_mcp_string(spec, "time");
    if (!jobs_mcp_time(time)) return "schedule.time als HH:MM angeben (Europe/Berlin)";
    native["time"] = time;
    if (kind == "monthly") {
      int day = 0;
      if (!jobs_mcp_int(jobs_mcp_field(spec, "day"), 1, 31, day))
        return "schedule.day muss 1..31 sein";
      native["type"] = "monthly"; native["day"] = day; return "";
    }
    native["type"] = "daily";
    if (kind == "weekdays") {
      Val days = Val::array();
      for (int i=1;i<=5;i++) days.push(i);
      native["days"] = days;
    }
    if (kind == "weekly") {
      int weekday = 0;
      if (!jobs_mcp_int(jobs_mcp_field(spec, "weekday"), 0, 6, weekday))
        return "schedule.weekday muss 0..6 sein (0=Sonntag)";
      Val days = Val::array(); days.push(weekday); native["days"] = days;
    }
    return "";
  }
  if (kind == "once") {
    const std::string at = jobs_mcp_string(spec, "at");
    // ISO 8601 local date-time, interpreted in the server's Europe/Berlin TZ.
    if (at.size() != 16 || at[4]!='-' || at[7]!='-' || at[10]!='T' || at[13]!=':')
      return "schedule.at als YYYY-MM-DDTHH:MM (Europe/Berlin) angeben";
    for (int i : {0,1,2,3,5,6,8,9,11,12,14,15})
      if (at[i] < '0' || at[i] > '9') return "schedule.at ungültig";
    int y = atoi(at.substr(0,4).c_str()), mo = atoi(at.substr(5,2).c_str());
    int d = atoi(at.substr(8,2).c_str()), h = atoi(at.substr(11,2).c_str());
    int mi = atoi(at.substr(14,2).c_str());
    if (y < 2020 || y > 2100 || mo < 1 || mo > 12 || d < 1 || d > 31 ||
        h > 23 || mi > 59) return "schedule.at ungültig";
    const int64_t epoch = local_epoch(y, mo-1, d, h, mi);
    time_t ep = (time_t)epoch; struct tm check{};
    localtime_r(&ep, &check);
    // Reject silently normalized nonexistent civil times (DST jump, 31 Feb).
    if (check.tm_year+1900 != y || check.tm_mon+1 != mo ||
        check.tm_mday != d || check.tm_hour != h || check.tm_min != mi)
      return "schedule.at existiert nicht in Europe/Berlin";
    if (epoch <= now_s()) return "schedule.at muss in der Zukunft liegen";
    native["type"] = "once"; native["at"] = at;
    return "";
  }
  return "schedule.kind unbekannt";
}
static std::string jobs_mcp_call(const std::string &name, const Val &args,
                                 const JobsActor &actor) {
  if (!is_jobs_tool(name)) return "Fehler: unbekanntes Jobs-Tool";
  if (!args.isObj()) return "Fehler: JSON-Objekt erwartet";
  std::string id = jobs_mcp_string(args, "id");
  if (name != "jobs_list" && name != "jobs_create" && !jobs_mcp_safe_id(id))
    return "Fehler: gültige Job-ID fehlt";
  Val body = Val::object();
  std::string path = "/api/jobs";
  std::string method = "GET";
  bool has_json = false;
  if (name == "jobs_create" || name == "jobs_update") {
    const bool create = name == "jobs_create";
    if (!create) path += "/" + id;
    method = create ? "POST" : "PUT";
    has_json = true;
    for (const char *key : {"name","prompt","paused"}) {
      if (auto v = jobs_mcp_field(args,key)) body[key] = *v;
    }
    if (create) {
      const auto key = jobs_mcp_field(args, "idempotency_key");
      if (!key || key->type != Val::STR || !jobs_valid_key(key->asStr()))
        return "Fehler: idempotency_key (8-128 Zeichen) fehlt";
      body["idempotency_key"] = *key;
      if (actor.is_bot) body["bot"] = actor.bot;
    }
    if (auto spec = jobs_mcp_field(args, "schedule")) {
      Val native; bool hook = false;
      std::string error = jobs_mcp_schedule(*spec, native, hook);
      if (!error.empty()) return "Fehler: " + error;
      body["schedule"] = native;
      if (hook) body["webhook"] = true;
      else if (!create) body["webhook"] = false; // remove stale webhook on type change
    } else if (create) return "Fehler: schedule fehlt";
  } else if (name == "jobs_get" || name == "jobs_history") {
    path += "/" + id + (name == "jobs_history" ? "/history" : "");
  } else if (name == "jobs_delete") {
    path += "/" + id; method = "DELETE";
  } else if (name == "jobs_run_now") {
    path += "/" + id + "/run"; method = "POST"; has_json = true;
  } else if (name == "jobs_pause" || name == "jobs_resume") {
    path += "/" + id + "/pause"; method = "POST"; has_json = true;
    body["paused"] = name == "jobs_pause";
  }
  const JobsResponse reply = jobs_request(method, path, body, has_json, actor);
  if (reply.status >= 400) {
    std::string msg = reply.value.find("error") ? reply.value.find("error")->asStr() : "Anfrage fehlgeschlagen";
    return "Fehler: " + msg;
  }
  return mj::dump(reply.value);
}
static std::string jobs_dispatch_tool(int bot_idx, const std::string &name,
                                     const Val &args) {
  const std::string bot = bot_field(bot_idx, "id");
  if (bot.empty()) return "Fehler: Bot unbekannt";
  return jobs_mcp_call(name, args, JobsActor{true, bot});
}
static Val jobs_mcp_list() {
  Val list = Val::array();
  for (const char *definition : JOBS_TOOL_DEFS) {
    Val src;
    if (!mj::parse(definition, src)) continue;
    const Val *fn = src.find("function");
    if (!fn) continue;
    Val v = Val::object();
    for (const char *key : {"name", "description"})
      if (auto f = fn->find(key)) v[key] = *f;
    if (auto p = fn->find("parameters")) v["inputSchema"] = *p;
    list.push(v);
  }
  return list;
}
static Val jobs_mcp_rpc_error(const Val *id, int code, const std::string &msg) {
  Val r = Val::object(); r["jsonrpc"] = "2.0";
  r["id"] = id ? *id : Val();
  Val e = Val::object(); e["code"] = code; e["message"] = msg;
  r["error"] = e; return r;
}
// JSON-RPC core: 2026-07-28 stateless MCP + initialize compatibility for
// clients still on 2025-11-25. Network route supplies HUMAN credentials;
// room model invokes the same jobs_mcp_call with a server-owned bot identity.
static Val jobs_mcp_rpc(const Val &req, const JobsActor &actor) {
  if (!req.isObj()) return jobs_mcp_rpc_error(nullptr, -32600, "Invalid Request");
  const Val *id = req.find("id");
  const std::string method = jobs_mcp_string(req, "method");
  if (method.empty() || jobs_mcp_string(req,"jsonrpc") != "2.0")
    return jobs_mcp_rpc_error(id,-32600,"Invalid Request");
  Val r = Val::object(); r["jsonrpc"] = "2.0"; if (id) r["id"] = *id;
  const Val *params = req.find("params");
  const bool modern = params && params->find("_meta") &&
      params->find("_meta")->find("io.modelcontextprotocol/protocolVersion") &&
      params->find("_meta")->find("io.modelcontextprotocol/protocolVersion")->asStr() == "2026-07-28";
  if (method == "initialize") {
    Val out = Val::object(); out["protocolVersion"] = "2025-11-25";
    Val caps = Val::object(); Val tools = Val::object(); tools["listChanged"] = false;
    caps["tools"] = tools; out["capabilities"] = caps;
    Val info = Val::object(); info["name"] = "mag-jobs"; info["version"] = "1.0.0";
    out["serverInfo"] = info; r["result"] = out;
    return r;
  }
  if (method == "server/discover") {
    Val out = Val::object();
    Val info = Val::object(); info["name"] = "mag-jobs"; info["version"] = "1.0.0";
    out["serverInfo"] = info; Val cap = Val::object(); cap["tools"] = Val::object();
    out["capabilities"] = cap;
    r["result"] = out; return r;
  }
  if (method == "ping") { r["result"] = Val::object(); return r; }
  if (method == "tools/list") {
    Val out = Val::object(); out["tools"] = jobs_mcp_list();
    if (modern) { out["resultType"] = "complete"; out["ttlMs"] = 3600000; out["cacheScope"] = "public"; }
    r["result"] = out; return r;
  }
  if (method == "tools/call") {
    if (!params || !params->isObj()) return jobs_mcp_rpc_error(id,-32602,"params fehlen");
    const std::string name = jobs_mcp_string(*params, "name");
    if (!is_jobs_tool(name)) return jobs_mcp_rpc_error(id,-32602,"Job-Tool unbekannt");
    Val args = Val::object();
    if (auto v = params->find("arguments")) args = *v;
    const std::string output = jobs_mcp_call(name,args,actor);
    Val out = Val::object();
    if (modern) out["resultType"] = "complete";
    Val blocks = Val::array(); Val b = Val::object(); b["type"] = "text";
    b["text"] = output; blocks.push(b); out["content"] = blocks;
    out["isError"] = output.rfind("Fehler:",0) == 0;
    r["result"] = out; return r;
  }
  return jobs_mcp_rpc_error(id,-32601,"Method not found");
}
