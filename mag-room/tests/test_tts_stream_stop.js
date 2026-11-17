#!/usr/bin/env node
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const listeners={};const utterances=[];let cancelled=0;
const msg={id:'stream1',from:'lena',role:'bot',state:'streaming',
  parts:[{k:'text',text:'Der erste Satz. Zweiter'}],text:''};
const state={messages:new Map([[msg.id,msg]]),bots:[]};
const btn={dataset:{speak:msg.id},attrs:{},setAttribute(k,v){this.attrs[k]=v;},
  getAttribute(k){return this.attrs[k]},set innerHTML(x){this.html=x}};
const synth={getVoices:()=>[],speak:u=>utterances.push(u),pause(){},resume(){},cancel(){cancelled++}};
function Utter(text){this.text=text;}
const window={MAG:{state,on(type,fn){(listeners[type] ||= []).push(fn)},
  tidy:t=>t,icons:{stop:'STOP-SQUARE',speak:'PLAY'},toast(){}},
  speechSynthesis:synth,SpeechSynthesisUtterance:Utter,addEventListener(){}};
const document={visibilityState:'visible',documentElement:{dataset:{},style:{setProperty(){}}},
  querySelectorAll:()=>[btn],addEventListener(){}};
const env={window,document,SpeechSynthesisUtterance:Utter,navigator:{},
  localStorage:{getItem(){return null},setItem(){}},performance:{now:()=>0},
  setTimeout(){return 1},clearTimeout(){},setInterval(){return 2},clearInterval(){},console};
vm.runInNewContext(read('web/app/speak.js'),env);
assert.equal(window.MAG.speak(msg,btn),true);
assert.equal(utterances[0].text,'Der erste Satz.');
assert.equal(btn.html,'STOP-SQUARE','stop square even during loading');
msg.parts[0].text+=' Satz. Noch offen';
listeners.delta.forEach(fn=>fn(msg,0));utterances[0].onend?.();
assert.equal(utterances[1].text,'Zweiter Satz.','next streamed delta is spoken');
window.MAG.speak(msg,btn);
assert.equal(window.MAG.speaking.busy(),false);
assert(cancelled>0,'speechSynth.cancel called immediately');
const count=utterances.length;
msg.parts[0].text+=' Nach Stop darf nichts starten.';
listeners.delta.forEach(fn=>fn(msg,0));
utterances[1].onend?.();
assert.equal(utterances.length,count,'stale streaming callbacks cannot resume audio');
assert.equal(btn.html,'PLAY');
msg.parts[0].text='Unfertiger Satz ohne Punkt';
assert.equal(window.MAG.speak(msg,btn),true,'live speech starts even before first full sentence');
assert.equal(btn.html,'STOP-SQUARE','immediate stop available during buffering');
window.MAG.speaking.stop();
assert.equal(window.MAG.speaking.busy(),false);
const core=read('web/app/core.js'),messages=read('web/app/messages.js');
assert(core.includes("const audioFocus = (MAG.audio ="),'one global audio owner');
assert(core.includes("session.setActionHandler('stop',stop)"),'lock-screen stop');
assert(messages.includes("MAG.on('delta', (m, part) =>"),'streaming action insertion');
console.log('streaming TTS: one-tap abort, no late replay, live delta feed, stop while loading OK');
