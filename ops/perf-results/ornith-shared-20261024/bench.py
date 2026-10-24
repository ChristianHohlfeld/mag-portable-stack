#!/usr/bin/env python3
# Ornith shared-model benchmark: Variant A (1 server, both nodes, --parallel 4, 28 thr, --numa distribute)
# vs Variant B (2 servers, one per NUMA node, 14 thr, --parallel 2). Transient units, test ports 8197-8199.
import json, os, random, subprocess, sys, threading, time, urllib.request
REV = open('/usr/local/lib/mag-ornith/runtime.sha').read().strip()
BIN = f'/home/chris/.local/qwen4exp/{REV}/bin/llama-server'
MODEL = '/home/chris/models/ornith-1.5-35b-a3b/Ornith-1.5-35B-A3B-Q4_K_M.gguf'
COMMON = ['--model', MODEL, '--alias', 'ornith-1.5-35b-a3b', '--host', '127.0.0.1', '--load-mode', 'mlock',
          '--jinja', '--reasoning-format', 'auto', '--reasoning', 'off', '--no-ui', '--cache-ram', '0']
WORDS = ("Haus Garten Wohnung Miete Vertrag Heizung Fenster Keller Treppe Nachbar Strom Wasser Rechnung Termin "
         "Server Modell Bot Raum Nachricht Antwort Frage Woche Jahr Stadt Strasse Licht Schluessel Tuer Dach "
         "schnell langsam gross klein neu alt gut schlecht heute morgen gestern immer nie oft manchmal").split()
def text(n, seed):
    r = random.Random(seed); return ' '.join(r.choice(WORDS) + (str(r.randint(0, 999)) if r.random() < .2 else '') for _ in range(n))
def post(port, body, timeout=3600):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/completion', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    t = time.time(); d = json.load(urllib.request.urlopen(req, timeout=timeout)); d['_wall'] = time.time() - t; return d
def wait(port):
    t = time.time()
    while time.time() - t < 1800:
        try:
            if json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=5)).get('status') == 'ok': return time.time() - t
        except Exception: pass
        time.sleep(2)
    raise SystemExit(f'port {port} not healthy')
def start(unit, port, pre, args):
    cmd = ['sudo', 'systemd-run', f'--unit={unit}', '-p', 'User=chris', '-p', 'LimitMEMLOCK=infinity', '-p', 'MemoryAccounting=yes',
           '--', '/usr/bin/numactl'] + pre + [BIN] + COMMON + ['--port', str(port)] + args
    subprocess.run(cmd, check=True)
def stop(unit): subprocess.run(['sudo', 'systemctl', 'stop', unit])
def mem(unit): return int(subprocess.run(['systemctl', 'show', unit, '-p', 'MemoryCurrent', '--value'], capture_output=True, text=True).stdout.strip() or 0)
def one(port, slot, nprompt, ngen, seed):
    d = post(port, {'prompt': f'[{seed}] ' + text(nprompt, seed) + '\nFasse zusammen:', 'n_predict': ngen, 'id_slot': slot,
                    'cache_prompt': False, 'ignore_eos': True, 'temperature': 0.7})
    tm = d['timings']; return {'slot': slot, 'port': port, 'prompt_n': tm['prompt_n'], 'pp_tps': round(tm['prompt_per_second'], 1),
                               'gen_n': tm['predicted_n'], 'tg_tps': round(tm['predicted_per_second'], 2), 'wall_s': round(d['_wall'], 1)}
def conc(jobs):
    res = [None] * len(jobs); ths = []
    t = time.time()
    for i, j in enumerate(jobs):
        th = threading.Thread(target=lambda i=i, j=j: res.__setitem__(i, one(*j))); th.start(); ths.append(th)
    for th in ths: th.join()
    return {'wall_s': round(time.time() - t, 1), 'runs': res}
def suite(name, servers):  # servers: list of (port, nslots)
    out = {'variant': name}; slots = [(p, s) for p, n in servers for s in range(n)]
    p0 = servers[0][0]
    out['warmup'] = one(p0, 0, 200, 16, 1)
    out['cold_prewarm_12k'] = one(p0, 0, 9000, 1, 2)            # ~12k tokens like the real room prompt
    out['single_4k_gen128'] = one(p0, 0, 3000, 128, 3)
    out['two_same_server'] = conc([(p0, 0, 1500, 128, 10), (p0, 1, 1500, 128, 11)])
    if len(servers) > 1:
        out['two_cross_server'] = conc([(servers[0][0], 0, 1500, 128, 12), (servers[1][0], 0, 1500, 128, 13)])
    out['four_conc'] = conc([(p, s, 1500, 128, 20 + k) for k, (p, s) in enumerate(slots)])
    out['four_prewarm_12k_conc'] = conc([(p, s, 9000, 1, 40 + k) for k, (p, s) in enumerate(slots)])
    return out
V = sys.argv[1]; R = {}
if V == 'A':
    start('ornith-bench-a', 8197, ['--interleave=all'], ['--ctx-size', '131072', '--parallel', '4', '--threads', '28', '--threads-batch', '28', '--numa', 'distribute'])
    R['load_s'] = round(wait(8197), 1); R.update(suite('A', [(8197, 4)])); R['mem_bytes'] = mem('ornith-bench-a'); stop('ornith-bench-a')
elif V == 'B':
    start('ornith-bench-b0', 8198, ['--cpunodebind=0', '--membind=0', '--physcpubind=0-13'], ['--ctx-size', '65536', '--parallel', '2', '--threads', '14', '--threads-batch', '14', '--numa', 'numactl'])
    start('ornith-bench-b1', 8199, ['--cpunodebind=1', '--membind=1', '--physcpubind=14-27'], ['--ctx-size', '65536', '--parallel', '2', '--threads', '14', '--threads-batch', '14', '--numa', 'numactl'])
    R['load_s'] = round(max(wait(8198), wait(8199)), 1); R.update(suite('B', [(8198, 2), (8199, 2)]))
    R['mem_bytes'] = mem('ornith-bench-b0') + mem('ornith-bench-b1'); stop('ornith-bench-b0'); stop('ornith-bench-b1')
print(json.dumps(R, indent=1)); open(f'/home/chris/ornith-bench/result-{V}.json', 'w').write(json.dumps(R, indent=1))
