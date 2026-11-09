/* Foto zu 3D: lazy, self-hosted GLB preview, no new chat vertical space. */
(() => {
  "use strict";
  const menu = document.getElementById("plus-menu");
  if (!menu || document.getElementById("mag-idol-button")) return;
  const button = document.createElement("button");
  button.type = "button";
  button.id = "mag-idol-button";
  button.setAttribute("role","menuitem");
  button.textContent = "▧ Foto zu 3D";
  menu.append(button);
  const file = document.createElement("input");
  file.type="file"; file.accept="image/png,image/jpeg,image/webp"; file.hidden=true;
  document.body.append(file);

  // One compact selector in the existing upload-action row (dialog + chat).
  // Never add a second row or new status/composer height on mobile.
  const QUALITY_KEY="mag-idol-quality";
  const QUALITY_VALUES=["fast","normal","high"];
  const QUALITY_LABELS=["Schnell","Normal","Hoch"];
  let qualityChoice="normal";
  try{
    const saved=localStorage.getItem(QUALITY_KEY);
    if(QUALITY_VALUES.includes(saved))qualityChoice=saved;
  }catch(_error){}
  function makeQualitySelect(){
    const label=document.createElement("label");
    label.className="idol-quality-label";
    label.style.cssText="display:inline-flex;align-items:center;gap:3px;white-space:nowrap;flex-shrink:0;font-size:11px";
    label.append("Qualität:");
    const select=document.createElement("select");
    select.className="idol-quality-select";
    select.setAttribute("aria-label","Qualität");
    select.style.cssText="max-width:74px;min-width:0;padding:3px 1px;border-radius:5px;border:1px solid #777;background:var(--panel,#1b1d23);color:inherit;font:inherit";
    for(let i=0;i<QUALITY_VALUES.length;i++){
      const option=document.createElement("option");
      option.value=QUALITY_VALUES[i];
      option.textContent=QUALITY_LABELS[i];
      select.append(option);
    }
    select.value=qualityChoice;
    select.onchange=()=>{
      qualityChoice=select.value;
      try{localStorage.setItem(QUALITY_KEY,qualityChoice)}catch(_error){}
      for(const other of document.querySelectorAll(".idol-quality-select"))
        other.value=qualityChoice;
    };
    label.append(select);
    return label;
  }

  const dialog = document.createElement("dialog");
  dialog.id="mag-idol-dialog";
  dialog.style.cssText="width:min(94vw,580px);max-height:88dvh;overflow:auto;border:1px solid #555;border-radius:14px;background:#1c1e24;color:#f1f0ed;padding:16px;box-shadow:0 20px 75px #0009";
  dialog.innerHTML='<div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><strong>Foto zu 3D · MI50</strong><button type="button" id="mag-idol-close" style="color:inherit;background:transparent;border:0;font-size:24px" aria-label="Schließen">×</button></div><p id="mag-idol-status" role="status" aria-live="polite">Bild auswählen</p><div id="mag-idol-progress" style="display:none;margin:6px 0 9px"><progress id="mag-idol-meter" max="50" value="0" style="width:100%;height:8px"></progress><small id="mag-idol-step" style="display:block;opacity:.8">0/50 Schritte</small></div><div style="display:flex;justify-content:flex-end;gap:8px;margin-bottom:8px"><button type="button" id="mag-idol-new" style="border:1px solid #555;border-radius:7px;padding:5px 9px;background:transparent;color:inherit">Neues Foto</button><button type="button" id="mag-idol-cancel" style="display:none;border:1px solid #a44;border-radius:7px;padding:5px 9px;background:transparent;color:inherit">Job abbrechen</button></div><canvas id="mag-idol-canvas" width="600" height="420" aria-label="Drehbare 3D-Vorschau" style="display:none;width:100%;max-height:45dvh;aspect-ratio:10/7;touch-action:none;background:#101115;border-radius:10px"></canvas><div id="mag-idol-files" style="display:flex;gap:12px;flex-wrap:wrap;margin-top:10px"></div><p style="font-size:12px;opacity:.72">GLB: farbiges Mesh · STL: druckbares Mesh ohne Farben · PLY: originale Gaussians. Die Mesh-Oberfläche ist eine Näherung.</p>';
  document.body.append(dialog);
  const status=dialog.querySelector("#mag-idol-status");
  const links=dialog.querySelector("#mag-idol-files");
  const canvas=dialog.querySelector("#mag-idol-canvas");
  const progressBox=dialog.querySelector("#mag-idol-progress");
  const meter=dialog.querySelector("#mag-idol-meter");
  const stepText=dialog.querySelector("#mag-idol-step");
  const cancelButton=dialog.querySelector("#mag-idol-cancel");
  const newButton=dialog.querySelector("#mag-idol-new");
  newButton.parentElement.style.gap="4px";
  newButton.parentElement.style.flexWrap="nowrap";
  newButton.style.cssText+=";padding:4px 5px;font-size:11px;white-space:nowrap";
  cancelButton.style.cssText+=";padding:4px 5px;font-size:11px;white-space:nowrap";
  newButton.parentElement.insertBefore(makeQualitySelect(),newButton);
  let timer=null, current=sessionStorage.getItem("mag-idol-current")||null;


  // Shared preview chrome. The wrapper is display:none until the model loads,
  // so the icon adds zero height to the chat history before rendering.
  let fullscreenState=null;
  function scheduleViewRender(canvas){
    requestAnimationFrame(()=>canvas._idolRender?.());
  }
  function showPreview(canvas){
    if(canvas._idolShell)canvas._idolShell.style.display="block";
    canvas.style.display="block";
    scheduleViewRender(canvas);
  }
  function closePreviewFullscreen(fromBack=false){
    const state=fullscreenState;
    if(!state)return;
    fullscreenState=null;
    if(state.native && document.fullscreenElement===state.shell){
      const leaving=document.exitFullscreen?.();
      leaving?.catch?.(()=>{});
    }
    if(state.placeholder.parentNode)state.placeholder.replaceWith(state.shell);
    else state.parent.append(state.shell);
    state.shell.style.cssText=state.shellStyle;
    state.canvas.style.cssText=state.canvasStyle;
    state.button.setAttribute("aria-label","Vollbild öffnen");
    state.button.setAttribute("aria-pressed","false");
    state.button.textContent="⛶";
    document.body.style.overflow=state.bodyOverflow;
    scheduleViewRender(state.canvas);
    // Consume only the synthetic history entry we added on entering full screen.
    if(!fromBack && state.historyArmed)history.back();
  }
  async function openPreviewFullscreen(canvas){
    if(fullscreenState){
      closePreviewFullscreen();
      return;
    }
    const shell=canvas._idolShell;
    if(!shell)return;
    const state={
      shell,canvas,parent:shell.parentNode,
      placeholder:document.createComment("idol-preview-placeholder"),
      shellStyle:shell.style.cssText,
      canvasStyle:canvas.style.cssText,
      button:shell.querySelector(".idol-fullscreen"),
      bodyOverflow:document.body.style.overflow,
      native:false,historyArmed:false
    };
    shell.before(state.placeholder);
    fullscreenState=state;
    try{
      if(typeof shell.requestFullscreen==="function"){
        await shell.requestFullscreen({navigationUI:"hide"});
        state.native=document.fullscreenElement===shell;
      }
    }catch(_error){ /* iOS Safari and denied fullscreen: fixed overlay below. */ }
    if(fullscreenState!==state)return;
    if(!state.native)document.body.append(shell);
    shell.style.cssText="position:fixed;inset:0;width:100vw;height:100dvh;z-index:2147483647;background:#101115;display:block;overflow:hidden;margin:0;padding:0;border:0;border-radius:0";
    canvas.style.cssText="display:block;width:100%;height:100%;max-height:none;aspect-ratio:auto;touch-action:none;background:#101115;border-radius:0";
    state.button.setAttribute("aria-label","Vollbild schließen");
    state.button.setAttribute("aria-pressed","true");
    state.button.textContent="×";
    document.body.style.overflow="hidden";
    history.pushState({magIdolFullscreen:true},"");
    state.historyArmed=true;
    scheduleViewRender(canvas);
  }
  document.addEventListener("keydown",event=>{
    if(fullscreenState && event.key==="Escape"){
      event.preventDefault();
      closePreviewFullscreen();
    }
  });
  window.addEventListener("popstate",()=>{
    if(fullscreenState)closePreviewFullscreen(true);
  });
  document.addEventListener("fullscreenchange",()=>{
    if(fullscreenState && fullscreenState.native &&
       document.fullscreenElement!==fullscreenState.shell){
      closePreviewFullscreen();
    }
  });
  function preparePreview(canvas){
    if(canvas._idolShell)return;
    const shell=document.createElement("div");
    shell.className="idol-viewer-shell";
    shell.style.cssText="position:relative;width:100%;display:none";
    canvas.before(shell);
    shell.append(canvas);
    const button=document.createElement("button");
    button.type="button";
    button.className="idol-fullscreen";
    button.textContent="⛶";
    button.title="Vollbild";
    button.setAttribute("aria-label","Vollbild öffnen");
    button.setAttribute("aria-pressed","false");
    button.style.cssText="position:absolute;top:6px;right:6px;z-index:2;display:grid;place-items:center;width:32px;height:32px;padding:0;border-radius:8px;border:1px solid #9998;background:#111b;color:white;font-size:23px;line-height:1;cursor:pointer";
    button.onclick=event=>{
      event.preventDefault();
      event.stopPropagation();
      if(fullscreenState?.shell===shell)closePreviewFullscreen();
      else void openPreviewFullscreen(canvas);
    };
    shell.append(button);
    canvas._idolShell=shell;
  }

  // Idol results live in the same persisted ROOM array as text messages.
  // Their ordinary .message node owns the card and native message actions.
  const cards=new Map(),jobStatuses=new Map(),jobTimers=new Map(),jobFetching=new Set();
  let lastJob=null;
  const parkRoot=document.createElement("div");
  parkRoot.style.display="none";
  parkRoot.id="idol-card-park";
  document.body.append(parkRoot);

  function park(area){
    for(const card of cards.values()){
      if(area.contains(card))parkRoot.append(card);
    }
  }
  function clear(){
    stop();
    if(fullscreenState)closePreviewFullscreen(true);
    for(const timer of jobTimers.values())clearTimeout(timer);
    jobTimers.clear();jobFetching.clear();jobStatuses.clear();
    for(const card of cards.values())card.remove();
    cards.clear();current=null;lastJob=null;
    sessionStorage.removeItem("mag-idol-current");
    links.replaceChildren();
    canvas.style.display="none";
    if(canvas._idolShell)canvas._idolShell.style.display="none";
  }
  function cardFor(jobId){
    let card=cards.get(jobId);
    if(card)return card;
    card=document.createElement("section");
    card.className="idol-chat-card";
    card.dataset.job=jobId;
    card.style.cssText="width:100%;max-width:560px;margin:5px 0;padding:8px;border:1px solid var(--line,#343840);border-radius:12px;background:var(--panel,#1b1d23);color:inherit;font-size:12px";
    card.innerHTML='<div style="display:flex;gap:6px;align-items:center"><strong style="flex:1">Foto zu 3D · MI50</strong><span class="idol-chat-status" role="status">Status laden …</span></div><p class="idol-lena" style="margin:4px 0;color:var(--orange,#e9b87f)"></p><progress max="50" value="0" style="width:100%;height:7px"></progress><div class="idol-actions" style="display:flex;align-items:center;gap:3px;min-width:0;white-space:nowrap"><small class="idol-steps" style="flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis">0/50</small><button type="button" class="idol-abort">Abbrechen</button><button type="button" class="idol-new">Neues Foto</button></div><canvas width="600" height="420" class="idol-preview" style="display:none;width:100%;max-height:45dvh;aspect-ratio:10/7;touch-action:none;background:#101115;border-radius:10px"></canvas><div class="idol-downloads" style="display:flex;gap:12px;flex-wrap:wrap;margin-top:7px"></div>';
    const actions=card.querySelector(".idol-actions");
    for(const b of actions.querySelectorAll("button")){
      b.style.cssText="font-size:11px;padding:3px 4px;white-space:nowrap;flex-shrink:0";
    }
    actions.insertBefore(makeQualitySelect(),card.querySelector(".idol-new"));
    card.querySelector(".idol-new").onclick=()=>file.click();
    card.querySelector(".idol-abort").onclick=()=>cancelJob(jobId);
    cards.set(jobId,card);
    return card;
  }
  function renderHistory(messages=[]){
    const live=new Set();
    for(const msg of messages){
      if(msg?.kind!=="idol"||!/^[a-f0-9]{32}$/.test(msg.idol_job||""))continue;
      const id=msg.idol_job,domId=String(msg.id||"");
      const node=document.querySelector('#conversation .message[data-id="'+CSS.escape(domId)+'"]');
      const content=node?.querySelector(".content");
      if(!content)continue;
      live.add(id);
      const card=cardFor(id);
      if(card.parentNode!==content)content.append(card);
      const job=jobStatuses.get(id);
      if(job)paintInline(job);
      else void pollJob(id);
    }
    for(const [id,card] of cards){
      if(live.has(id))continue;
      if(fullscreenState?.canvas===card.querySelector("canvas"))closePreviewFullscreen(true);
      card.remove();cards.delete(id);jobStatuses.delete(id);
      if(current===id){current=null;sessionStorage.removeItem("mag-idol-current");stop()}
      if(jobTimers.has(id))clearTimeout(jobTimers.get(id));
      jobTimers.delete(id);
    }
  }
  async function pollJob(jobId){
    if(jobFetching.has(jobId)||!cards.has(jobId))return;
    jobFetching.add(jobId);
    try{
      const response=await fetch("/api/idol/jobs/"+jobId,{cache:"no-store"});
      if(!response.ok){
        if(response.status===404)throw Error("3D-Dateien nicht mehr vorhanden");
        throw Error("Job HTTP "+response.status);
      }
      const job=await response.json();
      paintInline(job);
      if(job.state==="queued"||job.state==="running"){
        const t=setTimeout(()=>{jobTimers.delete(jobId);void pollJob(jobId)},2000);
        jobTimers.set(jobId,t);
      }
    }catch(error){
      const card=cards.get(jobId);
      if(card)card.querySelector(".idol-chat-status").textContent=error.message;
    }finally{jobFetching.delete(jobId)}
  }
  function paintInline(job){
    lastJob=job;
    if(!job?.id)return;
    jobStatuses.set(job.id,job);
    const card=cards.get(job.id);
    if(!card)return;
    const running=job.state==="queued"||job.state==="running";
    const step=Math.max(0,Math.min(50,+job.step||0));
    card.querySelector(".idol-chat-status").textContent=job.stage||job.state;
    card.querySelector(".idol-lena").textContent=job.lena_state==="pausiert für Foto zu 3D"?
      "Lena pausiert für Foto zu 3D":job.lena_state==="wird neu gestartet"?
      "Lena wird neu gestartet …":job.lena_state==="Wiederanlauf fehlgeschlagen"?
      "Lena: Wiederanlauf fehlgeschlagen":job.lena_state==="bereit"?"Lena wieder bereit":"";
    card.querySelector("progress").value=step;
    card.querySelector(".idol-steps").textContent=step+"/50";
    card.querySelector(".idol-abort").hidden=!running;
    if(!running&&job.error)card.querySelector(".idol-chat-status").textContent=job.error;
    if(job.state==="done"){
      const downloads=card.querySelector(".idol-downloads");
      if(!downloads.children.length){
        for(const [name,label] of [["splat.ply","PLY"],["mesh.glb","GLB"],["mesh.stl","STL"]]){
          const a=document.createElement("a");a.textContent=label+" herunterladen";
          a.href="/api/idol/jobs/"+job.id+"/"+name;a.download=name;
          a.style.color="var(--accent,#d9a885)";downloads.append(a);
        }
        if(navigator.share){
          const share=document.createElement("button");share.type="button";
          share.textContent="Teilen";share.style.cssText="border:0;background:transparent;color:var(--accent,#d9a885);padding:0";
          share.onclick=()=>navigator.share({
            title:"Foto zu 3D",
            url:new URL("/api/idol/jobs/"+job.id+"/mesh.glb",location.origin).href
          }).catch(()=>{});
          downloads.append(share);
        }
      }
      const previewCanvas=card.querySelector(".idol-preview");
      if(card.dataset.preview!==job.id){
        card.dataset.preview=job.id;
        preview("/api/idol/jobs/"+job.id+"/mesh.glb",previewCanvas)
          .then(()=>{if(previewCanvas.isConnected)showPreview(previewCanvas)})
          .catch(e=>{card.querySelector(".idol-chat-status").textContent="3D-Vorschau nicht verfügbar: "+e.message});
      }
    }
  }
  async function cancelJob(jobId){
    try{
      const res=await fetch("/api/idol/jobs/"+jobId+"/cancel",
        {method:"POST",headers:{"X-Mag-Idol-UI":"1"}});
      const data=await res.json();
      if(!res.ok)throw Error(data.error||"Abbruch fehlgeschlagen");
      await pollJob(jobId);
    }catch(error){const card=cards.get(jobId);if(card)card.querySelector(".idol-chat-status").textContent=error.message}
  }
  async function registerCurrent(jobId){
    if(!/^[a-f0-9]{32}$/.test(jobId||""))return;
    const res=await fetch("/api/room/idol",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({job_id:jobId})});
    if(!res.ok)throw Error("Raum-Eintrag fehlgeschlagen: "+res.status);
    return res.json();
  }
  window.magIdolUI={render:renderHistory,park,clear};
  // Main's initial /api/state refresh can finish before this defer script
  // loads. Mount old persisted 3D messages even with no current session job.
  requestAnimationFrame(()=>{
    if(typeof state!=="undefined" && Array.isArray(state.room))
      renderHistory(state.room);
  });

  function stop(){if(timer){clearTimeout(timer);timer=null}}  function stop(){if(timer){clearTimeout(timer);timer=null}}
  dialog.querySelector("#mag-idol-close").onclick=()=>dialog.close();
  dialog.addEventListener("close",()=>{stop();if(fullscreenState?.canvas===canvas)closePreviewFullscreen()});
  newButton.onclick=()=>file.click();
  cancelButton.onclick=async()=>{
    if(!current)return;
    cancelButton.disabled=true;status.textContent="Abbruch angefordert …";
    try{
      const res=await fetch("/api/idol/jobs/"+current+"/cancel",{method:"POST",headers:{"X-Mag-Idol-UI":"1"}});
      const reply=await res.json();
      if(!res.ok)throw Error(reply.error||"Abbruch fehlgeschlagen");
      if(reply.cancelled){cancelButton.style.display="none";status.textContent="Job abgebrochen · Lena wird wieder gestartet";
        const response=await fetch("/api/idol/jobs/"+current,{cache:"no-store"});
        if(response.ok)paintInline(await response.json());}
      else status.textContent="Job bereits abgeschlossen – Status aktualisieren";
    }catch(error){status.textContent=error.message}
    finally{cancelButton.disabled=false}
  };
  button.onclick=()=>{menu.hidden=true;document.getElementById("plus")?.setAttribute("aria-expanded","false");
    if(!dialog.open)dialog.showModal();
    if(current)pollCurrent();else status.textContent="Qualität wählen und Neues Foto antippen";};
  file.onchange=async()=>{
    const image=file.files?.[0];file.value="";
    if(!image)return;
    stop();current=null;sessionStorage.removeItem("mag-idol-current");
    if(!dialog.open)dialog.showModal();
    links.replaceChildren();canvas.style.display="none";if(canvas._idolShell)canvas._idolShell.style.display="none";progressBox.style.display="none";cancelButton.style.display="none";
    if(image.size>20*1024*1024){status.textContent="Maximal 20 MB";return}
    status.textContent="Foto übertragen …";
    try{
      const res=await fetch("/api/idol/jobs?quality="+encodeURIComponent(qualityChoice),
        {method:"POST",headers:{"Content-Type":image.type||"application/octet-stream","X-Mag-Idol-UI":"1"},body:image});
      const data=await res.json();
      if(!res.ok)throw Error(data.error||"Upload fehlgeschlagen");
      current=data.id;lastJob=data;sessionStorage.setItem("mag-idol-current",current);
      progressBox.style.display="block";cancelButton.style.display="inline-block";
      await registerCurrent(current); // native ROOM message; server persists order and identity
      if(dialog.open)dialog.close();
      await pollCurrent();
    }catch(err){status.textContent=err.message}
  };
  async function pollCurrent(){
      stop();
      const target=current;
      if(!target)return;
      async function poll(){
        if(current!==target)return;
        try{
          const response=await fetch("/api/idol/jobs/"+target,{cache:"no-store"});
          const job=await response.json();
          if(response.ok)paintInline(job);
          if(!response.ok)throw Error(job.error||"Job nicht gefunden");
          const step=Math.max(0,Math.min(50,Number(job.step)||0));
          progressBox.style.display=job.state==="queued"||job.state==="running"?"block":"none";
          meter.value=step;stepText.textContent=step+"/50 Schritte · Idol auf MI50 (ein Bild kann über 30 Minuten dauern)";
          cancelButton.style.display=job.state==="queued"||job.state==="running"?"inline-block":"none";
          status.textContent=job.state==="queued"?"In Warteschlange …":job.stage||job.state;
          if(job.state==="done"){
            links.replaceChildren();
            for(const [name,label] of [["splat.ply","PLY · Gaussians"],["mesh.glb","GLB · farbig"],["mesh.stl","STL · Druck"]]){
              const a=document.createElement("a");
              a.href="/api/idol/jobs/"+target+"/"+name;
              a.textContent=label;a.download=name;a.style.color="#d9a885";links.append(a);
            }
            status.textContent="Fertig · 3D-Vorschau laden …";
            try{
              await preview("/api/idol/jobs/"+target+"/mesh.glb",canvas);
              showPreview(canvas); status.textContent="Fertig · zum Drehen ziehen, zum Zoomen scrollen";
            }catch(err){status.textContent="Fertig · Vorschau nicht verfügbar: "+err.message}
          } else if(job.state==="error"||job.state==="cancelled"){
            status.textContent=job.error||job.stage||"Idol-Job abgebrochen";}
          if(job.state==="queued"||job.state==="running"||
             (job.lena_state==="wird neu gestartet"||
              job.lena_state==="pausiert für Foto zu 3D"))timer=setTimeout(poll,1800);
        }catch(err){
          status.textContent="Statusabfrage fehlgeschlagen: "+err.message+" · erneuter Versuch …";
          if(current===target)timer=setTimeout(poll,5000);
        }
      }
      await poll();
  }

  if(current)setTimeout(async()=>{
    try{await registerCurrent(current)}catch(_error){} // idempotent recovery of lost POST ACK
    await pollCurrent();
  },200);

  // Minimal WebGL2 glTF 2.0/GLB renderer. Only triangle primitives are used,
  // exported by trimesh. No external CDN, framework, or dependency.
  async function preview(url,canvas){
    preparePreview(canvas);
    if(canvas.dataset.previewUrl===url && canvas._idolRender)return;
    const response=await fetch(url,{cache:"no-store"});
    if(!response.ok)throw Error("GLB HTTP "+response.status);
    const buffer=await response.arrayBuffer();
    const view=new DataView(buffer);
    if(view.getUint32(0,true)!==0x46546c67)throw Error("Ungültiges GLB");
    let offset=12, json, binary;
    while(offset+8<=buffer.byteLength){
      const size=view.getUint32(offset,true),type=view.getUint32(offset+4,true);
      offset+=8;
      if(offset+size>buffer.byteLength)throw Error("GLB abgeschnitten");
      if(type===0x4e4f534a)json=JSON.parse(new TextDecoder().decode(new Uint8Array(buffer,offset,size)));
      if(type===0x004e4942)binary=new Uint8Array(buffer,offset,size);
      offset+=size;
    }
    if(!json||!binary)throw Error("Geometrie fehlt");
    const primitive=json.meshes?.[0]?.primitives?.[0];
    if(!primitive||primitive.mode && primitive.mode!==4)throw Error("Nur Dreiecksmeshes unterstützt");
    function accessor(id){
      const a=json.accessors[id];
      if(!a)throw Error("Accessor fehlt");
      const b=json.bufferViews[a.bufferView];
      if(!a||!b||a.sparse)throw Error("Accessor nicht unterstützt");
      const start=(b.byteOffset||0)+(a.byteOffset||0),n=a.count;
      const width={SCALAR:1,VEC2:2,VEC3:3,VEC4:4}[a.type];
      const ctor={5121:Uint8Array,5123:Uint16Array,5125:Uint32Array,5126:Float32Array}[a.componentType];
      if(!width||!ctor)throw Error("GLB-Format nicht unterstützt");
      const stride=b.byteStride||width*ctor.BYTES_PER_ELEMENT;
      const data=new ctor(n*width);
      const dv=new DataView(binary.buffer,binary.byteOffset,binary.byteLength);
      for(let i=0;i<n;i++)for(let j=0;j<width;j++){
        const at=start+i*stride+j*ctor.BYTES_PER_ELEMENT;
        data[i*width+j]=ctor===Float32Array?dv.getFloat32(at,true):
          ctor===Uint32Array?dv.getUint32(at,true):
          ctor===Uint16Array?dv.getUint16(at,true):dv.getUint8(at);
      }
      return {data,width,componentType:a.componentType,normalized:a.normalized};
    }
    const pos=accessor(primitive.attributes.POSITION).data;
    const colorId=primitive.attributes.COLOR_0;
    const col=colorId!==undefined?accessor(colorId):null;
    const colors=new Float32Array(pos.length);
    for(let i=0;i<pos.length/3;i++)for(let j=0;j<3;j++)
      colors[i*3+j]=col?(col.componentType===5121?col.data[i*col.width+j]/255:col.data[i*col.width+j]):0.76;
    const indices=primitive.indices===undefined?null:accessor(primitive.indices).data;
    const uv=primitive.attributes.TEXCOORD_0===undefined?null:accessor(primitive.attributes.TEXCOORD_0).data;
    // Trimesh's PBRMaterial embeds the BaseColor PNG in a GLB bufferView.
    const material=json.materials?.[primitive.material??0];
    const textureRef=material?.pbrMetallicRoughness?.baseColorTexture?.index;
    const imageRef=textureRef===undefined?null:json.textures?.[textureRef]?.source;
    const imageDef=imageRef===null?null:json.images?.[imageRef];
    let textureImage=null;
    if(uv&&imageDef){
      let blob;
      if(imageDef.bufferView!==undefined){
        const imageView=json.bufferViews?.[imageDef.bufferView];
        if(!imageView)throw Error("GLB Textur-Buffer fehlt");
        const start=imageView.byteOffset||0;
        blob=new Blob([binary.slice(start,start+imageView.byteLength)],
          {type:imageDef.mimeType||"image/png"});
      }else if(imageDef.uri?.startsWith("data:")){
        const response=await fetch(imageDef.uri);
        blob=await response.blob();
      }
      if(blob){
        textureImage=await new Promise((resolve,reject)=>{
          const source=URL.createObjectURL(blob);
          const img=new Image();
          img.onload=()=>{URL.revokeObjectURL(source);resolve(img)};
          img.onerror=()=>{URL.revokeObjectURL(source);reject(Error("GLB PNG konnte nicht geladen werden"))};
          img.src=source;
        });
      }
    }
    const gl=canvas.getContext("webgl2",{antialias:true,alpha:false});
    if(!gl)throw Error("WebGL2 nicht verfügbar");
    const vert="#version 300 es\nin vec3 p;in vec3 c;in vec2 t;out vec3 vc;out vec2 vt;uniform mat4 m;void main(){vc=c;vt=t;gl_Position=m*vec4(p,1.);}";
    const frag="#version 300 es\nprecision mediump float;in vec3 vc;in vec2 vt;uniform bool useTexture;uniform sampler2D baseColor;out vec4 outColor;void main(){outColor=vec4(useTexture?texture(baseColor,vt).rgb:vc,1.);}";
    function shader(type,code){const s=gl.createShader(type);gl.shaderSource(s,code);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s}
    const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vert));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,frag));gl.linkProgram(program);
    if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error("GLB shader");
    gl.useProgram(program);
    function attr(name,values){const gpu=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,gpu);gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);const loc=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,3,gl.FLOAT,false,0,0)}
    const vao=gl.createVertexArray();gl.bindVertexArray(vao);attr("p",pos);attr("c",colors);
    const uvBuffer=gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER,uvBuffer);
    gl.bufferData(gl.ARRAY_BUFFER,uv||new Float32Array((pos.length/3)*2),gl.STATIC_DRAW);
    const uvLoc=gl.getAttribLocation(program,"t");
    if(uvLoc>=0){gl.enableVertexAttribArray(uvLoc);gl.vertexAttribPointer(uvLoc,2,gl.FLOAT,false,0,0)}
    gl.uniform1i(gl.getUniformLocation(program,"useTexture"),Boolean(uv&&textureImage)?1:0);
    if(uv&&textureImage){
      const tex=gl.createTexture();
      gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,tex);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,textureImage);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.uniform1i(gl.getUniformLocation(program,"baseColor"),0);
    }
    let count=pos.length/3,mode=gl.drawArrays;
    if(indices){
      const gpu=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,gpu);
      const indexData=indices instanceof Uint8Array?new Uint16Array(indices):indices;
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,indexData,gl.STATIC_DRAW);
      count=indexData.length;mode=()=>gl.drawElements(gl.TRIANGLES,count,indexData instanceof Uint32Array?gl.UNSIGNED_INT:gl.UNSIGNED_SHORT,0);
    }
    let min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
    for(let i=0;i<pos.length;i+=3)for(let k=0;k<3;k++){min[k]=Math.min(min[k],pos[i+k]);max[k]=Math.max(max[k],pos[i+k])}
    const center=min.map((v,i)=>(v+max[i])/2),scale=2/Math.max(...max.map((v,i)=>v-min[i]),1e-5);
    // GLB arrives Y-up. Do not rotate it again in the viewer.
    let yaw=.5,pitch=.2,zoom=3.3,panX=0,panY=0;
    const pointers=new Map();
    let lastTap=0,lastTapPos=null,previous=null;
    const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
    function render(){
      const rect=canvas.getBoundingClientRect();
      const dpr=Math.min(2,window.devicePixelRatio||1);
      const w=Math.min(2400,Math.max(1,Math.round((rect.width||600)*dpr)));
      const h=Math.min(2400,Math.max(1,Math.round((rect.height||420)*dpr)));
      if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}
      gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(.06,.065,.08,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);
      const cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(pitch),sx=Math.sin(pitch),f=1.7;
      const fx=f/(canvas.width/canvas.height);
      const m=new Float32Array([
        cy*scale*fx/zoom,sx*sy*scale*f/zoom,sy*cx*scale/zoom,0,
        0,cx*scale*f/zoom,-sx*scale/zoom,0,
        -sy*scale*fx/zoom,sx*cy*scale*f/zoom,cy*cx*scale/zoom,0,
        -(cy*center[0]-sy*center[2])*scale*fx/zoom+panX,
        -(sx*(sy*center[0]+cy*center[2])+cx*center[1])*scale*f/zoom+panY,
        0,1
      ]);
      gl.uniformMatrix4fv(gl.getUniformLocation(program,"m"),false,m);
      gl.bindVertexArray(vao);
      if(indices)mode();else gl.drawArrays(gl.TRIANGLES,0,count);
    }
    function resetView(){yaw=.5;pitch=.2;zoom=3.3;panX=0;panY=0;render()}
    function gesture(){
      const v=[...pointers.values()];
      if(v.length===1)return {count:1,x:v[0].x,y:v[0].y};
      if(v.length>=2){
        const a=v[0],b=v[1];
        return {count:2,x:(a.x+b.x)/2,y:(a.y+b.y)/2,
          distance:Math.max(1,Math.hypot(a.x-b.x,a.y-b.y))};
      }
      return null;
    }
    canvas.onpointerdown=e=>{
      if(e.pointerType==="mouse"&&e.button!==0)return;
      e.preventDefault();
      try{canvas.setPointerCapture(e.pointerId)}catch(_error){}
      pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
      previous=gesture();
    };
    canvas.onpointermove=e=>{
      if(!pointers.has(e.pointerId))return;
      e.preventDefault();
      const before=previous;
      pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
      const now=gesture();
      if(before?.count===1&&now?.count===1){
        yaw+=(now.x-before.x)*.008;
        pitch=clamp(pitch+(now.y-before.y)*.008,-1.5,1.5);
      }else if(before?.count===2&&now?.count===2){
        zoom=clamp(zoom*before.distance/now.distance,.6,15);
        const rect=canvas.getBoundingClientRect();
        panX=clamp(panX+2*(now.x-before.x)/Math.max(1,rect.width),-2,2);
        panY=clamp(panY-2*(now.y-before.y)/Math.max(1,rect.height),-2,2);
      }
      previous=now;render();
    };
    function endPointer(e){
      if(!pointers.has(e.pointerId))return;
      const single=pointers.size===1;
      pointers.delete(e.pointerId);
      previous=gesture();
      if(single && e.type==="pointerup" && e.pointerType!=="mouse"){
        const now=Date.now(),point=[e.clientX,e.clientY];
        if(now-lastTap<325 && lastTapPos &&
           Math.hypot(point[0]-lastTapPos[0],point[1]-lastTapPos[1])<25){
          resetView();lastTap=0;lastTapPos=null;
        }else{lastTap=now;lastTapPos=point}
      }
    }
    canvas.onpointerup=endPointer;
    canvas.onpointercancel=endPointer;
    canvas.onlostpointercapture=endPointer;
    canvas.ondblclick=e=>{e.preventDefault();resetView()};
    canvas.onwheel=e=>{e.preventDefault();zoom=clamp(zoom*Math.exp(e.deltaY*.001),.6,15);render()};
    canvas._idolResizeObserver?.disconnect();
    if(typeof ResizeObserver!=="undefined"){
      canvas._idolResizeObserver=new ResizeObserver(()=>render());
      canvas._idolResizeObserver.observe(canvas);
    }
    canvas._idolRender=render;
    canvas.dataset.previewUrl=url;
    render();
  }
})();
