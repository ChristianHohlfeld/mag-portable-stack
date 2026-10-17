// libcurl TLS trust for Tailscale guest calls.
// The curl CLI reads CURL_CA_BUNDLE. libcurl does not. The audit mitm
// proxy CA is injected through that variable (and SSL_CERT_FILE) on mag-120.
#pragma once
#include <curl/curl.h>
#include <sys/stat.h>
#include <cstdlib>
#include <string>

inline std::string mag_guest_ca_file() {
  const char *keys[] = {"CURL_CA_BUNDLE", "SSL_CERT_FILE"};
  for (const char *key : keys) {
    const char *value = std::getenv(key);
    if (!value || !*value) continue;
    struct stat st{};
    if (::stat(value, &st) == 0 && S_ISREG(st.st_mode)) return value;
  }
  return {};
}

// `ca` must outlive curl_easy_perform. CURLOPT_CAINFO keeps the pointer.
inline void mag_guest_apply_ca(CURL *easy, const std::string &ca) {
  if (!easy || ca.empty()) return;
  curl_easy_setopt(easy, CURLOPT_CAINFO, ca.c_str());
}

inline std::string mag_guest_curl_error(CURLcode rc) {
  if (rc == CURLE_OK) return {};
  const char *text = curl_easy_strerror(rc);
  return text ? text : "unknown curl error";
}
