#!/usr/bin/env node
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../web/app/speak.js'),'utf8');
assert(code.includes('systemVoicesCache'), 'system voice lookup must be cached');
assert(code.includes('voiceschanged'), 'cache refreshes on OS voice change');
assert(!code.includes("return fallback(pb);\n    }\n    pb.loading"), 'model load must not silently use system voice');
assert(!code.includes("const oldContext=tts.ctx; tts.ctx=null"), 'stop keeps same AudioContext for next reading');
assert(code.includes('const start = () => modelPlay(pb).catch('), 'model playback failure handled');
assert(code.includes("if (ok) return start();\n      finish(pb);"), 'model readiness failure ends visibly');
console.log('Supertonic continuous playback contracts: no implicit system fallback, stable clock and voice cache');
