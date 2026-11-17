#!/usr/bin/env node
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..', 'web');
const code = p => fs.readFileSync(path.join(root,p),'utf8');
const native = p => fs.readFileSync(path.join(__dirname,'..','native',p),'utf8');
const wait = () => new Promise(resolve => setTimeout(resolve, 0));

(async () => {
  // End-to-end routing contract: both native job providers notify from
  // terminal success/error only; delivery is the existing Push service.
  const idol = native('idol.hpp'), scene = native('scene.hpp'), server = native('magroom.cpp');
  assert(idol.includes('on_finished(message_id, true)'));
  assert(idol.includes('state == "error" && on_finished'));
  assert(idol.includes('jstr(m["model3d"], "then_video").empty()'),
    'do not notify intermediate image before chained video');
  // Once the intermediate image is silent, a rejected video enqueue MUST
  // report failure on that card; otherwise the entire chain stays silent.
  const chained = idol.split('void then_video(')[1].split('// A waiting job')[0];
  assert(chained.includes('else m3["video_error"] = error.empty()'),
    'store the video rejection on the completed image card');
  assert(chained.includes('if (id.empty() && on_finished) on_finished(message_id, false);'),
    'failed video enqueue must trigger a terminal failure notification');
  assert(server.includes('!success ? jstr(m3, "video_error")'),
    'report a video failure, not an incorrectly labelled failed image');
  assert(scene.includes('on_finished(message_id, true)'));
  assert(scene.includes('state == "error" && on_finished'));
  assert(server.includes('idol.on_finished = generation_finished'));
  assert(server.includes('scenes.on_finished = generation_finished'));
  assert(server.includes('"gen-" + id, "/?m=" + id'));
  assert(server.includes('"Generierung abgeschlossen"'));
  assert(server.includes('"Generierung fehlgeschlagen"'));

  const handlers={};
  const posted=[];
  let windows=[];
  let opened=[];
  const env={
    URL, location:{origin:'https://mag.test'},
    self:{
      location:{origin:'https://mag.test'},
      addEventListener(name,fn){handlers[name]=fn},
      clients:{
        async matchAll(){return windows},
        async openWindow(url){opened.push(url);return null}
      },
      registration:{
        async showNotification(title, opts){posted.push({title,opts})}
      }
    },
    caches:{}, fetch(){}, console, Response
  };
  vm.runInNewContext(code('sw.js'),env,{filename:'sw.js'});
  async function push(kind,visible){
    windows = visible ? [{ visibilityState:'visible',focus:async()=>{},postMessage(){} }] : [];
    let finish;
    handlers.push({
      data:{json(){return {kind,title:'Generierung abgeschlossen',body:'Video: Test',tag:'gen-test',url:'/?m=film123'}}},
      waitUntil(p){finish=p}
    });
    await finish;
  }
  await push('job',true);
  assert.equal(posted.length,0,'visible app does not need a second completion notification');
  await push('job',false);
  assert.equal(posted.length,1,'background generation notifies');
  assert.equal(posted[0].title,'Generierung abgeschlossen');
  assert.equal(posted[0].opts.data.url,'/?m=film123');
  await push('error',true);
  assert.equal(posted.length,2,'errors remain visible even when app is open');

  async function click(windowOpen) {
    const seen=[];
    windows=windowOpen ? [{
      visibilityState:'hidden',async focus(){seen.push('focus')},
      postMessage(data){seen.push(data.show)}
    }] : [];
    let task;
    handlers.notificationclick({
      notification:{data:{url:'/?m=film123'},close(){seen.push('close')}},
      waitUntil(p){task=p}
    });
    await task;
    return seen;
  }
  const seen=await click(true);
  assert.equal(seen.join(','),'close,focus,film123',
    'existing PWA focuses and receives the exact message id');
  await click(false);
  assert.equal(opened.at(-1),'/?m=film123',
    'closed PWA opens the same-origin deep link to finished card');

  // The page waits for /api/state and highlights the actual card.
  const dom=new JSDOM(code('index.html'),{
    url:'https://mag.test/?m=film123',runScripts:'outside-only',pretendToBeVisual:true
  });
  const w=dom.window,d=w.document;
  w.ResizeObserver=class{observe(){}disconnect(){}};
  w.IntersectionObserver=class{observe(){}disconnect(){}};
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  let scrolled=0, serviceMessage;
  w.HTMLElement.prototype.scrollIntoView=function(){scrolled++};
  Object.defineProperty(w.navigator,'serviceWorker',{
    configurable:true,value:{addEventListener(event,handler){
      if(event==='message')serviceMessage=handler;
    }}
  });
  let source;
  w.EventSource=class {
    static OPEN=1;
    static CLOSED=2;
    constructor(){source=this;this.readyState=1}
    close(){}
  };
  w.eval(code('app/core.js'));
  w.MAG.api=async(method,url)=>{
    assert(url.startsWith('/api/state'));
    return {messages:[{id:'film123',role:'service',from:'idol',
      text:'Video',ts:Date.now(),state:'streaming',parts:[],attachments:[],
      model3d:{kind:'video',state:'running',prompt:'Testvideo'}}],
      bots:[],you:{role:'owner',name:'Chris'},settings:{},features:{},seq:1,more:false};
  };
  w.eval(code('app/messages.js'));
  d.dispatchEvent(new w.Event('DOMContentLoaded'));
  w.MAG.connect();source.onopen();
  await wait();
  const target=d.querySelector('[data-id="film123"]');
  assert(target,'target card exists after initial snapshot');
  assert(target.classList.contains('flash'),'push deep link highlights target card');
  assert(scrolled>0,'push deep link scrolls into view');
  const before=scrolled;
  serviceMessage({data:{show:'film123'}});
  assert(scrolled>before,'worker message focuses an already open PWA');
  dom.window.close();
  console.log('generation push: native hooks, background suppression, deep links and in-page focus OK');
})().catch(e=>{console.error(e);process.exitCode=1});
