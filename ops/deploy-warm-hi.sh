#!/usr/bin/env bash
# After mag-120 is up: both NUMA slots must already hold a prefix that a
# first hi can extend in under 5s. The slots document is large; pass it on
# stdin, never as an argument (Argument list too long). ik-llama on :8191
# reports only state=0 (no n_past / n_prompt_tokens / n_cached_tokens); a
# saved prefix file, or one timed token when the room hi is not the
# measurement, counts there. A slot still prefilling is given a short settle,
# then cancel+restore, before that measurement. This check does not restart
# mag-llm-ds-node@0. A direct completion is sent only when cache counts are
# absent and MAG_WARM_HI_REQUIRE_ROOM is off, so the room hi keeps the KV.
set -euo pipefail
budget=${MAG_WARM_HI_BUDGET_S:-5}
limit_ms=${MAG_WARM_HI_ANSWER_MS:-5000}
if [[ -n "${MAG_WARM_HI_PORT:-}" ]]; then
  ports=$MAG_WARM_HI_PORT
else
  ports=${MAG_WARM_HI_PORTS:-8191,8192}
fi
IFS=',' read -r -a port_list <<<"$ports"
fail() { echo "FAIL: $*" >&2; echo "Not restarting mag-llm." >&2; exit 1; }

check_port() {
  local port=$1
  local start end ms code slots
  start=$(date +%s%3N)
  code=$(curl -sS -o /dev/null -w '%{http_code}' --retry 0 --max-time "$budget" \
    "http://127.0.0.1:${port}/health" || true)
  end=$(date +%s%3N)
  ms=$((end - start))
  if [[ "$code" != 200 || "$ms" -ge "$limit_ms" ]]; then
    fail "warm hi gate: :${port}/health HTTP '${code:-none}' in ${ms}ms (need <${limit_ms}ms)"
  fi
  slots=$(curl -sS --retry 0 --max-time "$budget" "http://127.0.0.1:${port}/slots" || true)
  if [[ -z "$slots" ]]; then
    fail "warm hi gate: :${port}/slots silent in ${budget}s"
  fi
  # Classify on stdin (the slots body is too large for argv). Exit text is
  # "ok <detail>" or "fail <detail>". A busy slot is polled, then cancelled
  # and restored from the saved prefix, before the same rules run again.
  line=$(printf '%s' "$slots" | MAG_WARM_HI_PORT_ONE="$port" python3 -c "$(cat <<'PY'
import json, os, re, sys, time, urllib.request
raw = sys.stdin.read()
port = os.environ.get("MAG_WARM_HI_PORT_ONE", "")
budget = float(os.environ.get("MAG_WARM_HI_BUDGET_S", "5"))
settle = float(os.environ.get("MAG_WARM_HI_SETTLE_S", "2"))
limit_ms = int(os.environ.get("MAG_WARM_HI_ANSWER_MS", "5000"))
require_room = os.environ.get("MAG_WARM_HI_REQUIRE_ROOM", "0") == "1"
state_dir = os.environ.get("MAG_ROOM_STATE", "/var/lib/magbot/room")
slots_root = os.environ.get("MAG_WARM_HI_SLOTS", "/var/lib/mag-llm/slots")
CONTENT = re.compile(r'"content"\s*:\s*"((?:\\.|[^"\\])*)"')

def parse(body):
    try:
        slots = json.loads(body)
    except Exception:
        return None
    if isinstance(slots, dict):
        slots = slots.get("slots") or slots.get("data") or []
    if not isinstance(slots, list) or not slots:
        return None
    return slots

def cache_count(slot):
    # n_decoded, a prompt string or state>0 is not the room prefix
    # (live: n_past=1 / 12 tokens still printed "prefix resident").
    present, count = False, 0
    for key in ("n_past", "n_prompt_tokens", "n_cached_tokens"):
        n = slot.get(key)
        if isinstance(n, (int, float)):
            present = True
            if n > count:
                count = n
    return present, count

def busy(slots):
    return any(bool(slot.get("is_processing")) for slot in slots)

def fetch():
    with urllib.request.urlopen("http://127.0.0.1:%s/slots" % port, timeout=budget) as r:
        return r.read().decode("utf-8", "replace")

def prefix_names():
    path = os.path.join(state_dir, "prompt-prefix.json")
    try:
        meta = json.load(open(path))
    except Exception:
        return []
    if not isinstance(meta, dict):
        return []
    names, prefix = [], str(port) + "/"
    for key, val in meta.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        if not isinstance(val, str) or not val:
            continue
        name = key.split("/", 1)[1]
        if name and "/" not in name and name not in (".", "..") and name not in names:
            names.append(name)
    return names

def file_ready(directory, name):
    candidate = os.path.join(directory, name)
    return os.path.isfile(candidate) and os.path.getsize(candidate) > 0

def prefix_file():
    names = prefix_names()
    if not names:
        return None
    preferred = []
    if port == "8191":
        preferred.append(os.path.join(slots_root, "ds-node0"))
    if port == "8192":
        preferred.append(os.path.join(slots_root, "qwen"))
    preferred.append(slots_root)
    for name in names:
        for directory in preferred:
            if file_ready(directory, name):
                return name
        try:
            entries = list(os.scandir(slots_root))
        except FileNotFoundError:
            entries = []
        for entry in entries:
            if entry.is_dir(follow_symlinks=False) and file_ready(entry.path, name):
                return name
    return None

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:%s%s" % (port, path), data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=budget) as r:
            r.read()
    except Exception:
        pass

def probe():
    # One cached token. Only when the slot omits n_* and the room hi is not
    # already the measurement (that hi must keep the restored KV).
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1, "cache_prompt": True, "stream": True, "id_slot": 0,
    }
    req = urllib.request.Request(
        "http://127.0.0.1:%s/v1/chat/completions" % port,
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=limit_ms / 1000.0 + 1) as resp:
            buf = ""
            while time.monotonic() - started < limit_ms / 1000.0:
                chunk = resp.read(512)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                if any(part != "" for part in CONTENT.findall(buf)):
                    return int((time.monotonic() - started) * 1000)
    except Exception:
        return None
    return None

def classify(slots):
    if not slots:
        return "fail"
    if busy(slots):
        return "fail"
    missing = False
    for slot in slots:
        present, count = cache_count(slot)
        if present and count < 2048:
            return "fail"
        if not present:
            missing = True
    if not missing:
        return "prefix resident"
    # ik-llama: state only. A non-empty prefix file is the saved KV.
    # Without the room hi, also time one token. A bare state=0 is cold.
    saved = prefix_file()
    if saved and require_room:
        return "prefix file"
    if saved:
        ms = probe()
        if ms is None or ms >= limit_ms:
            return "fail"
        return "first token %dms" % ms
    if require_room:
        return "prefix unchecked"
    return "fail"

slots = parse(raw)
if slots and busy(slots):
    deadline = time.monotonic() + settle
    while slots and busy(slots) and time.monotonic() < deadline:
        time.sleep(0.1)
        try:
            slots = parse(fetch()) or slots
        except Exception:
            break
    if slots and busy(slots):
        post("/slots/0?action=cancel", {})
        name = prefix_file()
        if name:
            post("/slots/0?action=restore", {"filename": name})
        time.sleep(0.05)
        try:
            slots = parse(fetch()) or slots
        except Exception:
            pass
detail = classify(slots)
if detail == "fail":
    print("fail slot is busy or has no cached prefix")
else:
    print("ok " + detail)
PY
)" ) || line='fail slot is busy or has no cached prefix'
  case "$line" in
    ok\ *) echo "warm hi gate: :${port}/health ${ms}ms, ${line#ok }" ;;
    fail\ *) fail "warm hi gate: :${port} ${line#fail } (health ${ms}ms)" ;;
    *) fail "warm hi gate: :${port} slot is busy or has no cached prefix (health ${ms}ms)" ;;
  esac
}

for port in "${port_list[@]}"; do
  port=${port// /}
  [[ -n "$port" ]] || continue
  check_port "$port"
done

# Health on :8191/:8192 only proves the slots are idle. The room hi is what
# the deploy measured at 14–39s after a green gate. Time the first visible
# assistant token, not the completed turn.
if [[ "${MAG_WARM_HI_REQUIRE_ROOM:-0}" == 1 ]]; then
  room=${MAG_WARM_HI_ROOM:-http://127.0.0.1:8766}
  prewarm_s=${MAG_WARM_HI_PREWARM_S:-60}
  python3 - "$room" "$limit_ms" "$prewarm_s" <<'PY'
import json, sys, time, urllib.request
room, limit_ms, prewarm_s = sys.argv[1].rstrip("/"), int(sys.argv[2]), float(sys.argv[3])

def get(path, timeout):
    with urllib.request.urlopen(room + path, timeout=timeout) as r:
        return r.status, r.read()

deadline = time.monotonic() + prewarm_s
ready = False
while time.monotonic() < deadline:
    try:
        status, body = get("/api/state", 2)
    except Exception:
        time.sleep(0.2)
        continue
    if status != 200:
        time.sleep(0.2)
        continue
    state = json.loads(body)
    if state.get("prefix_ready", True):
        ready = True
        break
    time.sleep(0.2)
if not ready:
    sys.stderr.write("FAIL: warm hi gate: prefix not ready\nNot restarting mag-llm.\n")
    sys.exit(1)
status, html = get("/", 3)
token = ""
text = html.decode("utf-8", "replace")
key = 'name="mag-token" content="'
at = text.find(key)
if at >= 0:
    token = text[at + len(key):].split('"', 1)[0]
if not token:
    sys.stderr.write("FAIL: warm hi gate: no room token\nNot restarting mag-llm.\n")
    sys.exit(1)
state = json.loads(get("/api/state", 3)[1])
bots = [a.get("id") for a in state.get("agents") or [] if a.get("id")]
if len(bots) < 1:
    sys.stderr.write("FAIL: warm hi gate: no bots\nNot restarting mag-llm.\n")
    sys.exit(1)
before = {m.get("id") for m in state.get("room") or []}
payload = json.dumps({"text": "hi", "bots": bots}).encode()
req = urllib.request.Request(room + "/api/message", data=payload, method="POST",
    headers={"Content-Type": "application/json", "X-Mag-Token": token})
started = time.monotonic()
with urllib.request.urlopen(req, timeout=5) as r:
    if r.status not in (200, 202):
        sys.stderr.write("FAIL: warm hi gate: message HTTP %s\nNot restarting mag-llm.\n" % r.status)
        sys.exit(1)
need = set(bots)
seen = {}
while time.monotonic() - started < limit_ms / 1000:
    state = json.loads(get("/api/state", 2)[1])
    for m in state.get("room") or []:
        if m.get("id") in before or m.get("role") != "assistant":
            continue
        bot = m.get("bot")
        if bot in need and (m.get("content") or "").strip() and bot not in seen:
            seen[bot] = int((time.monotonic() - started) * 1000)
    if need <= set(seen):
        break
    time.sleep(0.05)
missing = [b for b in bots if b not in seen]
if missing:
    sys.stderr.write("FAIL: warm hi gate: no visible answer for %s within %sms\nNot restarting mag-llm.\n" % (",".join(missing), limit_ms))
    sys.exit(1)
slow = [b for b, ms in seen.items() if ms >= limit_ms]
if slow:
    sys.stderr.write("FAIL: warm hi gate: %s\nNot restarting mag-llm.\n" % " ".join("%s %sms" % (b, seen[b]) for b in slow))
    sys.exit(1)
print("warm hi gate: visible " + " ".join("%s %sms" % (b, seen[b]) for b in bots))
PY
fi
echo "warm hi gate: ${#port_list[@]} bot(s) ready for a first hi under $((limit_ms / 1000))s"
