#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'web');
const read = p => fs.readFileSync(path.join(root,p),'utf8');
const dom = new JSDOM(read('index.html'), {
  url: 'https://mag.test/', pretendToBeVisual: true, runScripts: 'outside-only'
});
const w = dom.window, d = w.document;
w.matchMedia = () => ({matches:false,addEventListener(){},removeEventListener(){}});
w.eval(read('app/core.js'));
const S=w.MAG.state;
S.you={role:'owner',name:'Chris'};
S.bots=[
  {id:'lena',name:'Lena',enabled:true,busy:true,phase:'writing',model:{state:'ready'}},
  {id:'noah',name:'Noah',enabled:true,busy:true,phase:'thinking',model:{state:'ready'}}
];
const calls=[];
w.MAG.api=async (method,url,body)=>{calls.push({method,url,body});return {}};
w.MAG.toast=()=>{};
w.MAG.connect=()=>{};
const handlers={};
const on=w.MAG.on;
w.MAG.on=(type,fn)=>{handlers[type]=fn;on(type,fn)};
w.eval(read('app/panels.js'));
d.dispatchEvent(new w.Event('DOMContentLoaded'));
handlers.bots();
const top=d.querySelector('#top'),chips=d.querySelector('#chips');
const chip=id=>d.querySelector('.chip[data-bot="'+id+'"]');
const ptr=(node,type,pointerType='touch')=>{
  const event=new w.Event(type,{bubbles:true,cancelable:true});
  Object.defineProperty(event,'pointerType',{value:pointerType});
  node.dispatchEvent(event);
};
(async()=>{
  const lena=chip('lena'),noah=chip('noah');
  assert(lena&&noah,'both chips should be mounted');
  const dot=lena.querySelector('.tog');
  ptr(dot,'pointerdown');
  S.bots[0].phase='idle';S.bots[0].busy=false;
  S.bots[1].phase='tool';
  handlers.bots(); handlers.settings();
  assert.strictEqual(chip('lena'),lena,'status must not replace Lena chip between down and click');
  assert.strictEqual(chip('noah'),noah,'status must not replace Noah chip between down and click');
  dot.click();
  await Promise.resolve();
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,'/api/bot/lena');
  assert.deepEqual(calls[0].body,{enabled:false});
  assert.equal(calls.some(c=>c.url.includes('/noah')),false);

  // Mismatched click target cannot turn the initial Lena press into a Noah Stop.
  ptr(lena.querySelector('.tog'),'pointerdown');
  noah.querySelector('.chip-stop').click();
  assert.equal(calls.length,1,'switched target must not run an action');
  ptr(top,'pointerleave','mouse');

  // Hover makes the header structurally stable across frequent bot events;
  // when pointer leaves, one fresh paint catches the latest model state.
  ptr(top,'pointerenter','mouse');
  const stable=chip('lena');
  S.bots[0].enabled=false;
  handlers.bots();
  assert.strictEqual(chip('lena'),stable);
  ptr(top,'pointerleave','mouse');
  assert.notStrictEqual(chip('lena'),stable,'deferred repaint happens after hover');
  assert(chip('lena').classList.contains('paused'));
  // Keyboard clicks (no pointerdown) continue to target their own bot.
  chip('noah').querySelector('.tog').click();
  await Promise.resolve();
  assert.equal(calls[1].url,'/api/bot/noah');
  assert.deepEqual(calls[1].body,{enabled:false});
  // Stop remains scoped by bot id too.
  handlers.bots();
  chip('noah').querySelector('.chip-stop').click();
  await Promise.resolve();
  assert.equal(calls[2].url,'/api/stop');
  assert.equal(calls[2].body.bot,'noah');
  dom.window.close();
  console.log('bot chips: frozen during pointer, exact bot identities, safe wrong target, hover and keyboard OK');
})().catch(e=>{console.error(e);process.exitCode=1});
