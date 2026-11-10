// mag-stt: speech to text for the room, on the CPU cores that are not the
// models'. One process keeps the recognizer in memory, so a dictation never
// waits for a model to load, and answers the room on loopback:
//   GET    /health
//   POST   /v1/transcribe?lang=de        PCM s16le 16 kHz mono -> {"text"}
//   POST   /v1/stream/<id>?seq=N[&end=1] the next PCM chunk -> the text so far
//   DELETE /v1/stream/<id>               discard a dictation
//
// The model (Parakeet TDT, German fine-tune, GGUF on transcribe.cpp) hears a
// recording as a whole and needs a small fraction of its length to do so.
// Live text therefore comes from decoding the recording so far again each
// time new audio has arrived. What has been said stays said: a sentence that
// two decodes in a row agree on, and that is followed by a pause or lies far
// enough back, is committed and its audio dropped. A long dictation then
// costs no more per update than a short one, and committed text never changes.
#include "http.hpp"

#ifdef MAG_STT_TRANSCRIBE
#include "transcribe.h"
#endif

#include <cctype>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <deque>

namespace mag::stt {

constexpr int kRate = 16000;
constexpr int64_t kMinNewMs = 200;       // decode again once this much new audio is there ...
constexpr int64_t kMaxNewMs = 600;       // ... or more, the longer the open part of the recording is
constexpr int64_t kPauseSentenceMs = 900; // a finished sentence and this much silence: final
constexpr int64_t kPauseAnyMs = 2500;     // any words and this much silence: final
constexpr int64_t kLongMs = 12000;        // from here on, sentences that lie back are committed
constexpr int64_t kBackMs = 1500;         // ... if they ended at least this long ago
constexpr int64_t kHardMs = 28000;        // no sentence end in sight: cut at the longest gap
constexpr int64_t kLimitMs = 60000;       // never decode more than this at once
constexpr int64_t kSilenceDropMs = 8000;  // nothing said for this long: forget the silence
constexpr int64_t kSilenceKeepMs = 1500;
constexpr int64_t kIdleMs = 20000;        // a dictation without a chunk for this long is gone

inline size_t samples(int64_t ms) { return (size_t)(std::max<int64_t>(0, ms) * kRate / 1000); }
inline int64_t millis(size_t n) { return (int64_t)n * 1000 / kRate; }

struct Word {
  std::string text;
  int64_t t0 = 0, t1 = 0; // ms from the start of the decoded audio
};
struct Result {
  bool ok = false;
  std::string error;
  std::vector<Word> words;
};

inline std::string join(const std::vector<Word> &words, size_t from, size_t to) {
  std::string out;
  for (size_t i = from; i < to && i < words.size(); ++i) {
    if (!out.empty()) out += ' ';
    out += words[i].text;
  }
  return out;
}
inline void append(std::string &text, const std::string &more) {
  if (more.empty()) return;
  if (!text.empty()) text += ' ';
  text += more;
}
// Does this word close a sentence? Quotes and brackets after the mark do not count.
inline bool sentence_end(std::string word) {
  while (!word.empty() && (word.back() == '"' || word.back() == '\'' || word.back() == ')' || word.back() == ']')) word.pop_back();
  if (word.empty()) return false;
  return word.back() == '.' || word.back() == '!' || word.back() == '?' || ends_with(word, "\xE2\x80\xA6");
}

inline void drop_end_mark(std::string &word) {
  while (word.size() > 1 && (sentence_end(word) || word.back() == ',')) {
    if (ends_with(word, "\xE2\x80\xA6")) word.resize(word.size() - 3);
    else word.pop_back();
  }
}
// A word without its punctuation and case: "Tag." and "Tag," are the same word.
inline std::string bare(const std::string &word) {
  std::string out;
  for (size_t i = 0; i < word.size(); ++i) {
    const unsigned char c = (unsigned char)word[i];
    if (c < 0x80 && ispunct(c)) continue;
    if (word.compare(i, 3, "\xE2\x80\xA6") == 0) { i += 2; continue; }
    out += (char)tolower(c);
  }
  return out;
}
// What the reader sees of a hypothesis while the recording goes on. The end
// of a recording cuts through a word, and the model guesses there. So a word
// is shown once it has been heard to its end, the newest one only when the
// decode before this one heard the same, and a full stop once silence
// follows it.
constexpr int64_t kHeardMs = 300, kStopMs = 600;
inline std::string preview(const std::vector<Word> &now, size_t from, const std::vector<Word> &before, int64_t len_ms) {
  size_t to = now.size();
  while (to > from && now[to - 1].t1 > len_ms - kHeardMs) --to;
  if (to > from && (to > before.size() || bare(before[to - 1].text) != bare(now[to - 1].text))) --to;
  if (to <= from) return "";
  std::vector<Word> shown(now.begin() + (long)from, now.begin() + (long)to);
  if (to < now.size() || len_ms - now.back().t1 < kStopMs) drop_end_mark(shown.back().text);
  return join(shown, 0, shown.size());
}

// What of a hypothesis is final: its first `words` words, and the audio
// before `at_ms` is not needed again.
struct Cut {
  size_t words = 0;
  int64_t at_ms = 0;
};
inline Cut decide(const std::vector<Word> &now, const std::vector<Word> &before, int64_t len_ms) {
  if (now.empty()) return len_ms > kSilenceDropMs ? Cut{0, len_ms - kSilenceKeepMs} : Cut{};
  size_t agree = 0; // leading words the decode before this one already had
  while (agree < now.size() && agree < before.size() && now[agree].text == before[agree].text) ++agree;
  // Between two words the audio is cut in the middle of the gap.
  const auto after = [&](size_t i) {
    const int64_t next = i + 1 < now.size() ? now[i + 1].t0 : len_ms;
    return std::min(len_ms, now[i].t1 + std::max<int64_t>(0, next - now[i].t1) / 2);
  };
  const int64_t quiet = len_ms - now.back().t1;
  if (agree == now.size() && quiet >= (sentence_end(now.back().text) ? kPauseSentenceMs : kPauseAnyMs))
    return {now.size(), after(now.size() - 1)};
  if (len_ms > kLongMs)
    for (size_t i = agree; i-- > 0;)
      if (sentence_end(now[i].text) && now[i].t1 <= len_ms - kBackMs) return {i + 1, after(i)};
  if (len_ms > kHardMs) {
    size_t best = now.size();
    int64_t widest = -1;
    for (size_t i = 0; i < agree && i + 1 < now.size(); ++i) {
      const int64_t gap = now[i + 1].t0 - now[i].t1;
      if (now[i].t1 >= 4000 && now[i].t1 <= len_ms - 2000 && gap > widest) { widest = gap; best = i; }
    }
    if (best < now.size()) return {best + 1, after(best)};
  }
  // The text keeps changing and the recording keeps growing: keep the cost bounded.
  if (len_ms > kLimitMs)
    for (size_t i = now.size(); i-- > 0;)
      if (now[i].t1 <= len_ms - 2000) return {i + 1, after(i)};
  return {};
}

struct Engine {
  virtual ~Engine() = default;
  virtual std::string name() const = 0;
  virtual Result run(const float *pcm, size_t n, const std::string &lang) = 0;
};

// The CI engine: no model, the "speech" spells its own transcript. Every
// 400 ms slot with a constant level of k * 100 (of 32768) is the word "w<k>";
// a silent slot is a pause, and the word before a pause ends its sentence.
struct FakeEngine : Engine {
  std::string name() const override { return "fake"; }
  Result run(const float *pcm, size_t n, const std::string &) override {
    Result r;
    r.ok = true;
    const size_t slot = samples(400);
    std::vector<int> level;
    for (size_t at = 0; at + slot / 2 <= n; at += slot) {
      const size_t end = std::min(n, at + slot);
      double sum = 0;
      for (size_t i = at; i < end; ++i) sum += std::fabs(pcm[i]);
      level.push_back((int)std::lround(sum / (double)(end - at) * 32768.0 / 100.0));
    }
    for (size_t i = 0; i < level.size(); ++i) {
      if (level[i] <= 0) continue;
      Word w;
      w.text = "w" + std::to_string(level[i]);
      if (i + 1 < level.size() && level[i + 1] <= 0) w.text += '.';
      w.t0 = (int64_t)i * 400;
      w.t1 = w.t0 + 300;
      r.words.push_back(w);
    }
    return r;
  }
};

#ifdef MAG_STT_TRANSCRIBE
struct Transcribe : Engine {
  transcribe_session *session = nullptr;
  std::string label;
  Transcribe(const std::string &path, int threads) {
    // The journal gets this program's own lines only (MAG_STT_DEBUG=1: the library's too).
    if (env("MAG_STT_DEBUG") != "1") transcribe_log_set(nullptr, nullptr);
    transcribe_model_load_params load;
    transcribe_model_load_params_init(&load);
    load.backend = TRANSCRIBE_BACKEND_CPU; // never a GPU: the MI50 belongs to Idol
    transcribe_session_params params;
    transcribe_session_params_init(&params);
    params.n_threads = threads;
    const transcribe_status st = transcribe_open(path.c_str(), &load, &params, &session);
    if (st != TRANSCRIBE_OK) throw std::runtime_error(std::string("cannot load ") + path + ": " + transcribe_status_string(st));
    const transcribe_model *model = transcribe_get_model(session);
    const char *variant = transcribe_model_variant_string(model), *backend = transcribe_model_backend(model);
    label = std::string(variant ? variant : "model") + " on " + (backend ? backend : "cpu");
  }
  ~Transcribe() override { transcribe_close(session); }
  std::string name() const override { return label; }
  Result run(const float *pcm, size_t n, const std::string &lang) override {
    Result r;
    if (n < samples(100)) { // too short for a single frame of the model
      r.ok = true;
      return r;
    }
    transcribe_run_params params;
    transcribe_run_params_init(&params);
    params.timestamps = TRANSCRIBE_TIMESTAMPS_WORD;
    params.language = lang.empty() ? nullptr : lang.c_str();
    const transcribe_status st = transcribe_run(session, pcm, (int)n, &params);
    if (st != TRANSCRIBE_OK) {
      r.error = transcribe_status_string(st);
      return r;
    }
    r.ok = true;
    const int count = transcribe_n_words(session);
    for (int i = 0; i < count; ++i) {
      transcribe_word w;
      transcribe_word_init(&w);
      if (transcribe_get_word(session, i, &w) != TRANSCRIBE_OK || !w.text) continue;
      const std::string text = trim(w.text);
      if (!text.empty()) r.words.push_back({text, w.t0_ms, w.t1_ms});
    }
    // A model without word times still has its text.
    if (r.words.empty()) {
      const char *full = transcribe_full_text(session);
      const std::string text = trim(full ? full : "");
      if (!text.empty()) r.words.push_back({text, 0, millis(n)});
    }
    return r;
  }
};
#endif

// A whole recording of any length: decoded in windows that end at a sentence
// (or, failing that, at the longest gap), so no window is cut inside a word.
inline Result long_form(Engine &engine, const std::vector<float> &pcm, const std::string &lang) {
  Result all;
  all.ok = true;
  size_t pos = 0;
  while (pos < pcm.size()) {
    const size_t len = std::min(pcm.size() - pos, samples(40000));
    Result r = engine.run(pcm.data() + pos, len, lang);
    if (!r.ok) return r;
    const int64_t base = millis(pos), len_ms = millis(len);
    size_t keep = r.words.size(), advance = len;
    if (pos + len < pcm.size() && !r.words.empty()) {
      size_t best = r.words.size();
      int64_t widest = -1;
      for (size_t i = 0; i + 1 < r.words.size(); ++i) {
        if (r.words[i].t1 < 10000 || r.words[i].t1 > len_ms - 3000) continue;
        const int64_t gap = r.words[i + 1].t0 - r.words[i].t1 + (sentence_end(r.words[i].text) ? 100000 : 0);
        if (gap >= widest) { widest = gap; best = i; }
      }
      if (best < r.words.size()) {
        keep = best + 1;
        advance = std::max(samples(r.words[best].t1 + (r.words[best + 1].t0 - r.words[best].t1) / 2), samples(1000));
      }
    }
    for (size_t i = 0; i < keep; ++i) all.words.push_back({r.words[i].text, r.words[i].t0 + base, r.words[i].t1 + base});
    pos += advance;
  }
  return all;
}

// Nothing but silence needs no decode.
inline bool silent(const std::vector<float> &pcm) {
  for (const float x : pcm)
    if (std::fabs(x) > 0.004f) return false;
  return true;
}

struct Stream {
  std::string id, lang;
  std::vector<float> audio; // not committed yet
  std::vector<Word> before; // the hypothesis of the last decode, for agreement
  std::string committed, partial, error;
  size_t decoded = 0;       // samples of `audio` the hypothesis covers
  int seq = 0, rev = 0;
  bool ending = false, done = false;
  int64_t touched = 0;
  size_t heard = 0;         // samples received in all
  int decodes = 0;
  int64_t busy_ms = 0;      // time the recognizer spent on this dictation
};
struct Job {
  std::vector<float> pcm;
  std::string lang;
  Result result;
  bool done = false;
};

class Service {
public:
  explicit Service(std::unique_ptr<Engine> engine) : engine_(std::move(engine)) {}

  void loop(const std::atomic<bool> &stop) {
    std::unique_lock<std::mutex> lk(mu_);
    while (!stop) {
      if (!jobs_.empty()) {
        const std::shared_ptr<Job> job = jobs_.front();
        jobs_.pop_front();
        lk.unlock();
        Result r = long_form(*engine_, job->pcm, job->lang);
        lk.lock();
        job->result = std::move(r);
        job->done = true;
        news_.notify_all();
        continue;
      }
      const std::shared_ptr<Stream> s = pick();
      if (!s) {
        work_.wait_for(lk, std::chrono::milliseconds(500));
        forget_idle();
        continue;
      }
      const std::vector<float> pcm = s->audio;
      const std::string lang = s->lang;
      lk.unlock();
      const int64_t began = mono_ms();
      Result r;
      if (pcm.size() >= samples(100) && !silent(pcm)) r = engine_->run(pcm.data(), pcm.size(), lang);
      else r.ok = true;
      lk.lock();
      ++s->decodes;
      s->busy_ms += mono_ms() - began;
      apply(*s, r, pcm.size());
      // The journal gets numbers, never what was said.
      if (s->done)
        logf("mag-stt: dictation of " + std::to_string(millis(s->heard) / 1000) + " s, " + std::to_string(s->decodes) + " decodes, " +
             std::to_string(s->busy_ms) + " ms of recognizer time" + (s->error.empty() ? "" : ", error: " + s->error));
      news_.notify_all();
    }
  }

  void handle(int fd, http::Request &r) {
    if (r.method == "GET" && r.path == "/health") {
      Val v = Val::object();
      v["ok"] = true;
      v["engine"] = engine_->name();
      {
        std::lock_guard<std::mutex> lk(mu_);
        v["streams"] = (double)streams_.size();
      }
      return http::send_json(fd, 200, v);
    }
    std::string lang = r.param("lang");
    if (lang == "auto") lang.clear();
    else if (lang != "en") lang = "de";
    if (r.method == "POST" && r.path == "/v1/transcribe") return transcribe(fd, r, lang);
    if (starts_with(r.path, "/v1/stream/")) {
      const std::string id = r.path.substr(11);
      const bool valid = id.size() >= 8 && id.size() <= 64 &&
                         std::all_of(id.begin(), id.end(), [](unsigned char c) { return isalnum(c) || c == '-' || c == '_'; });
      if (!valid) return http::send_error(fd, 400, "Ungültige Diktat-Kennung");
      if (r.method == "POST") return chunk(fd, r, id, lang);
      if (r.method == "DELETE") {
        std::lock_guard<std::mutex> lk(mu_);
        streams_.erase(id);
        return http::send_ok(fd);
      }
    }
    http::send_error(fd, 404, "Nicht gefunden");
  }

private:
  static bool pcm_of(const std::string &body, std::vector<float> &out) {
    if (body.size() % 2) return false;
    const size_t n = body.size() / 2, at = out.size();
    out.resize(at + n);
    const auto *p = (const unsigned char *)body.data();
    for (size_t i = 0; i < n; ++i) out[at + i] = (float)(int16_t)(p[2 * i] | (p[2 * i + 1] << 8)) / 32768.0f;
    return true;
  }

  void transcribe(int fd, const http::Request &r, const std::string &lang) {
    const auto job = std::make_shared<Job>();
    job->lang = lang;
    if (!pcm_of(r.body, job->pcm) || job->pcm.size() < samples(100)) return http::send_error(fd, 400, "Keine Aufnahme");
    const int64_t began = mono_ms();
    std::unique_lock<std::mutex> lk(mu_);
    jobs_.push_back(job);
    work_.notify_one();
    if (!news_.wait_for(lk, std::chrono::seconds(600), [&] { return job->done; })) return http::send_error(fd, 504, "Spracherkennung dauert zu lange");
    lk.unlock();
    if (!job->result.ok) return http::send_error(fd, 500, "Spracherkennung fehlgeschlagen: " + job->result.error);
    Val v = Val::object();
    v["text"] = join(job->result.words, 0, job->result.words.size());
    v["audio_ms"] = (double)millis(job->pcm.size());
    v["ms"] = (double)(mono_ms() - began);
    http::send_json(fd, 200, v);
  }

  // One chunk of a dictation. Chunks carry a running number, so one that is
  // sent again after a lost answer is not heard twice.
  void chunk(int fd, const http::Request &r, const std::string &id, const std::string &lang) {
    const int seq = atoi(r.param("seq").c_str());
    const bool end = r.param("end") == "1";
    std::unique_lock<std::mutex> lk(mu_);
    std::shared_ptr<Stream> s;
    const auto it = streams_.find(id);
    if (it != streams_.end()) s = it->second;
    else if (seq == 0) {
      if (streams_.size() >= 8) return http::send_error(fd, 429, "Zu viele Diktate gleichzeitig");
      s = std::make_shared<Stream>();
      s->id = id;
      s->lang = lang;
      streams_[id] = s;
    } else return http::send_error(fd, 410, "Dieses Diktat gibt es nicht mehr");
    if (seq > s->seq) return http::send_error(fd, 409, "Ein Teil der Aufnahme fehlt");
    if (seq == s->seq) {
      if (!pcm_of(r.body, s->audio)) return http::send_error(fd, 400, "Ungültige Audiodaten");
      s->heard += r.body.size() / 2;
      ++s->seq;
    }
    s->touched = mono_ms();
    if (end) s->ending = true;
    work_.notify_one();
    if (end) {
      if (!news_.wait_for(lk, std::chrono::seconds(90), [&] { return s->done; })) return http::send_error(fd, 504, "Spracherkennung dauert zu lange");
      streams_.erase(id);
    } else if (s->audio.size() > s->decoded) {
      // The text for this chunk is usually ready within a moment: take it along.
      const int rev = s->rev;
      news_.wait_for(lk, std::chrono::milliseconds(150), [&] { return s->rev != rev || s->done; });
    }
    Val v = Val::object();
    v["committed"] = s->committed;
    v["partial"] = s->partial;
    v["rev"] = (double)s->rev;
    v["final"] = s->done;
    if (!s->error.empty()) v["error"] = s->error;
    lk.unlock();
    http::send_json(fd, 200, v);
  }

  // The dictation that needs the recognizer most: one that is ending, else
  // the one whose new audio has waited longest.
  std::shared_ptr<Stream> pick() {
    std::shared_ptr<Stream> best;
    for (auto &[id, s] : streams_) {
      if (s->done) continue;
      if (s->ending) return s;
      const int64_t open_ms = millis(s->audio.size());
      if (s->audio.size() - s->decoded < samples(std::clamp<int64_t>(open_ms / 16, kMinNewMs, kMaxNewMs))) continue;
      if (!best || s->touched < best->touched) best = s;
    }
    return best;
  }
  void forget_idle() {
    const int64_t now = mono_ms();
    for (auto it = streams_.begin(); it != streams_.end();)
      it = now - it->second->touched > kIdleMs ? streams_.erase(it) : std::next(it);
  }

  void apply(Stream &s, const Result &r, size_t n) {
    // The whole recording was decoded and no more will come: that is the result.
    const bool final = s.ending && n == s.audio.size();
    if (!r.ok) {
      s.error = r.error;
      s.decoded = n;
      if (final) s.done = true;
      return;
    }
    ++s.rev;
    if (final) {
      append(s.committed, join(r.words, 0, r.words.size()));
      s.audio.clear();
      s.before.clear();
      s.partial.clear();
      s.decoded = 0;
      s.done = true;
      return;
    }
    const Cut cut = decide(r.words, s.before, millis(n));
    append(s.committed, join(r.words, 0, cut.words));
    s.partial = preview(r.words, cut.words, s.before, millis(n));
    std::vector<Word> rest(r.words.begin() + (long)cut.words, r.words.end());
    const size_t drop = std::min(samples(cut.at_ms), n);
    for (Word &w : rest) {
      w.t0 -= cut.at_ms;
      w.t1 -= cut.at_ms;
    }
    s.audio.erase(s.audio.begin(), s.audio.begin() + (long)drop);
    s.before = std::move(rest);
    s.decoded = n - drop;
  }

  std::unique_ptr<Engine> engine_;
  std::mutex mu_;
  std::condition_variable work_, news_;
  std::map<std::string, std::shared_ptr<Stream>> streams_;
  std::deque<std::shared_ptr<Job>> jobs_;
};

} // namespace mag::stt

int main() {
  using namespace mag;
  signal(SIGPIPE, SIG_IGN);
  static std::atomic<bool> stop{false};
  struct sigaction sa{};
  sa.sa_handler = [](int) { stop = true; };
  sigaction(SIGTERM, &sa, nullptr);
  sigaction(SIGINT, &sa, nullptr);

  std::unique_ptr<stt::Engine> engine;
  if (env("MAG_STT_FAKE") == "1") engine = std::make_unique<stt::FakeEngine>();
#ifdef MAG_STT_TRANSCRIBE
  else {
    const std::string model = env("MAG_STT_MODEL", "/var/lib/mag-stack/models/parakeet-primeline-Q8_0.gguf");
    try {
      auto real = std::make_unique<stt::Transcribe>(model, (int)envl("MAG_STT_THREADS", 16));
      // The first decode pays for page faults and thread start: pay now.
      const std::vector<float> silence(stt::samples(1000), 0.0f);
      real->run(silence.data(), silence.size(), "de");
      engine = std::move(real);
    } catch (const std::exception &e) {
      logf(std::string("mag-stt: ") + e.what());
      return 1;
    }
  }
#endif
  if (!engine) {
    logf("mag-stt: built without a recognizer (see ops/stt/bootstrap.sh)");
    return 1;
  }
  const std::string host = env("MAG_STT_BIND", "127.0.0.1");
  const int port = (int)envl("MAG_STT_PORT", 8194);
  const int listener = http::listen_on(host, port);
  if (listener < 0) {
    logf("mag-stt: cannot listen on " + host + ":" + std::to_string(port));
    return 1;
  }
  stt::Service service(std::move(engine));
  std::thread worker([&] { service.loop(stop); });
  logf("mag-stt ready on " + host + ":" + std::to_string(port));
  // A minute of 16 kHz PCM is 1.9 MB; whole recordings are limited to 40 MB.
  http::serve(listener, false, [&](int fd, http::Request &r) { service.handle(fd, r); }, stop, 40u << 20);
  worker.join();
  _exit(0);
}
