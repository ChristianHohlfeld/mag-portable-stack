#pragma once
// Conservative input-channel adaptation of PhoLine / PhO-Token-Wire.
// Runs only on a new wire message; no retroactive mutation of warm llama KV.
#include "pho_codec.hpp"
#include <string>

namespace pho {
struct LiveDecision {
  std::string text;
  long before = -1, after = -1;
  bool accepted = false;
};
inline unsigned live_words(const std::string &s) {
  unsigned n=0; bool word=false;
  for (unsigned char c : s) {
    bool next=(c>='0'&&c<='9')||(c>='A'&&c<='Z')||(c>='a'&&c<='z')||c>=0x80;
    if(next&&!word)++n;
    word=next;
  }
  return n;
}
// Let the active model's /tokenize endpoint decide. Never infer savings from
// bytes, IPA or OpenAI's o200k vocabulary; fail closed on tokenizer failure.
template<class TokenCount>
LiveDecision live_encode(const std::string &raw, TokenCount &&count) {
  LiveDecision d; d.text=raw;
  // Recent instructions remain exact unless substantial plain prose gives a
  // convincing token saving. Explicit structured/quoted text stays verbatim.
  if(raw.size()<80 || raw.size()>4000 || raw.find('\n')!=std::string::npos ||
     raw.find('"')!=std::string::npos || raw.find('[')!=std::string::npos ||
     raw.find(']')!=std::string::npos || !eligible(raw)) return d;
  const unsigned words=live_words(raw);
  if(words<16)return d;
  const auto channel=encode(raw).text;
  if(channel==raw || live_words(channel)*5<words*3)return d;
  d.before=count(raw);
  if(d.before<24)return d;
  d.after=count(channel);
  // Require at least 4 real saved tokens and 15% savings. The one-time model
  // instruction already exists in MAG; no per-message control prefix.
  if(d.after<=0 || d.before-d.after<4 || d.after*100>d.before*85)return d;
  d.text=channel;d.accepted=true;
  return d;
}
} // namespace pho
