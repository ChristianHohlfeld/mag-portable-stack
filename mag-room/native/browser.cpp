#include <ctime>
#include <strings.h>
#include <fstream>
#include <unistd.h>
// Persistent Chromium controller. CDP is loopback-only; VM HTTP requires a
// token.
#include "common.hpp"
#include <openssl/evp.h>
#include <openssl/sha.h>
#include <signal.h>
using namespace mag;
static std::string b64(const unsigned char *p, size_t n) {
  std::string s(4 * ((n + 2) / 3), '\0');
  EVP_EncodeBlock((unsigned char *)s.data(), p, (int)n);
  return s;
}
class CDP {
  int fd = -1;
  std::string pending;
  int id = 0;
  void exact(char *p, size_t n) {
    while (n) {
      if (!pending.empty()) {
        size_t k = std::min(n, pending.size());
        memcpy(p, pending.data(), k);
        pending.erase(0, k);
        p += k;
        n -= k;
        continue;
      }
      ssize_t k = recv(fd, p, n, 0);
      if (k <= 0)
        throw std::runtime_error("CDP disconnected");
      p += k;
      n -= k;
    }
  }
  void frame(const std::string &s, int op = 1) {
    std::string out;
    out += (char)(0x80 | op);
    if (s.size() < 126)
      out += (char)(0x80 | s.size());
    else if (s.size() < 65536) {
      out += (char)0xfe;
      out += (char)(s.size() >> 8);
      out += (char)s.size();
    } else {
      out += (char)0xff;
      for (int i = 7; i >= 0; --i)
        out += (char)((uint64_t)s.size() >> (i * 8));
    }
    std::string mask =
        unbase64(b64((const unsigned char *)randomhex().data(), 4));
    out += mask;
    for (size_t i = 0; i < s.size(); ++i)
      out += (char)(s[i] ^ mask[i % 4]);
    if (!sendall(fd, out))
      throw std::runtime_error("CDP write failed");
  }
  std::string receive() {
    std::string s;
    for (;;) {
      unsigned char h[2];
      exact((char *)h, 2);
      uint64_t n = h[1] & 127;
      if (n == 126) {
        unsigned char b[2];
        exact((char *)b, 2);
        n = ((unsigned)b[0] << 8) | b[1];
      } else if (n == 127) {
        unsigned char b[8];
        exact((char *)b, 8);
        n = 0;
        for (auto c : b)
          n = (n << 8) | c;
      }
      if (n > 32 * 1024 * 1024)
        throw std::runtime_error("CDP message too large");
      char mask[4]{};
      if (h[1] & 128)
        exact(mask, 4);
      std::string part(n, '\0');
      exact(part.data(), n);
      if (h[1] & 128)
        for (size_t i = 0; i < n; ++i)
          part[i] ^= mask[i % 4];
      int op = h[0] & 15;
      if (op == 8)
        throw std::runtime_error("CDP closed");
      if (op == 9) {
        frame(part, 10);
        continue;
      }
      if (op == 10)
        continue;
      s += part;
      if (h[0] & 128)
        return s;
    }
  }

public:
  // Navigation log: one JSON line per top-level document response.
  void nav_event(const Val &v) {
    auto p = v.find("params");
    if (!p || str(*p, "type") != "Document") return;
    auto r = p->find("response");
    if (!r) return;
    Val e = Val::object();
    e["ts"] = (double)time(nullptr);
    e["url"] = str(*r, "url");
    e["status"] = num(*r, "status");
    if (auto h = r->find("headers"))
      for (const char *k : {"cf-ray", "x-datadome", "server", "cf-mitigated"})
        if (h->isObj())
          for (auto &kv : *h->obj)
            if (strcasecmp(kv.first.c_str(), k) == 0) e[k] = kv.second.asStr();
    // Main frame id is stable across navigations (= frame used for the
    // isolated world). iframes (e.g. reCAPTCHA) are logged marked "sub" and
    // never replace last_nav.
    const std::string fid = str(*p, "frameId");
    const bool main = world_frame.empty() || fid == world_frame;
    e["frame"] = main ? "main" : "sub";
    if (main) last_nav = e;
    std::ofstream("/home/bot/browserd/nav.log", std::ios::app) << mj::dump(e) << "\n";
  }
  Val last_nav;
  ~CDP() {
    if (fd >= 0)
      close(fd);
  }
  void connect(const std::string &url) {
    auto start = url.find("://");
    auto slash = url.find('/', start + 3);
    std::string authority = url.substr(start + 3, slash - start - 3);
    auto colon = authority.find(':');
    int port = std::stoi(authority.substr(colon + 1));
    std::string path = url.substr(slash);
    fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    timeval t{45, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &t, sizeof t);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &t, sizeof t);
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
    if (::connect(fd, (sockaddr *)&a, sizeof a))
      throw std::runtime_error("CDP connect failed");
    std::string key = b64((const unsigned char *)randomhex().data(), 16);
    sendall(fd, "GET " + path + " HTTP/1.1\r\nHost: " + authority +
                    "\r\nUpgrade: websocket\r\nConnection: "
                    "Upgrade\r\nSec-WebSocket-Key: " +
                    key + "\r\nSec-WebSocket-Version: 13\r\n\r\n");
    std::string hdr;
    char b[4096];
    size_t end;
    while ((end = hdr.find("\r\n\r\n")) == hdr.npos) {
      auto n = recv(fd, b, sizeof b, 0);
      if (n <= 0)
        throw std::runtime_error("CDP handshake failed");
      hdr.append(b, n);
    }
    unsigned char hash[SHA_DIGEST_LENGTH];
    auto material = key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    SHA1((const unsigned char *)material.data(), material.size(), hash);
    if (hdr.substr(0, end).find("101") == std::string::npos ||
        hdr.substr(0, end).find(b64(hash, sizeof hash)) == std::string::npos)
      throw std::runtime_error("Invalid CDP handshake");
    pending = hdr.substr(end + 4);
  }
  Val call(const std::string &method, Val params = Val::object()) {
    int seq = ++id;
    Val q = Val::object();
    q["id"] = seq;
    q["method"] = method;
    q["params"] = params;
    frame(mj::dump(q));
    auto end = std::chrono::steady_clock::now() + std::chrono::seconds(45);
    while (std::chrono::steady_clock::now() < end) {
      auto v = json(receive());
      if (str(v, "method") == "Network.responseReceived") nav_event(v);
      if (str(v, "method") == "Runtime.executionContextsCleared") world = -1;
      if (num(v, "id") == seq) {
        if (v.find("error"))
          throw std::runtime_error(mj::dump(*v.find("error")));
        return v.find("result") ? *v.find("result") : Val::object();
      }
    }
    throw std::runtime_error("CDP deadline");
  }
  long world = -1;
  std::string world_frame;
  long isolated_world() {
    auto ft = call("Page.getFrameTree");
    std::string fid;
    if (auto t = ft.find("frameTree"))
      if (auto f = t->find("frame"))
        if (auto i = f->find("id")) fid = i->asStr();
    Val p = Val::object();
    p["frameId"] = fid;
    p["worldName"] = "mag";
    auto w = call("Page.createIsolatedWorld", p);
    world_frame = fid;
    world = (long)num(w, "executionContextId");
    return world;
  }
  Val eval(const std::string &js) {
    Val p = Val::object();
    p["expression"] = js;
    p["returnByValue"] = true;
    p["awaitPromise"] = true;
    if (world < 0) isolated_world();
    p["contextId"] = (double)world;
    Val r;
    try {
      r = call("Runtime.evaluate", p);
    } catch (const std::exception &) {
      // context gone after a navigation: new isolated world, retry once
      p["contextId"] = (double)isolated_world();
      r = call("Runtime.evaluate", p);
    }
    if (r.find("exceptionDetails"))
      throw std::runtime_error("Browser script: " +
                               mj::dump(*r.find("exceptionDetails")));
    auto v = r.find("result");
    return v && v->find("value") ? *v->find("value") : Val();
  }
};
static std::string chromium() {
  auto override = env("MAG_CHROMIUM");
  if (!override.empty())
    return override;
  if (fs::exists("/opt/google/chrome/chrome"))
    return "/opt/google/chrome/chrome";  // real Google Chrome stable
  for (auto &d : fs::directory_iterator("/opt/magbot/browsers"))
    for (auto p :
         {d.path() / "chrome-linux/chrome", d.path() / "chrome-linux64/chrome",
          d.path() / "chrome-headless-shell-linux64/headless_shell"})
      if (fs::exists(p))
        return p.string();
  throw std::runtime_error("Golden image Chromium missing");
}
static std::string js(const std::string &s) { return mj::dump(Val(s)); }
#include "browser_udp.hpp"
int main(int argc, char **argv) {
  try {
    signal(SIGPIPE, SIG_IGN);
    if (argc == 3 && std::string(argv[1]) == "--cdp-test") {
      CDP c;
      c.connect(argv[2]);
      c.call("Page.enable");
      std::cout << mj::dump(c.eval("document.title")) << std::endl;
      return 0;
    }
    bool daemon = false;
    for (int i = 1; i < argc; ++i)
      if (std::string(argv[i]) == "--daemon")
        daemon = true;
    int port = std::stoi(env("BROWSERD_PORT", "9225"));
    auto token =
        trim(read(env("MAG_BROWSER_TOKEN_FILE", "/home/bot/browserd/token")));
    if (token.size() < 24)
      throw std::runtime_error("Browser token missing");
    int server = mag::listen(env("MAG_BROWSER_BIND", "0.0.0.0"), port);
    if (daemon) {
      pid_t p = fork();
      if (p < 0)
        throw std::runtime_error("fork failed");
      if (p > 0)
        return 0;
      setsid();
      p = fork();
      if (p > 0)
        _exit(0);
      int log = open("/home/bot/browserd/browser.log",
                     O_CREAT | O_WRONLY | O_APPEND, 0600);
      int null = open("/dev/null", O_RDONLY);
      dup2(null, 0);
      dup2(log, 1);
      dup2(log, 2);
      close(null);
      close(log);
    }
    fs::create_directories("/home/bot/browserd/profile");
    CDP c;
    bool ready = false;
    pid_t child = -1;
    // Reattach the new controller to the original hot Chromium session
    // after a server release. Never relaunch Chromium if CDP is already up:
    // doing so would lose the current page and conflict with profile locks.
    auto try_connect = [&]() -> bool {
      try {
        auto pages = json(http("127.0.0.1", 9226, "/json/list"));
        for (auto &pg : pages.asArr())
          if (str(pg, "type") == "page") {
            c.connect(str(pg, "webSocketDebuggerUrl"));
            return true;
          }
      } catch (const std::exception &e) {
        std::cerr << "CDP probe: " << e.what() << std::endl;
      }
      return false;
    };
    ready = try_connect();
    if (!ready) {
      std::string binary = chromium();
      auto sandbox = fs::path(binary).parent_path() / "chrome_sandbox";
      if (fs::exists(sandbox))
        setenv("CHROME_DEVEL_SANDBOX", sandbox.c_str(), 1);
      // Headful Chromium on a private Xvfb display when available: a normal
      // browser (regular "Chrome/<v>" user agent, no headless signals) with
      // the persistent profile below. Plain CDP launch, no --enable-automation.
      // Falls back to headless only if the VM has no Xvfb.
      bool headful = fs::exists("/usr/bin/Xvfb");
      if (headful) {
        setenv("DISPLAY", ":99", 1);
        setenv("TZ", "Europe/Berlin", 1);
        setenv("LANG", "de_DE.UTF-8", 1);
        setenv("LANGUAGE", "de_DE:de", 1);
        if (!fs::exists("/tmp/.X11-unix/X99")) {
          std::vector<std::string> x{"/usr/bin/Xvfb", ":99", "-screen", "0",
                                     "1920x1080x24", "-nolisten", "tcp"};
          std::vector<char *> xa;
          for (auto &arg : x) xa.push_back(arg.data());
          xa.push_back(nullptr);
          pid_t xp;
          if (posix_spawn(&xp, x[0].c_str(), nullptr, nullptr, xa.data(),
                          environ) != 0)
            headful = false;
          for (int i = 0; headful && i < 50 && !fs::exists("/tmp/.X11-unix/X99"); ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
          if (!fs::exists("/tmp/.X11-unix/X99")) headful = false;
        }
      }
      std::vector<std::string> a{binary,
                                 headful ? "--start-maximized" : "--headless=new",
                                 "--window-position=0,0",
                                 "--lang=de-DE",
                                 "--accept-lang=de-DE,de,en-US,en",
                                 // Google Chrome disables software WebGL by
                                 // default (M137+); the VM has no GPU.
                                 "--enable-unsafe-swiftshader",
                                 "--no-default-browser-check",
                                 "--password-store=basic",
                                 "--disable-session-crashed-bubble",
                                 "--disable-dev-shm-usage",
                                 "--remote-debugging-address=127.0.0.1",
                                 "--remote-debugging-port=9226",
                                 "--user-data-dir=/home/bot/browserd/profile",
                                 "--window-size=1920,1080",
                                 "--no-first-run",
                                 "https://google.de"};  // Startseite des Bot-Browsers
      std::vector<char *> args;
      for (auto &arg : a) args.push_back(arg.data());
      args.push_back(nullptr);
      posix_spawn_file_actions_t acts;
      posix_spawn_file_actions_init(&acts);
      int log = open("/home/bot/browserd/chromium.log",
                     O_CREAT | O_WRONLY | O_APPEND, 0600);
      posix_spawn_file_actions_adddup2(&acts, log, 1);
      posix_spawn_file_actions_adddup2(&acts, log, 2);
      int err = posix_spawn(&child, binary.c_str(), &acts, nullptr, args.data(),
                            environ);
      posix_spawn_file_actions_destroy(&acts);
      close(log);
      if (err) throw std::runtime_error("Chromium spawn failed");
      for (int i = 0; i < 100 && !ready; ++i) {
        ready = try_connect();
        if (!ready)
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      if (!ready) {
        kill(child, SIGTERM);
        throw std::runtime_error("Chromium CDP startup failed");
      }
    }
    c.call("Page.enable");
    // No Runtime.enable (detectable console-serialization side effect):
    // scripts run in an isolated world (see CDP::eval). Network events only
    // feed the navigation log (status, cf-ray, x-datadome).
    c.call("Network.enable");
    Val metrics = Val::object();
    metrics["width"] = 1440;
    metrics["height"] = 900;
    metrics["screenWidth"] = 1920;
    metrics["screenHeight"] = 1080;
    metrics["deviceScaleFactor"] = 1;
    metrics["mobile"] = false;
    c.call("Emulation.setDeviceMetricsOverride", metrics);
    auto snapshot = [&]() {
      Val state = c.eval("({url:location.href,title:document.title,"
                         "blocked:(()=>{const t=(document.title+' '+"
                         "(document.body?.innerText||'').slice(0,1000)).toLowerCase();"
                         "return (t.includes('unusual traffic')||"
                         "t.includes('verify you are human')||"
                         "t.includes('checking your browser')||"
                         "t.includes('are you a robot')||"
                         "t.includes('security check')||"
                         "t.includes('captcha'))})()})");
      state["udp_transport"] = "MGB5"; // MGB5: terminal_open repaints a running shell
      state["udp_port"] = 9227;
      try {
        const Val nav = c.call("Page.getNavigationHistory");
        const auto entries = nav.find("entries");
        const auto idx = nav.find("currentIndex");
        if (entries && entries->isArr() && idx) {
          state["can_go_back"] = idx->asNum() > 0;
          state["can_go_forward"] = idx->asNum() + 1 < (double)entries->asArr().size();
        }
      } catch (...) {
        state["can_go_back"] = false; state["can_go_forward"] = false;
      }
      return state;
    };
    char host[128]{};
    if (gethostname(host, sizeof(host) - 1) != 0)
      throw std::runtime_error("Unable to identify VM");
    std::string vm_name = host;
    if (vm_name.rfind("magbot-", 0) == 0)
      vm_name.erase(0, 7); // cloud-init uses magbot-<VM name>
    const std::string udp_key =
        trim(read(env("MAG_BROWSER_UDP_KEY_FILE", "/home/bot/browserd/udp-key")));
    if (udp_key.size() != 64)
      throw std::runtime_error("Per-VM browser UDP key missing");
    BrowserUDP udp(udp_key, vm_name);
    for (;;) {
      // CDP and UDP input run against exactly the same hot tab on one thread.
      // Do not race a second CDP connection from an auxiliary process.
      try { udp.tick(c); }
      catch (const std::exception &e) {
        std::cerr << "UDP capture: " << e.what() << std::endl;
      }
      pollfd sources[3]{{server, POLLIN, 0}, {udp.fd(), POLLIN, 0},
                        {udp.terminal_fd(), POLLIN, 0}};
      int nready = poll(sources, 3, 70);
      if (nready < 0) continue;
      if (sources[1].revents & POLLIN) {
        for (int i = 0; i < 24; ++i) {
          try { udp.receive(c); }
          catch (const std::exception &e) {
            std::cerr << "UDP control: " << e.what() << std::endl;
            break;
          }
        }
      }
      if (sources[2].revents & (POLLIN | POLLHUP | POLLERR))
        udp.terminal_output();
      if (!(sources[0].revents & POLLIN)) continue;
      int fd = accept4(server, nullptr, nullptr, SOCK_CLOEXEC);
      if (fd < 0) continue;
      timeval timeout{15, 0};
      setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
      setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
      try {
        auto r = request(fd, 1024 * 1024);
        if (r.headers["authorization"] != "Bearer " + token) {
          response(fd, 401, "{\"error\":\"Browser token required\"}");
          close(fd);
          continue;
        }
        Val body = r.body.empty() ? Val::object() : json(r.body);
        auto value = [&](const char *k) {
          auto x = str(body, k);
          return x.empty() ? param(r.query, k) : x;
        };
        Val out = Val::object();
        if (r.path == "/state")
          out = snapshot();
        else if (r.path == "/read") {
          out = c.eval("({url:location.href,title:document.title,text:document."
                       "body.innerText.slice(0,12000)})");
          Val meta = snapshot();
          out["blocked"] = meta.find("blocked") ? *meta.find("blocked") : Val(false);
          if (out.find("blocked") && out.find("blocked")->asBool())
            out["hint"] = "Website access challenge. Avoid repeated retries; "
                          "use authorized web_search or request manual verification.";
        }
        else if (r.path == "/screenshot.png" ||
                 r.path == "/screenshot.jpg") {
          Val p = Val::object();
          const bool jpeg = r.path == "/screenshot.jpg";
          p["format"] = jpeg ? "jpeg" : "png";
          if (jpeg) p["quality"] = 72;
          p["captureBeyondViewport"] = false;
          response(fd, 200,
                   unbase64(str(c.call("Page.captureScreenshot", p), "data")),
                   jpeg ? "image/jpeg" : "image/png");
          close(fd);
          continue;
        } else if (r.path == "/navigate" || r.path == "/snapshot") {
          auto url = trim(value("url"));
          // Treat a bare domain as HTTPS, exactly like the address bar.
          // Explicit schemes must still be HTTP(S); do not allow file/JS.
          if (url.find("://") == std::string::npos &&
              url.find(':') == std::string::npos &&
              url.find_first_of(" \t\r\n") == std::string::npos &&
              url.find('.') != std::string::npos)
            url = "https://" + url;
          if (url.rfind("http://", 0) != 0 && url.rfind("https://", 0) != 0)
            throw std::runtime_error("Please enter a valid HTTP(S) address");
          if (url.find_first_of(" \t\r\n") != std::string::npos)
            throw std::runtime_error("URL must not contain whitespace");
          Val p = Val::object();
          p["url"] = url;
          auto n = c.call("Page.navigate", p);
          if (n.find("errorText"))
            throw std::runtime_error(str(n, "errorText"));
          std::this_thread::sleep_for(std::chrono::milliseconds(400));
          for (int i = 0; i < 100; ++i) {
            try {
              auto state = c.eval("document.readyState");
              if (state.asStr() == "complete")
                break;
            } catch (...) {
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
          }
          out = snapshot();
          if (c.last_nav.isObj()) {
            Val nav = c.last_nav;
            nav["title"] = str(out, "title");
            out["nav"] = nav;  // status, cf-ray, x-datadome of the document
            std::ofstream("/home/bot/browserd/nav.log", std::ios::app)
                << mj::dump(nav) << "\n";
          }
          if (r.path == "/snapshot") {
            Val p = Val::object();
            p["format"] = "png";
            out["png"] = str(c.call("Page.captureScreenshot", p), "data");
            out["text"] = c.eval("document.body.innerText.slice(0,8000)");
            out["errors"] = Val::array();
            out["console"] = Val::array();
          }
        } else if (r.path == "/click") {
          std::string selector = value("selector"), text = value("text");
          if (selector.empty() && text.empty())
            throw std::runtime_error("Selector or text required");
          std::string find =
              !selector.empty()
                  ? "document.querySelector(" + js(selector) + ")"
                  : "[...document.querySelectorAll('button,a,input,label,[role="
                    "button]')].find(e=>e.innerText?.trim()===" +
                        js(text) + ")";
          auto pos = c.eval("(()=>{let e=" + find +
                            ";if(!e)throw Error('Element not "
                            "found');e.scrollIntoView({block:'center'});let "
                            "r=e.getBoundingClientRect();return "
                            "{x:r.x+r.width/2,y:r.y+r.height/2}})()");
          // Visible approach for the live view: hover first, short pause.
          {
            Val mv = pos;
            mv["type"] = "mouseMoved";
            mv["button"] = "none";
            c.call("Input.dispatchMouseEvent", mv);
            usleep(250000);
          }
          for (auto type : {"mousePressed", "mouseReleased"}) {
            Val p = pos;
            p["type"] = type;
            p["button"] = "left";
            p["clickCount"] = 1;
            c.call("Input.dispatchMouseEvent", p);
          }
          out = snapshot();
          out["click"] = pos;
        } else if (r.path == "/pointer") {
          // Browser viewport is fixed at 1440x900 CDP CSS pixels.
          auto px = body.find("x"), py = body.find("y");
          if (!px || !py || px->type != Val::NUM || py->type != Val::NUM)
            throw std::runtime_error("Numeric pointer coordinates required");
          const double x = px->asNum(), y = py->asNum();
          if (!std::isfinite(x) || !std::isfinite(y) ||
              x < 0 || x >= 1440 || y < 0 || y >= 900)
            throw std::runtime_error("Pointer outside browser viewport");
          std::string action = value("action");
          if (action.empty()) action = "tap";
          std::string button = value("button");
          if (button.empty()) button = "left";
          if (button != "left" && button != "right" && button != "middle")
            throw std::runtime_error("Unsupported pointer button");
          if (action != "tap" && action != "move" && action != "down" &&
              action != "up")
            throw std::runtime_error("Unsupported pointer action");
          bool held = body.find("held") && body.find("held")->asBool();
          auto mouse = [&](const std::string &type) {
            Val p = Val::object();
            p["type"] = type;
            p["x"] = x;
            p["y"] = y;
            p["button"] =
                type == "mouseMoved" && !held ? "none" : button;
            p["buttons"] = (type == "mousePressed" ||
                            (type == "mouseMoved" && held))
                               ? (button == "left" ? 1 : button == "right" ? 2 : 4)
                               : 0;
            p["clickCount"] = 1;
            c.call("Input.dispatchMouseEvent", p);
          };
          if (action == "tap") {
            mouse("mouseMoved");
            mouse("mousePressed");
            mouse("mouseReleased");
          } else if (action == "move") {
            mouse("mouseMoved");
          } else if (action == "down") {
            mouse("mousePressed");
          } else {
            mouse("mouseReleased");
          }
          out = snapshot();
        } else if (r.path == "/type") {
          auto selector = value("selector");
          if (!selector.empty())
            c.eval("(()=>{let e=document.querySelector(" + js(selector) +
                   ");if(!e)throw Error('Element not "
                   "found');e.focus();e.value='';})()");
          // Type in small chunks so the live view shows text appearing
          // (about 2.5 s at most, independent of the text length).
          const std::string text = value("text");
          const size_t n = text.size(),
                       step = std::max<size_t>(1, n / 100 + 1);
          for (size_t i = 0; i < n;) {
            size_t j = std::min(n, i + step);
            while (j < n && (static_cast<unsigned char>(text[j]) & 0xC0) == 0x80)
              ++j;  // never split a UTF-8 sequence
            Val p = Val::object();
            p["text"] = text.substr(i, j - i);
            c.call("Input.insertText", p);
            i = j;
            if (i < n) usleep(25000);
          }
          auto enter = body.find("enter");
          if (enter && enter->asBool()) {
            Val k = Val::object();
            k["type"] = "keyDown";
            k["key"] = "Enter";
            k["code"] = "Enter";
            k["windowsVirtualKeyCode"] = 13;
            c.call("Input.dispatchKeyEvent", k);
            k["type"] = "keyUp";
            c.call("Input.dispatchKeyEvent", k);
          }
          out = snapshot();
        } else if (r.path == "/scroll") {
          auto dy = value("dy");
          c.eval("window.scrollBy(0," +
                 std::to_string(dy.empty() ? 600 : std::stoi(dy)) + ")");
          out = snapshot();
        } else if (r.path == "/back" || r.path == "/forward" ||
                   r.path == "/reload") {
          c.eval(r.path == "/back"      ? "history.back()"
                 : r.path == "/forward" ? "history.forward()"
                                        : "location.reload()");
          out = snapshot();
        } else if (r.path == "/key") {
          auto key = value("key");
          Val p = Val::object();
          p["type"] = "keyDown";
          p["key"] = key;
          p["code"] = key;
          c.call("Input.dispatchKeyEvent", p);
          p["type"] = "keyUp";
          c.call("Input.dispatchKeyEvent", p);
          out = snapshot();
        } else {
          response(fd, 404, "{\"error\":\"Unknown browser route\"}");
          close(fd);
          continue;
        }
        response(fd, 200, mj::dump(out));
      } catch (const std::exception &e) {
        Val error = Val::object();
        error["error"] = e.what();
        response(fd, 500, mj::dump(error));
      }
      close(fd);
    }
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
