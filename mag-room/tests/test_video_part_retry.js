#!/usr/bin/env node
'use strict';
// Real jsdom timeline + native request-routing contract for single-part H3 retry.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');
const pause = () => new Promise(resolve => setTimeout(resolve, 0));
const message = (id, state, extra = {}) => ({
  id, from:'idol', role:'service', state: state === 'done' ? 'done' : 'streaming',
  ts:Date.now(), text:'', parts:[], attachments:[],
  model3d:{kind:'video',model:'h3',state,job:'j-'+id,prompt:'Original prompt '+id,seconds:5,...extra}
});
(async () => {
  const api = read('native/api.hpp');
  const idol = read('native/idol.hpp');
  assert.match(api,/p == "\/api\/video\/retry"/);
  assert.match(api,/if \(!who\.owner\(\)\).*403/);
  assert.match(api,/const std::string prompt = jstr\(m3, "prompt"\)/);
  assert.match(api,/const std::string predecessor = jstr\(m3, "continues"\)/);
  assert.match(api,/idol_\.extend\(predecessor, prompt, "chris", error, seconds\)/);
  assert.match(api,/idol_\.film\(image, prompt, "chris", error, seconds, jstr\(m3, "use", "first"\)\)/);
  assert.match(api,/Ursprungsbild nicht mehr vorhanden/);
  // Retry idempotence is server-side, not merely a disabled DOM button.
  assert.match(api,/std::mutex video_retry_mu_/);
  assert.match(api,/std::lock_guard<std::mutex> retry_guard\(video_retry_mu_\)/);
  assert.match(api,/room_\.get\(previous_id, previous\)/);
  assert.match(api,/jstr\(previous\["model3d"\], "state"\) == "queued"/);
  assert.match(api,/jstr\(previous\["model3d"\], "state"\) == "running"/);
  assert.match(api,/m\["video_retry_id"\] = id/);
  assert.match(api,/m\["video_retry_at"\] = \(double\)now_ms\(\)/);
  assert.doesNotMatch(api,/elapsed\s*<\s*60000/, 'completed retries must never be deduplicated by a time window');
  assert.doesNotMatch(api,/retry_at\s*>\s*0\s*&&/, 'no grace period for finished requests');

  assert.match(idol,/m3\["continues"\] = message_id/);

  const original=message('vid1','done',{image:'source.png',use:'ref',seconds:10});
  const continuation=message('vid2','done',{continues:'vid1',seconds:5,total_seconds:15});
  const failed=message('vid3','error',{continues:'vid1'});
  const cancelled=message('vid4','cancelled');
  const pending=message('vid5','running');
  const picture={...message('img','done'),model3d:{kind:'image',state:'done',prompt:'Image'}};
  const msgs=[original,continuation,failed,cancelled,pending,picture];
  const dom=new JSDOM(read('web/index.html'), {url:'https://mag.test/',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window,d=w.document;
  w.ResizeObserver=class{observe(){}disconnect(){}};
  w.IntersectionObserver=class{observe(){}disconnect(){}};
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  w.HTMLMediaElement.prototype.play=function(){return Promise.resolve()};
  w.HTMLMediaElement.prototype.pause=function(){};
  w.EventSource=class{static OPEN=1;constructor(){this.readyState=1;w.stream=this}close(){}};
  w.eval(read('web/app/core.js'));
  let calls=[], rejectRequest=false, resolveRequest=null;
  w.MAG.api=async(method,url,body)=>{
    if(url.startsWith('/api/state'))return {messages:msgs,bots:[],you:{role:'owner',name:'Chris'},settings:{},features:{},seq:1,more:false};
    calls.push({method,url,body});
    if(rejectRequest)throw Error('Retry abgelehnt');
    return new Promise(resolve=>{resolveRequest=resolve});
  };
  w.MAG.scrollToEnd=()=>{};
  w.MAG.toast=()=>{};
  w.eval(read('web/app/messages.js'));
  if(d.readyState==='loading')await new Promise(resolve=>d.addEventListener('DOMContentLoaded',resolve,{once:true}));
  w.MAG.connect();w.stream.onopen();
  await pause();
  const button=id=>d.querySelector('[data-id="'+id+'"] .video-retry');
  for(const id of ['vid1','vid2','vid3','vid4']){
    assert(button(id),'retry visible for '+id);
    assert.equal(button(id).getAttribute('aria-label'),'Videoteil erneut generieren');
    assert(button(id).querySelector('svg'),'minimal icon instead of text');
  }
  assert.equal(button('vid5'),null,'no retry while original job is running');
  assert.equal(button('img'),null,'no video retry on images');
  button('vid2').click();
  button('vid2').click();
  assert.equal(calls.length,1,'double-click must start only one job');
  assert.equal(calls[0].method,'POST');
  assert.equal(calls[0].url,'/api/video/retry');
  assert.equal(calls[0].body.message,'vid2','exact selected continuation');
  assert.equal(Object.keys(calls[0].body).length,1,'no altered prompt or unrelated part');
  assert.equal(button('vid2').disabled,true);
  resolveRequest({});
  await pause();
  rejectRequest=true;
  button('vid1').click();
  await pause();
  assert.equal(button('vid1').disabled,false,'re-enable on error');
  assert.equal(calls.at(-1).body.message,'vid1','independent initial part');
  dom.window.close();
  console.log('video part retry: initial + continuation, error/cancel states, owner action, no double submit OK');
})().catch(err=>{console.error(err);process.exit(1)});
