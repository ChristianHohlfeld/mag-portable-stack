#!/usr/bin/env python3
"""One-shot patch applied by CI, then removed. Not a production service."""
import sys
from pathlib import Path


def replace_once(path, old, new):
    file = Path(path)
    text = file.read_text()
    if old not in text:
        if new in text:
            print(f"already applied: {path}")
            return
        sys.exit(f"missing hunk in {path}")
    file.write_text(text.replace(old, new, 1))
    print(f"patched {path}")


def main():
    target_old = "--target magroom mag-audit-gateway mag-browser magbot-native"
    target_new = target_old + " mag-bots-publish"
    replace_once("ops/update.sh", target_old, target_new)
    replace_once("ops/bootstrap.sh", target_old, target_new)
    replace_once(
        "ops/deploy.sh",
        "for binary in magroom mag-browser magbot-native; do",
        "for binary in magroom mag-browser magbot-native mag-bots-publish; do",
    )
    replace_once(
        "ops/deploy.sh",
        '"$SOURCE/build/magbot-native" "$SOURCE/build/mag-browser-webrtc"',
        '"$SOURCE/build/magbot-native" "$SOURCE/build/mag-bots-publish" "$SOURCE/build/mag-browser-webrtc"',
    )

    cpp = Path("mag-room/native/magroom.cpp")
    text = cpp.read_text()
    old = (
        '    "damit surfen. Deine eigene Tailnet-Website ist "\n'
        '    "https://{vm}.bot.stardenburg.de (nur im Tailnet, nicht oeffentlich). "\n'
        '    "Dateien in /home/bot/www veroeffentlichst du mit bash /home/bot/publish-site. "\n'
        '    "Lies /home/bot/SITE.md. Stuetze Aussagen ueber deine VM auf echte Werkzeugausgaben "\n'
    )
    new = '    "damit surfen. Stuetze Aussagen ueber deine VM auf echte Werkzeugausgaben "\n'
    if old not in text:
        sys.exit("vm rules hunk missing")
    text = text.replace(old, new, 1)
    sig = (
        "static std::string build_context_tail(int bot_idx, bool autotalk,\n"
        "                                      const std::string &other) {\n"
    )
    fn = (
        "static bool bot_site_ready(int bot_idx) {\n"
        '  std::string vm = bot_field(bot_idx, "vm", "keine");\n'
        '  if (vm == "keine" || vm.empty())\n'
        "    return false;\n"
        '  if (const char *over = std::getenv("MAG_BOTS_READY"))\n'
        '    return ::strcmp(over, "1") == 0;\n'
        '  return ::access("/var/lib/mag-bots/ready", R_OK) == 0;\n'
        "}\n"
        "static std::string bot_site_rules(int bot_idx) {\n"
        "  // Volatile on purpose: the cached VM_RULES prefix must not claim a site\n"
        "  // that install.sh has not marked ready.\n"
        "  std::string text =\n"
        '      "\\nDeine eigene Tailnet-Website ist https://{vm}.bot.stardenburg.de "\n'
        '      "(nur im Tailnet, nicht oeffentlich). "\n'
        '      "Dateien in /home/bot/www veroeffentlichst du mit bash /home/bot/publish-site. "\n'
        '      "Lies /home/bot/SITE.md.";\n'
        '  replaceAll(text, "{vm}", bot_field(bot_idx, "vm", ""));\n'
        "  return text;\n"
        "}\n"
    )
    if sig not in text:
        sys.exit("context tail signature missing")
    text = text.replace(sig, fn + sig, 1)
    clock = '  out += "\\nZeit: " + server_clock_minute();\n'
    clock_new = clock + "  if (bot_site_ready(bot_idx))\n    out += bot_site_rules(bot_idx);\n"
    if text.count(clock) != 1:
        sys.exit("clock line not unique")
    text = text.replace(clock, clock_new, 1)
    if "https://{vm}.bot.stardenburg.de" not in text:
        sys.exit("site sentence missing after patch")
    cpp.write_text(text)
    print("patched magroom.cpp")


if __name__ == "__main__":
    main()
