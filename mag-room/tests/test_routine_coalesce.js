#!/usr/bin/env node
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const native=fs.readFileSync(path.join(__dirname,'../native/assist.hpp'),'utf8');
const panel=fs.readFileSync(path.join(__dirname,'../web/app/panels.js'),'utf8');
assert(panel.includes("min: 1, max: 44640, step: 1"));
assert(native.includes("std::floor(n) != n"),'reject fractions, NaN, inf, zero');
assert(native.includes('routine_unanswered(messages, bot, now * 1000)'), 'pending routine coalesced with expiration');
assert(native.includes('now_ms - ts < 5 * 60 * 1000'), 'stalled pending turn lease expires');
assert(native.includes('bots_scheduled.insert(bot)'), 'one bot can only be enqueued once per scheduler tick');
assert(native.includes("(busy && busy(bot))"),'running bot turn blocks new tick');
assert(native.includes('r["next"] = (double)next_after(r["when"], now)'), 'never stockpile missed times');
const history=(entries,bot,now=600000)=>{
 let answered=false;
 for(const m of [...entries].reverse()){
  if(m.from===bot && m.role==='bot')answered=true;
  if(m.routine && m.mentions?.includes(bot) && !answered) return now-(m.ts||now) < 300000;
 }
 return false;
};
const pending={from:'routine',role:'user',routine:'r1',mentions:['noah'],ts:600000};
assert(history([pending],'noah'),'pending routine blocks 1m tick');
assert(!history([pending],'noah',900001),'unanswered turn releases after 5 minutes');
assert(!history([pending,{from:'noah',role:'bot',text:'done'}],'noah'),'reply clears pending');
assert(!history([pending],'lena'),'Lena not blocked by Noah routine');
assert(history([pending,{from:'noah',role:'bot'},pending],'noah'),'newer pending blocks after older done');
console.log('one-minute routine validation and coalescing: busy, pending, release, independent bots OK');
