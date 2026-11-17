// One owner for all audible media in MAG: speech, chat videos, voiceovers,
// explorer players and lock-screen controls. Playback never overlaps by default.
'use strict';
(function () {
  const MAG=window.MAG;
  let current=null, status='idle', serial=0;
  const session=navigator.mediaSession;
  const valid=new Set(['idle','loading','playing','paused','stopped']);
  const update=(state)=>{
    if(!valid.has(state))return;
    status=state;
    try {if(session)session.playbackState=state==='playing'?'playing':
      state==='paused'?'paused':'none';} catch(_){}
    document.dispatchEvent(new CustomEvent('mag-audio-state',{detail:{state:status,source:current?.name||null}}));
  };
  function release(id) {
    if(!current || (id!==undefined&&id!==current.id))return;
    current=null;update('stopped');
  }
  function stop() {
    const was=current;
    current=null;
    update('stopped');
    try{was?.stop?.()}catch(_){}
  }
  function activate(id,handlers={}) {
    if(current?.id!==id){
      const prev=current;
      current=null;
      try{prev?.stop?.()}catch(_){}
      current={id,...handlers};
    }else Object.assign(current,handlers);
    update(handlers.loading?'loading':'playing');
    try {
      if(session&&typeof MediaMetadata!=='undefined')
        session.metadata=new MediaMetadata({title:handlers.title||'MAG',artist:handlers.artist||'MAG'});
    }catch(_){}
    return id;
  }
  function pause(){
    if(!current)return;
    try{current.pause?.()}catch(_){}
    update('paused');
  }
  function resume(){
    if(!current)return;
    try{Promise.resolve(current.resume?.()).catch(()=>{});}catch(_){}
    update('playing');
  }
  if(session){
    for(const [action,fn] of [['stop',stop],['pause',pause],['play',resume]]){
      try{session.setActionHandler(action,fn);}catch(_){}
    }
  }
  // The voiceover player intentionally uses several hidden audio elements;
  // those plus its video are a single owner and must not interrupt each other.
  const ids=new WeakMap();
  function nativeId(el){return el.closest?.('.voiceover')||el;}
  function mediaHandler(node){
    if(!(node instanceof HTMLMediaElement) || node.dataset.magTts==='1')return;
    const id=nativeId(node);
    const audios=()=>id===node?[node]:[...id.querySelectorAll('audio,video')];
    const handler={
      name:'media',
      title:node.getAttribute('title')||node.closest('[data-id]')?.querySelector('.txt b')?.textContent||'MAG Media',
      stop:()=>audios().forEach(a=>{try{a.pause();a.currentTime=0;}catch(_){}}),
      pause:()=>audios().forEach(a=>{try{a.pause()}catch(_){}}),
      resume:()=>{const video=audios().find(a=>a.tagName==='VIDEO')||audios()[0];try{Promise.resolve(video?.play()).catch(()=>{});}catch(_){}}
    };
    activate(id,handler);
  }
  document.addEventListener('play',e=>mediaHandler(e.target),true);
  document.addEventListener('pause',e=>{
    const id=nativeId(e.target);
    if(!current||current.id!==id)return;
    queueMicrotask(()=>{
      if(current?.id===id){
        const els=id===e.target?[e.target]:[...id.querySelectorAll('audio,video')];
        if(els.every(el=>el.paused))update('paused');
      }
    });
  },true);
  document.addEventListener('ended',e=>{
    if(current?.id!==nativeId(e.target))return;
    const id=nativeId(e.target);
    const els=id===e.target?[e.target]:[...id.querySelectorAll('audio,video')];
    if(els.every(el=>el.ended||el.paused))release(id);
  },true);
  MAG.audio={activate,stop,pause,resume,release,
    state:()=>status,source:()=>current?.name||null,isActive:id=>current?.id===id,
    setState:(id,next)=>{if(current?.id===id)update(next)}};
})();