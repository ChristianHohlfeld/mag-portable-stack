#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const read = name => fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const wait = () => new Promise(resolve => setTimeout(resolve,0));
(async()=>{
 const native=read('native/api.hpp');
 assert(native.includes('p == "/api/media"'));
 assert(native.includes('void media_messages('));
 assert(native.includes('m.find("model3d") != nullptr'), 'queued/running media not filtered out');
 const w=new JSDOM(read('web/index.html'),{url:'https://mag.test/',pretendToBeVisual:true,runScripts:'outside-only'}).window;
 const d=w.document;
 w.ResizeObserver=class{observe(){}disconnect(){}};
 w.IntersectionObserver=class{observe(){}disconnect(){}};
 w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 let resolveMedia, calls=0, stream, olderUrl='';
 const state={messages:[{id:'txt1',role:'bot',from:'lena',text:'hello',ts:Date.now(),state:'done',parts:[],attachments:[]}],you:{role:'owner',name:'Chris'},bots:[],settings:{},features:{},more:true,seq:1};
 w.EventSource=class{static OPEN=1;constructor(){this.readyState=1;stream=this;}close(){}};
 w.eval(read('web/app/core.js'));
 w.MAG.api=async(method,url)=>{
   if(url.startsWith('/api/state')) return state;
   if(url.startsWith('/api/media')){calls++;return new Promise(resolve=>{resolveMedia=resolve})}
   if(url.startsWith('/api/messages')) { olderUrl=url; return {messages:[],more:false}; }
   throw Error('bad route '+url);
 };
 w.eval(read('web/app/messages.js'));
 if(d.readyState==='loading')await new Promise(resolve=>d.addEventListener('DOMContentLoaded',resolve,{once:true}));
 w.MAG.connect();stream.onopen();await wait();
 d.querySelector('#media-filter').click();await wait();
 assert.equal(calls,1,'one independent media load when tab opens');
 assert.equal(d.querySelector('#empty').textContent,'Medien werden geladen …');
 resolveMedia({messages:[
  {id:'oldvideo',role:'service',from:'idol',state:'done',ts:Date.now(),parts:[],attachments:[],model3d:{kind:'video',model:'h3',state:'done',prompt:'old'}},
  {id:'active',role:'service',from:'idol',state:'streaming',ts:Date.now(),parts:[],attachments:[],model3d:{kind:'image',state:'running',progress:.2,prompt:'running'}}
 ],more:false});
 await wait();await wait();
 assert(d.querySelector('[data-id="oldvideo"]'),'historical video hydrated');
 assert(d.querySelector('[data-id="active"]'),'running job hydrated');
 assert.equal(d.querySelector('#empty').hidden,true);
 assert.equal(w.MAG.state.messages.has('txt1'),true,'ordinary chat retained');
 await w.MAG.loadOlder();
 assert(olderUrl.includes('before=txt1'),'media hydration must never advance ordinary chat cursor');
 w.close();
 console.log('media initial snapshot: loading, historical video, active generation, retained chat OK');
})().catch(e=>{console.error(e);process.exit(1)});
