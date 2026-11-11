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
  std::string id, name, arguments; // arguments: a JSON object as text ("{}" when the model's were beyond repair)
  bool valid = true;               // false: what the model wrote as arguments was no JSON object
};

struct ChatResult {
  std::string content, reasoning, finish, error;
  std::vector<ToolCall> calls;
  bool cancelled = false;
  // Why the room ended the generation itself: "repeat" (the answer ran in
  // circles and is cut to its first round), "thinking" (the thoughts did) or
  // "call" (a tool call ran away into raw model markup).
  std::string guard;
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

// ---- what a model wrote, made fit for the chat ------------------------------
// Control tokens of the two model families (Gemma 4, gpt-oss). The server
// reads them and hands on text, thoughts and tool calls. They reach the room
// raw when its parser gives up, when a tool call runs away (see `chat`), or
// inside an error that quotes what the model wrote. None of them is ever text
// for the chat.
inline const std::vector<std::string> &control_tags() {
  static const std::vector<std::string> tags = {
      "<|channel>", "<channel|>", "<|tool_call>", "<tool_call|>", "<|tool_response>", "<tool_response|>", "<|turn>", "<turn|>",
      "<|\"|>", "<start_of_turn>", "<end_of_turn>", "<bos>", "<eos>",
      "<|start|>", "<|end|>", "<|message|>", "<|channel|>", "<|constrain|>", "<|return|>", "<|call|>"};
  return tags;
}
// Where the first control tag stands in a text, at or after `from`.
inline size_t find_tag(const std::string &text, size_t from = 0) {
  for (size_t at = text.find('<', from); at != std::string::npos; at = text.find('<', at + 1))
    for (auto &tag : control_tags())
      if (text.compare(at, tag.size(), tag) == 0) return at;
  return std::string::npos;
}

// What went wrong, for the chat: one short line. A server's error can quote
// everything a model wrote, markup and all, with its line ends spelled out.
inline std::string short_error(std::string text) {
  if (text.find("Failed to parse tool call arguments") != std::string::npos)
    return "Ein Werkzeugaufruf im Verlauf war nicht lesbar; der Modell-Server hat die Anfrage abgelehnt.";
  const size_t tag = find_tag(text);
  if (tag != std::string::npos) text.erase(tag);
  replace_all(text, "\\n", " ");
  for (char &c : text)
    if (c == '\n' || c == '\r' || c == '\t') c = ' ';
  while (text.find("  ") != std::string::npos) replace_all(text, "  ", " ");
  return clip(trim(text), 240, "…");
}

// Takes a model's content as it streams and tells text from thinking. Raw
// channel markup is read for what it means: Gemma's <|channel>thought …
// <channel|> and gpt-oss's <|channel|>analysis<|message|> … <|end|> are
// thoughts, a raw tool call and a turn the model made up for somebody else
// are dropped, every other tag just disappears. A tag that is still arriving
// is held back until it is whole. Inside code (a fenced block, an inline
// span) nothing is markup: a bot may well explain a prompt format.
class Clean {
public:
  void feed(const std::string &delta, std::string &text, std::string &thought) {
    pend_ += delta;
    size_t i = 0;
    while (i < pend_.size()) {
      const size_t lt = pend_.find('<', i);
      if (mode_ == HEAD && kind_ != 'h') {
        // Gemma: the name of a channel or the role of a turn, up to the end of its line.
        const size_t nl = pend_.find('\n', i), end = std::min(nl, lt);
        head_ += pend_.substr(i, end == std::string::npos ? std::string::npos : end - i);
        if (end == std::string::npos && head_.size() < 40) { i = pend_.size(); break; }
        open(false);
        i = end == std::string::npos ? pend_.size() : end + (end == nl ? 1 : 0);
        continue;
      }
      put(pend_.substr(i, lt == std::string::npos ? std::string::npos : lt - i), text, thought);
      if (lt == std::string::npos) { i = pend_.size(); break; }
      std::string tag;
      bool partial = false;
      if (mode_ != TEXT || !code())
        for (auto &t : control_tags()) {
          if (pend_.compare(lt, t.size(), t) == 0) { tag = t; break; }
          if (pend_.size() - lt < t.size() && t.compare(0, pend_.size() - lt, pend_, lt, std::string::npos) == 0) partial = true;
        }
      if (tag.empty()) {
        if (partial) { pend_.erase(0, lt); return; } // the rest of the tag is still to come
        put("<", text, thought);
        i = lt + 1;
        continue;
      }
      i = lt + tag.size();
      take(tag, text, thought);
    }
    pend_.clear();
  }
  // The stream is over. The beginning of a tag that never arrived is no text.
  void flush(std::string &text, std::string &thought) {
    const std::string rest = pend_;
    pend_.clear();
    if (mode_ != HEAD && rest.size() < 2) put(rest, text, thought);
  }

private:
  enum Mode { TEXT, THOUGHT, DROP, HEAD } mode_ = TEXT;
  char kind_ = 0; // of a head: 'c' Gemma channel, 't' turn role, 'h' gpt-oss message header
  std::string pend_, head_;
  bool fence_ = false, inline_ = false; // the text is inside a code block / an inline code span
  size_t ticks_ = 0;                    // backticks in a row, so far

  // Is the text inside code right now? Backticks that just arrived count already.
  bool code() const {
    const bool fence = ticks_ >= 3 ? !fence_ : fence_;
    return fence || (ticks_ > 0 && ticks_ < 3 ? !inline_ : inline_);
  }

  void put(const std::string &piece, std::string &text, std::string &thought) {
    if (mode_ == HEAD) head_ += piece; // gpt-oss: channel and recipient of the message that follows
    else if (mode_ == THOUGHT) thought += piece;
    else if (mode_ == TEXT) {
      text += piece;
      for (const char c : piece) {
        if (c == '`') { ++ticks_; continue; }
        if (ticks_ >= 3) fence_ = !fence_;
        else if (ticks_ > 0 && !fence_) inline_ = !inline_;
        ticks_ = 0;
        if (c == '\n') inline_ = false;
      }
    }
  }
  // The head is complete: what follows it?
  void open(bool message) {
    const std::string head = trim(head_);
    head_.clear();
    if (kind_ == 'c') mode_ = THOUGHT;                                  // "thought"; no other channel is known
    else if (kind_ == 't') mode_ = starts_with(head, "model") || starts_with(head, "assistant") ? TEXT : DROP;
    else if (!message) mode_ = TEXT;
    else if (head.find("to=") != std::string::npos) mode_ = DROP;      // a raw tool call
    else mode_ = head.find("analysis") != std::string::npos ? THOUGHT : TEXT;
  }
  void take(const std::string &tag, std::string &text, std::string &thought) {
    if (mode_ == HEAD) { // gpt-oss, inside a header
      if (tag == "<|message|>") return open(true);
      if (tag == "<|channel|>" || tag == "<|constrain|>") { head_ += " "; return; }
      open(false);
    }
    if (tag == "<|channel>") { mode_ = HEAD; kind_ = 'c'; }
    else if (tag == "<|turn>" || tag == "<start_of_turn>") { mode_ = HEAD; kind_ = 't'; }
    else if (tag == "<|start|>" || tag == "<|channel|>") { mode_ = HEAD; kind_ = 'h'; }
    else if (tag == "<|tool_call>" || tag == "<|tool_response>") mode_ = DROP;
    else if (tag == "<|\"|>") put("\"", text, thought);
    else if (tag != "<|constrain|>" && tag != "<bos>" && tag != "<eos>") mode_ = TEXT; // a closing tag ends what it closed
  }
};

// A model's thoughts as text for the chat. Gemma thinks in an indented
// outline: every line a sub-item, the headings left out, the first line
// already stripped of its indent by the server, and now and then an item
// with nothing behind its bullet. Rendered as it comes that is a list inside
// a list with empty entries. The indent all lines share goes, an item
// without words goes, and so do runs of empty lines.
inline std::string tidy_thoughts(const std::string &raw) {
  std::vector<std::string> lines;
  std::istringstream in(raw);
  for (std::string line; std::getline(in, line);) {
    while (!line.empty() && (line.back() == ' ' || line.back() == '\t' || line.back() == '\r')) line.pop_back();
    lines.push_back(line);
  }
  auto indent = [](const std::string &line) { return line.find_first_not_of(' '); };
  // What all lines but the first are indented by; the first has lost its own.
  size_t shared = std::string::npos, first = std::string::npos;
  for (size_t i = 0; i < lines.size(); ++i) {
    if (lines[i].empty()) continue;
    if (first == std::string::npos) first = i;
    else shared = std::min(shared, indent(lines[i]));
  }
  if (shared == std::string::npos) shared = 0;
  std::string out;
  size_t blank = 0;
  for (size_t i = 0; i < lines.size(); ++i) {
    std::string line = lines[i].empty() ? "" : lines[i].substr(std::min(shared, indent(lines[i])));
    // A bullet or a number with nothing behind it.
    const std::string bare = trim(line);
    const bool marker = bare == "*" || bare == "-" || bare == "+" || bare == "•" || bare == "–" ||
                        (bare.size() >= 2 && bare.size() <= 4 && (bare.back() == '.' || bare.back() == ')') &&
                         bare.find_first_not_of("0123456789") == bare.size() - 1);
    if (marker) continue;
    blank = line.empty() ? blank + 1 : 0;
    if (blank > 1 || (line.empty() && out.empty())) continue;
    out += line + "\n";
  }
  return trim(out);
}

// Where a text starts to run in circles, or npos. A model that lost its way
// writes the same passage again and again until its tokens run out. Prose
// does not say a long passage three times in a row, or a sentence four
// times; code and the arguments of a tool call may repeat themselves (a grid
// of equal cells, a list of equal actions), so they must be far over the top
// before they count (`strict`). The text is to be cut at the returned
// length: what came before the circles, and one round.
inline size_t circles(const std::string &s, bool strict) {
  const size_t n = s.size(), probe = 32, npos = std::string::npos;
  if (n < 180) return npos;
  // Never in the middle of a character.
  auto whole = [&](size_t keep) {
    while (keep < n && ((unsigned char)s[keep] & 0xC0) == 0x80) ++keep;
    return keep;
  };
  // The distance to where the end of the text stood before is the length of
  // a round; the circle began where the text first repeats at that distance.
  const size_t before = s.rfind(s.substr(n - probe), n - probe - 1);
  if (before != npos) {
    const size_t p = n - probe - before;
    size_t start = n - p;
    while (start > 0 && s[start - 1] == s[start - 1 + p]) --start;
    const size_t run = n - start;
    const bool loop = strict ? run >= 8 * p && run >= 6000 : run >= 3 * p && (p >= 150 || run >= 2000 || (p >= 60 && run >= 4 * p));
    if (loop) return whole(start + p);
  }
  if (strict || n < 480) return npos;
  // The same long passage for the fourth time, whatever stood between: circles with a little variation.
  const std::string passage = s.substr(n - 120);
  size_t first = npos, seen = 0;
  for (size_t at = s.find(passage); at != npos; at = s.find(passage, at + passage.size())) {
    if (first == npos) first = at;
    ++seen;
  }
  return seen >= 4 ? whole(first + passage.size()) : npos;
}
// Inside a code block that is still open, the text is code: equal lines are normal there.
inline bool in_code(const std::string &s) {
  size_t fences = 0;
  for (size_t at = s.find("```"); at != std::string::npos; at = s.find("```", at + 3)) ++fences;
  return fences % 2 == 1;
}

// The arguments of a tool call as a JSON object. A call that ran away
// carries, behind what was meant, the model's raw markup and whatever it
// wrote after it: that is cut off, and a string the model closed with a plain
// quote where its own quote token belonged (the server then escapes the
// quote and reads on) is closed. What is no JSON object after that is "{}",
// and the call is marked: such arguments must never enter a prompt log, the
// server refuses every later request that contains them.
inline void mend(ToolCall &call) {
  std::string text = call.arguments;
  const size_t tag = find_tag(text);
  if (tag != std::string::npos) text.erase(tag);
  text = trim(text);
  if (text.empty()) text = "{}";
  if (jparse(text).isObj()) { call.arguments = text; return; }
  // …\"} : the escaped quote was the end of the string, and only closers follow it.
  const size_t quote = text.rfind("\\\"");
  if (quote != std::string::npos && text.find_first_not_of("}] \n\t", quote + 2) == std::string::npos) {
    std::string closed = text;
    closed.erase(quote, 1);
    if (jparse(closed).isObj()) { call.arguments = closed; return; }
  }
  call.arguments = "{}";
  call.valid = false;
}

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
  Clean clean, clean_thoughts;
  size_t checked_content = 0, checked_reasoning = 0;
  // The room ends a generation that lost its way instead of waiting for its
  // tokens to run out: the server stops when the connection closes.
  auto halt = [&](const char *why) { if (out.guard.empty()) out.guard = why; };
  auto take = [&](const std::string &text, const std::string &thought) {
    if (!thought.empty()) {
      out.reasoning += thought;
      if (cb.on_reasoning) cb.on_reasoning(thought);
      if (out.reasoning.size() >= checked_reasoning + 48) {
        checked_reasoning = out.reasoning.size();
        const size_t keep = circles(out.reasoning, false);
        if (keep != std::string::npos) {
          out.reasoning.resize(keep);
          halt("thinking");
        }
      }
    }
    if (!text.empty()) {
      out.content += text;
      if (cb.on_content) cb.on_content(text);
      if (out.content.size() >= checked_content + 48) {
        checked_content = out.content.size();
        const size_t keep = circles(out.content, in_code(out.content));
        if (keep != std::string::npos) {
          out.content.resize(keep);
          halt("repeat");
        }
      }
    }
  };
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
      std::string text, thought; // thoughts stay thoughts, whatever markup they carry
      clean_thoughts.feed(r, text, thought);
      take("", text + thought);
      break;
    }
    const std::string c = jstr(*delta, "content");
    if (!c.empty()) {
      first();
      std::string text, thought;
      clean.feed(c, text, thought);
      take(text, thought);
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
          const std::string more = jstr(*fn, "arguments");
          const size_t had = call.arguments.size();
          call.arguments += more;
          // Raw markup inside the arguments: the model closed a string its
          // own way, the server's grammar still holds it inside that string,
          // and it would now write on (its answer, again and again) until
          // its tokens run out. What it meant stands before the first tag.
          if (!more.empty() && (find_tag(call.arguments, had > 24 ? had - 24 : 0) != std::string::npos || circles(call.arguments, true) != std::string::npos))
            halt("call");
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
    return !done && out.error.empty() && out.guard.empty();
  };
  const http::ClientResult res = http::fetch(be.url("/v1/chat/completions"), opt);
  out.cancelled = res.cancelled;
  if (out.guard.empty()) {
    std::string text, thought, more;
    clean_thoughts.flush(more, more);
    clean.flush(text, thought);
    take(text, more + thought);
  }
  if (!out.cancelled && out.error.empty() && out.guard.empty()) {
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
    mend(kv.second);
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
