#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const read = p => fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const api=read('native/api.hpp'), mixer=read('native/voiceover.hpp'), ui=read('web/app/voiceover.js');
assert(api.includes('r.method == "DELETE" && p == "/api/video/voiceover"'));
assert(api.includes('void voiceover_remove('));
assert(api.includes('std::unique_lock<std::mutex> guard(voiceover_render_mu_, std::try_to_lock)'));
assert(api.includes('void voiceover_restore('));
assert(api.includes('message["voiceover_active"] = jstr(baked, "file")'));
assert(api.includes('message["voiceover_active"] = ""'));
assert(api.includes('message["voiceover_gains"] = *gains'));
assert(api.includes('voiceover::original(m, video)'), 'source video must remain intact');
assert(api.includes('input.gain = gain(lane,'));
assert(api.includes('!valid_gain("original", 0.55)'), 'numeric gains validated server side');
assert(mixer.includes('dropout_transition=0.01:weights'));
assert(mixer.includes('afade=t=in:st=0:d='));
assert(mixer.includes('afade=t=out:st='));
assert(mixer.includes('alimiter=limit=0.95'));
assert(mixer.includes('append_mix(label, std::to_string(input.gain))'));
assert(ui.indexOf('const bytes = await file.arrayBuffer()') < ui.indexOf("songFile.value = '';"),
 'Android content:// bytes read before input reset');
assert(ui.includes("audioContext.createGain()"));
assert(ui.includes('audio.playbackRate = Math.max('));
assert(ui.includes("mode: 'mix'") && ui.includes('gains: { ...gains }'));
assert(ui.includes("MAG.api('POST','/api/video/restore'"));
assert(ui.includes("MAG.api('DELETE','/api/video/voiceover'"));
console.log('voiceover contract: Android content, gain/fades, melody deletion, active bake and original restore OK');
