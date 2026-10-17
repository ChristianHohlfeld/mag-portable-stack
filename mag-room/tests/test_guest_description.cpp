// Real C++17 unit regressions for Tailscale auth-key descriptions and logs.
// No network, OAuth credential, Python, Node or server process required.
#include "../native/guest_key_description.hpp"
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

static void require(bool result, std::string_view reason) {
  if (!result) throw std::runtime_error(std::string(reason));
}

static bool allowed_description(std::string_view value) {
  if (value.empty() || value.size() > 50) return false;
  for (unsigned char ch : value) {
    if (!((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
          (ch >= '0' && ch <= '9') || ch == ' ' || ch == '-' || ch == '_'))
      return false;
  }
  return true;
}

int main() {
  try {
    struct Case { std::string name, expected; };
    const std::vector<Case> cases{
      {"Ali", "MAG guest Ali"},
      {"Ali (Test)", "MAG guest Ali Test"},
      {"(Ali) [DEV]!", "MAG guest Ali DEV"},
      {"Jörg Müller", "MAG guest J rg M ller"},
      {"München 🚀", "MAG guest M nchen"},
      {"😀🚀 ( )", "MAG guest guest"},
      {"", "MAG guest guest"},
      {"  \tName \n with\r controls  ", "MAG guest Name with controls"},
      {"ali-test_user 42", "MAG guest ali-test_user 42"},
      {"a/b\\c:?*&%=+", "MAG guest a b c"},
      {std::string("Ali") + '\0' + "Rob", "MAG guest Ali Rob"},
    };
    for (const auto &test : cases) {
      const auto value = mag_guest_key_description(test.name);
      require(value == test.expected, "description normalization mismatch");
      require(allowed_description(value), "description contains forbidden character");
    }
    const auto fifty = mag_guest_key_description(std::string(250, 'X'));
    require(fifty.size() == 50, "description must be at most 50 ASCII bytes");
    require(allowed_description(fifty), "truncated description has invalid bytes");
    const auto end = mag_guest_key_description(std::string(38, 'A') + " () ! B");
    require(end.size() <= 50 && end.back() != ' ', "truncation must not end with space");

    const std::string upstream = "keys: description had invalid characters";
    require(mag_guest_safe_api_error(upstream) == upstream,
            "real Tailscale description failure must be visible");
    const auto leaked = mag_guest_safe_api_error(
        "invalid value for token tskey-auth-SUPERSECRET and client_secret=abc");
    require(leaked.find("tskey-") == std::string::npos &&
            leaked.find("SUPERSECRET") == std::string::npos,
            "untrusted upstream errors must be redacted");
    require(mag_guest_safe_api_error("password\nBearer abc") ==
            "unrecognized API message (redacted)",
            "journal must not include multiline secrets");
    require(mag_guest_safe_api_error("") ==
            "unrecognized API message (redacted)", "missing error message redacted");
    require(mag_guest_email_valid("user@example.com"),"valid email");
    require(!mag_guest_email_valid("user@@example.com"),"double at");
    require(!mag_guest_email_valid("user@example..com"),"double dot");
    require(!mag_guest_email_valid("a@b.com\\nBcc:x"),"header injection");
    std::cout << "OK: ASCII key descriptions, UTF-8/punctuation/emoji, 50 bytes, safe errors\n";
  } catch (const std::exception &e) {
    std::cerr << "test_guest_description: " << e.what() << "\n";
    return 1;
  }
  return 0;
}
