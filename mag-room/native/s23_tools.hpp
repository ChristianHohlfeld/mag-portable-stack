// Room tools for the local S23 control daemon. The token stays on the host.
// The daemon is optional: a missing process is a tool error, not a change to
// s23pub or cloudflared.
#pragma once
#include <curl/curl.h>
#include <string>

static const char *S23_TOOL_DEFS[] = {
    R"S23({"type":"function","function":{"name":"s23_status","description":"Read the Samsung S23 control daemon on this host: phone state, display size, and whether the single session is free. Does not touch s23pub. Example {}.","parameters":{"type":"object","properties":{},"additionalProperties":false}}})S23",
    R"S23({"type":"function","function":{"name":"s23_tap","description":"Tap the S23 screen. Coordinates are device pixels. Takes the single session for this call and releases it. Example {\"x\":540,\"y\":1200}.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"],"additionalProperties":false}}})S23",
    R"S23({"type":"function","function":{"name":"s23_swipe","description":"Swipe on the S23. ms is optional, 1..5000. Example {\"x1\":500,\"y1\":1600,\"x2\":500,\"y2\":400,\"ms\":120}.","parameters":{"type":"object","properties":{"x1":{"type":"integer"},"y1":{"type":"integer"},"x2":{"type":"integer"},"y2":{"type":"integer"},"ms":{"type":"integer"}},"required":["x1","y1","x2","y2"],"additionalProperties":false}}})S23",
    R"S23({"type":"function","function":{"name":"s23_text","description":"Type visible ASCII into the S23. Spaces are allowed. Example {\"text\":\"Hallo\"}.","parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}}})S23",
    R"S23({"type":"function","function":{"name":"s23_key","description":"Send one Android key. Use key like KEYCODE_HOME, KEYCODE_BACK, KEYCODE_ENTER, or a numeric code. Example {\"key\":\"KEYCODE_HOME\"}.","parameters":{"type":"object","properties":{"key":{"type":"string"},"code":{"type":"integer"}},"additionalProperties":false}}})S23",
    R"S23({"type":"function","function":{"name":"s23_screenshot","description":"Capture one S23 screenshot. Returns size and latency only, not the image bytes. Example {}.","parameters":{"type":"object","properties":{},"additionalProperties":false}}})S23"};

static bool is_s23_tool(const std::string &name) {
  for (const char *spec : S23_TOOL_DEFS) {
    mj::Val v;
    if (!mj::parse(spec, v)) continue;
    auto fn = v.find("function");
    if (fn && fn->find("name") && fn->find("name")->asStr() == name) return true;
  }
  return false;
}

static size_t s23_curl_write(char *p, size_t n, size_t m, void *u) {
  auto *s = static_cast<std::string *>(u);
  size_t add = n * m;
  if (s->size() + add > 8 * 1024 * 1024) return 0;
  s->append(p, add);
  return add;
}

static std::string s23_token() {
  std::string direct = env("MAG_S23_TOKEN", "");
  if (!direct.empty()) return direct;
  std::string path = env("MAG_S23_TOKEN_FILE", "/run/mag-s23/token");
  bool ok = false;
  std::string file = read_file(path, &ok);
  if (!ok) return {};
  while (!file.empty() && (file.back() == '\n' || file.back() == '\r')) file.pop_back();
  return file;
}

struct S23Http {
  long status = 0;
  std::string body;
  std::string latency;
  std::string error;
};

static S23Http s23_http(const std::string &method, const std::string &path, const std::string &body,
                        const std::string &token, const std::string &session) {
  S23Http r;
  std::string port = env("MAG_S23_PORT", "18721");
  if (port == "8720") {
    r.error = "refusing s23pub port";
    return r;
  }
  std::string url = "http://127.0.0.1:" + port + path;
  CURL *c = curl_easy_init();
  if (!c) {
    r.error = "curl init failed";
    return r;
  }
  struct curl_slist *hdrs = nullptr;
  std::string auth = "Authorization: Bearer " + token;
  hdrs = curl_slist_append(hdrs, auth.c_str());
  hdrs = curl_slist_append(hdrs, "Content-Type: application/json");
  std::string sess;
  if (!session.empty()) {
    sess = "X-S23-Session: " + session;
    hdrs = curl_slist_append(hdrs, sess.c_str());
  }
  curl_easy_setopt(c, CURLOPT_URL, url.c_str());
  curl_easy_setopt(c, CURLOPT_HTTPHEADER, hdrs);
  curl_easy_setopt(c, CURLOPT_CUSTOMREQUEST, method.c_str());
  if (!body.empty()) curl_easy_setopt(c, CURLOPT_POSTFIELDS, body.c_str());
  curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, s23_curl_write);
  curl_easy_setopt(c, CURLOPT_WRITEDATA, &r.body);
  curl_easy_setopt(c, CURLOPT_TIMEOUT, 8L);
  curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(c, CURLOPT_PROTOCOLS_STR, "http");
  CURLcode rc = curl_easy_perform(c);
  curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &r.status);
  curl_slist_free_all(hdrs);
  curl_easy_cleanup(c);
  if (rc != CURLE_OK) r.error = curl_easy_strerror(rc);
  return r;
}

static std::string s23_down_message() {
  return "Fehler: S23-Steuerung ist nicht erreichbar auf 127.0.0.1:" + env("MAG_S23_PORT", "18721") +
         ". s23pub auf Port 8720 und cloudflared bleiben unberuehrt.";
}

static std::string s23_with_session(const std::string &token, const std::string &method,
                                    const std::string &path, const std::string &body) {
  S23Http open = s23_http("POST", "/v1/session", "{}", token, "");
  if (!open.error.empty() || open.status == 0) return s23_down_message();
  if (open.status == 409) return "Fehler: S23-Sitzung ist belegt.";
  if (open.status != 200) return "Fehler: S23-Sitzung abgelehnt (" + std::to_string(open.status) + ").";
  mj::Val rec;
  std::string sid;
  if (mj::parse(open.body, rec) && rec.isObj() && rec.find("session")) sid = rec.find("session")->asStr();
  if (sid.empty()) return "Fehler: S23-Sitzung ohne Kennung.";
  S23Http res = s23_http(method, path, body, token, sid);
  s23_http("DELETE", "/v1/session", "", token, sid);
  if (!res.error.empty()) return s23_down_message();
  if (res.status >= 400) return "Fehler: S23 " + path + " HTTP " + std::to_string(res.status) + " " + res.body;
  if (path == "/v1/screenshot")
    return "Screenshot ok bytes=" + std::to_string(res.body.size());
  return res.body.empty() ? "ok" : res.body;
}

static std::string s23_dispatch_tool(int, const std::string &name, const Val &args) {
  std::string token = s23_token();
  if (token.size() < 16)
    return "Fehler: S23-Steuerung ist nicht konfiguriert (kein Token). s23pub und cloudflared bleiben unberuehrt.";
  auto num = [&](const char *k) -> long {
    auto v = args.find(k);
    return v ? static_cast<long>(v->asNum()) : 0;
  };
  auto str = [&](const char *k) -> std::string {
    auto v = args.find(k);
    return v ? v->asStr() : "";
  };
  if (name == "s23_status") {
    S23Http res = s23_http("GET", "/v1/status", "", token, "");
    if (!res.error.empty() || res.status == 0) return s23_down_message();
    if (res.status >= 400) return "Fehler: S23 status HTTP " + std::to_string(res.status);
    return res.body;
  }
  if (name == "s23_tap")
    return s23_with_session(token, "POST", "/v1/input/tap",
                            "{\"x\":" + std::to_string(num("x")) + ",\"y\":" + std::to_string(num("y")) + "}");
  if (name == "s23_swipe")
    return s23_with_session(token, "POST", "/v1/input/swipe",
                            "{\"x1\":" + std::to_string(num("x1")) + ",\"y1\":" + std::to_string(num("y1")) +
                                ",\"x2\":" + std::to_string(num("x2")) + ",\"y2\":" + std::to_string(num("y2")) +
                                ",\"ms\":" + std::to_string(num("ms") ? num("ms") : 120) + "}");
  if (name == "s23_text") {
    mj::Val body = mj::Val::object();
    body["text"] = str("text");
    return s23_with_session(token, "POST", "/v1/input/text", mj::dump(body));
  }
  if (name == "s23_key") {
    mj::Val body = mj::Val::object();
    if (!str("key").empty()) body["key"] = str("key");
    else body["code"] = static_cast<double>(num("code"));
    return s23_with_session(token, "POST", "/v1/input/key", mj::dump(body));
  }
  if (name == "s23_screenshot") return s23_with_session(token, "GET", "/v1/screenshot", "");
  return "Fehler: unbekanntes S23-Werkzeug";
}
