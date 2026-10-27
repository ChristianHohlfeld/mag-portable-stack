#include "mag_deadline.hpp"
// Proves magroom's libcurl honors CURL_CA_BUNDLE / SSL_CERT_FILE.
// A private CA is not in the system trust store. Without the helper the
// request fails before any HTTP status (the live "HTTP 0" symptom).
#include "../native/guest_curl.hpp"
#include <arpa/inet.h>
#include <curl/curl.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>

static size_t discard_body(char *, size_t s, size_t n, void *) { return s * n; }

static int curl_status(const std::string &url, const std::string &ca, long *http,
                       std::string *error) {
  CURL *easy = curl_easy_init();
  if (!easy) return 99;
  curl_easy_setopt(easy, CURLOPT_URL, url.c_str());
  curl_easy_setopt(easy, CURLOPT_WRITEFUNCTION, discard_body);
  curl_easy_setopt(easy, CURLOPT_CONNECTTIMEOUT, 3L);
  curl_easy_setopt(easy, CURLOPT_TIMEOUT, 5L);
  curl_easy_setopt(easy, CURLOPT_NOSIGNAL, 1L);
  mag_guest_apply_ca(easy, ca);
  CURLcode rc = curl_easy_perform(easy);
  curl_easy_getinfo(easy, CURLINFO_RESPONSE_CODE, http);
  *error = mag_guest_curl_error(rc);
  curl_easy_cleanup(easy);
  return rc == CURLE_OK ? 0 : 1;
}

static void require(bool ok, const std::string &why) {
  if (!ok) throw std::runtime_error(why);
}

int main() {
  mag_test_deadline(60); // mag-test-timeout: 60
  const char *dir = std::getenv("MAG_GUEST_CURL_TMP");
  std::string root = dir && *dir ? dir : "/tmp/mag-guest-curl";
  std::string script = root + ".sh";
  {
    std::ofstream out(script);
    out << "#!/bin/bash\nset -euo pipefail\nmkdir -p '" << root << "'\n"
        << "d='" << root << "'\n"
        << "openssl req -x509 -newkey rsa:2048 -nodes -keyout \"$d/ca.key\" -out \"$d/ca.pem\" "
           "-days 1 -subj /CN=mag-guest-test-ca >/dev/null 2>&1\n"
        << "openssl req -newkey rsa:2048 -nodes -keyout \"$d/srv.key\" -out \"$d/srv.csr\" "
           "-subj /CN=127.0.0.1 >/dev/null 2>&1\n"
        << "printf 'subjectAltName=IP:127.0.0.1\\n' > \"$d/ext.cnf\"\n"
        << "openssl x509 -req -in \"$d/srv.csr\" -CA \"$d/ca.pem\" -CAkey \"$d/ca.key\" "
           "-CAcreateserial -out \"$d/srv.pem\" -days 1 -extfile \"$d/ext.cnf\" >/dev/null 2>&1\n"
        << "openssl req -x509 -newkey rsa:2048 -nodes -keyout \"$d/other.key\" -out \"$d/other.pem\" "
           "-days 1 -subj /CN=other >/dev/null 2>&1\n";
  }
  if (std::system(("bash " + script).c_str()) != 0) {
    std::cerr << "openssl fixture failed\n";
    return 1;
  }
  int port = 0;
  {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    require(bind(fd, reinterpret_cast<sockaddr *>(&addr), sizeof addr) == 0, "bind");
    socklen_t len = sizeof addr;
    require(getsockname(fd, reinterpret_cast<sockaddr *>(&addr), &len) == 0, "port");
    port = ntohs(addr.sin_port);
    close(fd);
  }
  std::string sport = std::to_string(port);
  pid_t child = fork();
  require(child >= 0, "fork");
  if (child == 0) {
    int devnull = open("/dev/null", O_RDWR);
    if (devnull >= 0) {
      dup2(devnull, 1);
      dup2(devnull, 2);
    }
    execlp("openssl", "openssl", "s_server", "-accept", sport.c_str(),
           "-cert", (root + "/srv.pem").c_str(), "-key", (root + "/srv.key").c_str(),
           "-www", "-naccept", "20", static_cast<char *>(nullptr));
    _exit(127);
  }
  std::string url = "https://127.0.0.1:" + sport + "/";
  std::string ca = root + "/ca.pem";
  bool up = false;
  for (int i = 0; i < 50 && !up; ++i) {
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    unsetenv("CURL_CA_BUNDLE");
    unsetenv("SSL_CERT_FILE");
    setenv("CURL_CA_BUNDLE", ca.c_str(), 1);
    long http = 0;
    std::string error;
    if (curl_status(url, mag_guest_ca_file(), &http, &error) == 0 && http == 200) up = true;
  }
  int rc = 0;
  try {
    require(up, "private CA server did not accept the pinned bundle");
    unsetenv("CURL_CA_BUNDLE");
    unsetenv("SSL_CERT_FILE");
    long http = 0;
    std::string error;
    require(curl_status(url, mag_guest_ca_file(), &http, &error) != 0, "system trust must reject the test CA");
    require(http == 0, "TLS failure must surface as HTTP 0");
    require(!error.empty() && error.find("SSL") != std::string::npos, "curl error text missing: " + error);

    setenv("SSL_CERT_FILE", ca.c_str(), 1);
    require(mag_guest_ca_file() == ca, "SSL_CERT_FILE fallback");
    http = 0;
    error.clear();
    require(curl_status(url, mag_guest_ca_file(), &http, &error) == 0 && http == 200,
            "SSL_CERT_FILE must satisfy libcurl");

    setenv("CURL_CA_BUNDLE", ca.c_str(), 1);
    setenv("SSL_CERT_FILE", (root + "/other.pem").c_str(), 1);
    require(mag_guest_ca_file() == ca, "CURL_CA_BUNDLE wins over SSL_CERT_FILE");
    http = 0;
    require(curl_status(url, mag_guest_ca_file(), &http, &error) == 0 && http == 200,
            "CURL_CA_BUNDLE must be what libcurl trusts");
    std::cout << "OK: libcurl uses CURL_CA_BUNDLE/SSL_CERT_FILE; failures keep curl error text\n";
  } catch (const std::exception &e) {
    std::cerr << "test_guest_curl: " << e.what() << "\n";
    rc = 1;
  }
  kill(child, SIGTERM);
  waitpid(child, nullptr, 0);
  return rc;
}
