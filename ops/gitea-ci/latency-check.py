#!/usr/bin/env python3
"""Compare real, warmed room measurements; never sends prompts to resident slots.
Usage: latency-check.py baseline.json with-ci.json
Each file: {"8191":[{"ttft_s":2.0,"prefix_reuse":0.99},...],"8192":[...]}.
"""
import json,math,statistics,sys
before,after=(json.load(open(p)) for p in sys.argv[1:3])
for port in ('8191','8192'):
 a,b=before[port],after[port]
 if min(len(a),len(b))<5:raise SystemExit('At least five real samples per model/scenario required')
 if any(s['prefix_reuse']<.95 for s in a+b):raise SystemExit('Samples must use warmed >=95% prefixes')
 x,y=([s['ttft_s'] for s in rows] for rows in (a,b))
 p95=lambda values:sorted(values)[math.ceil(len(values)*.95)-1]
 assert statistics.median(y)<=statistics.median(x)*1.05,port+' median regression >5%'
 assert p95(y)<=p95(x)+.5,port+' p95 regression >0.5s'
 print(port,'median',statistics.median(x),'->',statistics.median(y),'p95',p95(x),'->',p95(y),'PASS')
