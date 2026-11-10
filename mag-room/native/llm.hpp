// llama-server client. Every call goes through the audit gateway (which picks
// the bot's server by X-Mag-Slot) or, without a gateway, straight to the port.
// One slot per server: slot 0 holds exactly this bot's conversation.
#pragma once
#include "http.hpp"

namespace mag::llm {

struct Backend {
  std::string bot;     // "lena" / "noah": also the model id the gateway expects
  int index = 0;       // gateway backend index (NUMA node)
  int port = 0;        // llama-server port, used when there is no gateway
  std::string gateway; // e.g. http://127.0.0.1:8080, empty = direct
  std::string url(const std::string &path) const {
    return (gateway.empty() ? "http://127.0.0.1:" + std::to_string(port) : gateway) + path;
  }
  http::Headers headers() const {
    http::Headers h;
    if (!gateway.empty()) {
      h.push_back({"X-Mag-Slot", std::to_string(index)});
      h.push_back({"X-Mag-Bot", bot});
    }
    return h;
  }
};

struct ToolCall {
  std::string id, name, arguments; // arguments: JSON text as produced by the model
};

struct ChatResult {
  std::string content, reasoning, finish, error;
  std::vector<ToolCall> calls;
  bool cancelled = false;
  long prompt_tokens = 0, completion_tokens = 0, cached_tokens = 0;
  double prompt_ms = 0, gen_ms = 0, tps = 0;
  int64_t first_token_ms = 0; // wall time until the first generated token
  bool ok() const { return error.empty() && !cancelled; }
};

struct ChatCallbacks {
  std::function<void(const std::string &)> on_content, on_reasoning;
  std::function<void(int index, const std::string &name)> on_tool_start;
  std::function<void(long processed, long total, long cached)> on_progress;
};

inline std::string error_text(const http::ClientResult &r) {
  if (!r.error.empty()) return r.error;
  Val v = jparse(r.body);
  if (const Val *e = v.find("error")) {
    if (e->type == Val::STR) return e->str;
    const std::string m = jstr(*e, "message");
    if (!m.empty()) return m;
  }
  return "HTTP " + std::to_string(r.status);
}

// Streams one chat completion. `body` is the complete request (messages,
// tools, sampling); stream, cache and slot fields are set here.
inline ChatResult chat(const Backend &be, Val body, const Cancel *cancel, const ChatCallbacks &cb,
                       int budget_s = 3600) {
  ChatResult out;
  body["model"] = be.bot;
  body["stream"] = true;
  Val so = Val::object();
  so["include_usage"] = true;
  body["stream_options"] = so;
  body["cache_prompt"] = true;
  body["id_slot"] = 0;
  body["return_progress"] = true;

  const int64_t started = mono_ms();
  std::string buffer, http_body;
  std::map<int, ToolCall> calls;
  bool done = false;
  auto handle = [&](const std::string &data) {
    if (data == "[DONE]") { done = true; return; }
    Val ev;
    if (!mj::parse(data, ev) || !ev.isObj()) return;
    if (const Val *e = ev.find("error")) {
      out.error = e->type == Val::STR ? e->str : jstr(*e, "message", "Modellfehler");
      return;
    }
    if (const Val *pp = ev.find("prompt_progress"); pp && pp->isObj() && cb.on_progress)
      cb.on_progress((long)jnum(*pp, "processed"), (long)jnum(*pp, "total"), (long)jnum(*pp, "cache"));
    if (const Val *u = ev.find("usage"); u && u->isObj()) {
      out.prompt_tokens = (long)jnum(*u, "prompt_tokens");
      out.completion_tokens = (long)jnum(*u, "completion_tokens");
      if (const Val *d = u->find("prompt_tokens_details"); d && d->isObj())
        out.cached_tokens = (long)jnum(*d, "cached_tokens");
    }
    if (const Val *t = ev.find("timings"); t && t->isObj()) {
      out.prompt_ms = jnum(*t, "prompt_ms");
      out.gen_ms = jnum(*t, "predicted_ms");
      out.tps = jnum(*t, "predicted_per_second");
      if (!out.prompt_tokens) out.prompt_tokens = (long)(jnum(*t, "prompt_n") + jnum(*t, "cache_n"));
      if (!out.cached_tokens) out.cached_tokens = (long)jnum(*t, "cache_n");
      if (!out.completion_tokens) out.completion_tokens = (long)jnum(*t, "predicted_n");
    }
    const Val *choices = ev.find("choices");
    if (!choices || !choices->isArr() || choices->asArr().empty()) return;
    const Val &choice = choices->asArr()[0];
    if (const Val *f = choice.find("finish_reason"); f && f->type == Val::STR) out.finish = f->str;
    const Val *delta = choice.find("delta");
    if (!delta || !delta->isObj()) return;
    auto first = [&] { if (!out.first_token_ms) out.first_token_ms = mono_ms() - started; };
    for (const char *key : {"reasoning_content", "reasoning"}) {
      const std::string r = jstr(*delta, key);
      if (r.empty()) continue;
      first();
      out.reasoning += r;
      if (cb.on_reasoning) cb.on_reasoning(r);
      break;
    }
    const std::string c = jstr(*delta, "content");
    if (!c.empty()) {
      first();
      out.content += c;
      if (cb.on_content) cb.on_content(c);
    }
    if (const Val *tcs = delta->find("tool_calls"); tcs && tcs->isArr()) {
      first();
      for (auto &tc : tcs->asArr()) {
        const int index = (int)jnum(tc, "index");
        const bool fresh = !calls.count(index);
        ToolCall &call = calls[index];
        const std::string id = jstr(tc, "id");
        if (!id.empty()) call.id = id;
        if (const Val *fn = tc.find("function"); fn && fn->isObj()) {
          const std::string name = jstr(*fn, "name");
          if (!name.empty()) call.name = name;
          call.arguments += jstr(*fn, "arguments");
        }
        if (fresh && !call.name.empty() && cb.on_tool_start) cb.on_tool_start(index, call.name);
      }
    }
  };

  http::ClientOptions opt;
  opt.method = "POST";
  opt.body = mj::dump(body);
  opt.headers = be.headers();
  opt.headers.push_back({"Content-Type", "application/json"});
  opt.headers.push_back({"Accept", "text/event-stream"});
  opt.timeout_s = budget_s;
  opt.connect_timeout_s = 5;
  // The server sends progress and pings while it works; minutes of silence
  // mean it is gone.
  opt.stall_s = 300;
  opt.cancel = cancel;
  opt.on_data = [&](const char *data, size_t n) {
    if (http_body.size() < 65536) http_body.append(data, std::min(n, 65536 - http_body.size()));
    buffer.append(data, n);
    size_t nl;
    while ((nl = buffer.find('\n')) != std::string::npos) {
      std::string line = buffer.substr(0, nl);
      buffer.erase(0, nl + 1);
      if (!line.empty() && line.back() == '\r') line.pop_back();
      if (starts_with(line, "data:")) handle(trim(line.substr(5)));
    }
    return !done && out.error.empty();
  };
  const http::ClientResult res = http::fetch(be.url("/v1/chat/completions"), opt);
  out.cancelled = res.cancelled;
  if (!out.cancelled && out.error.empty()) {
    if (!res.error.empty()) out.error = "Modell nicht erreichbar: " + res.error;
    else if (res.status != 200) {
      http::ClientResult shaped = res;
      shaped.body = http_body;
      out.error = error_text(shaped);
    } else if (!done && out.finish.empty()) out.error = "Antwort unvollständig (Verbindung beendet)";
  }
  for (auto &kv : calls) {
    if (kv.second.name.empty()) continue;
    if (kv.second.id.empty()) kv.second.id = "call_" + random_hex(6);
    out.calls.push_back(kv.second);
  }
  return out;
}

// ---- slot actions (all need --slot-save-path on the server) ---------------
inline bool slot_action(const Backend &be, const std::string &action, const std::string &filename,
                        std::string *error = nullptr, int timeout_s = 120) {
  http::ClientOptions opt;
  opt.method = "POST";
  opt.headers = be.headers();
  opt.headers.push_back({"Content-Type", "application/json"});
  Val body = Val::object();
  if (!filename.empty()) body["filename"] = filename;
  opt.body = mj::dump(body);
  opt.timeout_s = timeout_s;
  const auto res = http::fetch(be.url("/slots/0?action=" + action), opt);
  if (res.ok()) return true;
  if (error) *error = error_text(res);
  return false;
}
// Empties the slot's KV cache. A running task defers the erase on the server,
// so the caller cancels its own request first.
inline bool slot_erase(const Backend &be, std::string *error = nullptr) {
  return slot_action(be, "erase", "", error, 60);
}
inline bool slot_save(const Backend &be, const std::string &file, std::string *error = nullptr) {
  return slot_action(be, "save", file, error);
}
inline bool slot_restore(const Backend &be, const std::string &file, std::string *error = nullptr) {
  return slot_action(be, "restore", file, error);
}

inline bool healthy(const Backend &be) {
  http::ClientOptions opt;
  opt.headers = be.headers();
  opt.timeout_s = 4;
  opt.connect_timeout_s = 2;
  return http::fetch(be.url("/health"), opt).status == 200;
}

inline Val props(const Backend &be) {
  http::ClientOptions opt;
  opt.headers = be.headers();
  opt.timeout_s = 6;
  opt.connect_timeout_s = 2;
  const auto res = http::fetch(be.url("/props"), opt);
  return res.ok() ? jparse(res.body) : Val();
}

} // namespace mag::llm
