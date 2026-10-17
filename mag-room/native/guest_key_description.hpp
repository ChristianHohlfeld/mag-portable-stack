// Tailscale auth-key description and error-journal hygiene.
// Pure C++17 helpers, shared by magroom and the compiled native unit test.
#pragma once
#include <string>
#include <string_view>

inline std::string mag_guest_key_description(std::string_view name) {
  // Tailscale accepts plain ASCII descriptions; do not let a free-form
  // display name insert punctuation, UTF-8 or control bytes into the API.
  // The prefix itself is safe. Max 50 bytes including the prefix.
  constexpr size_t max_bytes = 50;
  std::string out = "MAG guest ";
  bool have_name = false, pending_space = false;
  for (unsigned char byte : name) {
    const char c = static_cast<char>(byte);
    const bool valid = (c >= 'A' && c <= 'Z') ||
                       (c >= 'a' && c <= 'z') ||
                       (c >= '0' && c <= '9') ||
                       c == '-' || c == '_';
    if (!valid) { pending_space = true; continue; }
    const size_t need = 1 + (have_name && pending_space ? 1 : 0);
    if (out.size() + need > max_bytes) break;
    if (have_name && pending_space) out.push_back(' ');
    out.push_back(c);
    pending_space = false;
    have_name = true;
  }
  if (!have_name) out += "guest";
  return out;
}

inline std::string mag_guest_safe_api_error(std::string_view message) {
  // The upstream "message" field is untrusted. Never emit arbitrary API
  // bodies, credentials, guest names, auth-key IDs or bearer tokens to journald.
  // Allowlisted static phrases preserve actionable errors without leaks.
  if (message == "keys: description had invalid characters")
    return "keys: description had invalid characters";
  if (message == "invalid request") return "invalid request";
  if (message == "unauthorized") return "unauthorized";
  if (message == "forbidden") return "forbidden";
  if (message == "permission denied") return "permission denied";
  if (message == "rate limit exceeded") return "rate limit exceeded";
  return "unrecognized API message (redacted)";
}

inline bool mag_guest_email_valid(std::string_view e) {
  if (e.size() < 6 || e.size() > 254) return false;
  auto at=e.find('@');
  if (at==0 || at==std::string_view::npos || at!=e.rfind('@')) return false;
  auto domain=e.substr(at+1);
  if (domain.size()<4 || domain.front()=='.' || domain.back()=='.' ||
      domain.find('.')==std::string_view::npos ||
      domain.find("..")!=std::string_view::npos) return false;
  for (unsigned char ch:e) {
    if (!(std::isalnum(ch) || ch=='@' || ch=='.' ||
          ch=='_' || ch=='+' || ch=='-')) return false;
  }
  return true;
}
