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
  let timer=null, current=sessionStorage.getItem("mag-idol-current")||null;

  // Chat-history card, not a modal or an extra status/composer row. Keep the
  // same DOM node across normal room re-renders so WebGL does not reset.
  let inlineCard=null;
  let lastJob=null;
  function attachInline(){
    if(!current)return;
    const root=document.querySelector("#conversation .conversation-inner");
    if(!root)return;
    if(!inlineCard || inlineCard.dataset.job!==current){
      inlineCard=document.createElement("article");
      inlineCard.className="idol-chat-card";
      inlineCard.dataset.job=current;
      inlineCard.style.cssText="max-width:min(100%,560px);margin:5px 0 10px 36px;padding:10px;border:1px solid var(--line,#343840);border-radius:12px;background:var(--panel,#1b1d23);color:inherit;font-size:12px";
      inlineCard.innerHTML='<div style="display:flex;gap:8px;align-items:center"><strong style="flex:1">Foto zu 3D · MI50</strong><span class="idol-chat-status" role="status">Wartet auf Status …</span></div><p class="idol-lena" style="margin:4px 0;color:var(--orange,#e9b87f)"></p><progress max="50" value="0" style="width:100%;height:7px"></progress><div style="display:flex;align-items:center;gap:8px"><small class="idol-steps" style="flex:1">0/50</small><button type="button" class="idol-abort">Abbrechen</button><button type="button" class="idol-new">Neues Foto</button></div><canvas width="600" height="420" class="idol-preview" style="display:none;width:100%;max-height:45dvh;aspect-ratio:10/7;touch-action:none;background:#101115;border-radius:10px"></canvas><div class="idol-downloads" style="display:flex;gap:12px;flex-wrap:wrap;margin-top:7px"></div>';
      inlineCard.querySelector(".idol-new").onclick=()=>file.click();
      inlineCard.querySelector(".idol-abort").onclick=()=>cancelButton.click();
    }
    if(inlineCard.parentElement!==root){
      const created=Number(lastJob?.created||0)*1000;
      const next=[...root.children].find(n=>+n.dataset.displayMs>created && created>0);
      root.insertBefore(inlineCard,next||null);
    }
    // State is painted by polling; reattachment never recursively repaints.
  }
  function paintInline(job){
    lastJob=job;attachInline();
    if(!inlineCard||inlineCard.dataset.job!==job.id)return;
    const state=job.state;
    const running=state==="queued"||state==="running";
    const step=Math.max(0,Math.min(50,+job.step||0));
    inlineCard.querySelector(".idol-chat-status").textContent=job.stage||state;
    inlineCard.querySelector(".idol-lena").textContent=job.lena_state==="pausiert für Foto zu 3D"?
      "Lena pausiert für Foto zu 3D":job.lena_state==="wird neu gestartet"?
      "Lena wird neu gestartet …":job.lena_state==="Wiederanlauf fehlgeschlagen"?
      "Lena: Wiederanlauf fehlgeschlagen":job.lena_state==="bereit"?"Lena wieder bereit":"";
    inlineCard.querySelector("progress").value=step;
    inlineCard.querySelector(".idol-steps").textContent=step+"/50 Schritte";
    inlineCard.querySelector(".idol-abort").hidden=!running;
    if(!running && job.error)inlineCard.querySelector(".idol-chat-status").textContent=job.error;
    if(state==="done"){
      const downloads=inlineCard.querySelector(".idol-downloads");
      if(!downloads.children.length)for(const [name,label] of [["splat.ply","PLY"],["mesh.glb","GLB"],["mesh.stl","STL"]]){
        const a=document.createElement("a");a.textContent=label+" herunterladen";
        a.href="/api/idol/jobs/"+job.id+"/"+name;a.download=name;
        a.style.color="var(--accent,#d9a885)";downloads.append(a);
      }
      const canvas=inlineCard.querySelector(".idol-preview");
      if(inlineCard.dataset.preview!==job.id){
        inlineCard.dataset.preview=job.id;
        preview("/api/idol/jobs/"+job.id+"/mesh.glb",canvas)
          .then(()=>{if(canvas.isConnected)canvas.style.display="block"})
          .catch(e=>{inlineCard.querySelector(".idol-chat-status").textContent="3D-Vorschau nicht verfügbar: "+e.message});
      }
    }
  }
  window.magIdolUI={render:attachInline};

  function stop(){if(timer){clearTimeout(timer);timer=null}}
  dialog.querySelector("#mag-idol-close").onclick=()=>dialog.close();
  dialog.addEventListener("close",stop);
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
    if(current){dialog.showModal();pollCurrent();}else file.click()};
  file.onchange=async()=>{
    const image=file.files?.[0];file.value="";
    if(!image)return;
    stop();current=null;sessionStorage.removeItem("mag-idol-current");
    if(!dialog.open)dialog.showModal();
    links.replaceChildren();canvas.style.display="none";progressBox.style.display="none";cancelButton.style.display="none";
    if(image.size>20*1024*1024){status.textContent="Maximal 20 MB";return}
    status.textContent="Foto übertragen …";
    try{
      const res=await fetch("/api/idol/jobs",{method:"POST",headers:{"Content-Type":image.type||"application/octet-stream","X-Mag-Idol-UI":"1"},body:image});
      const data=await res.json();
      if(!res.ok)throw Error(data.error||"Upload fehlgeschlagen");
      current=data.id;lastJob=data;inlineCard=null;sessionStorage.setItem("mag-idol-current",current);progressBox.style.display="block";cancelButton.style.display="inline-block";
      attachInline();if(dialog.open)dialog.close();
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
              canvas.style.display="block"; status.textContent="Fertig · zum Drehen ziehen, zum Zoomen scrollen";
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

  if(current)setTimeout(()=>pollCurrent(),200);

  // Minimal WebGL2 glTF 2.0/GLB renderer. Only triangle primitives are used,
  // exported by trimesh. No external CDN, framework, or dependency.
  async function preview(url,canvas){
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
      const a=json.accessors[id],b=json.bufferViews[a.bufferView];
      if(!a||!b||a.sparse)throw Error("Accessor nicht unterstützt");
      const start=(b.byteOffset||0)+(a.byteOffset||0),n=a.count;
      const width={SCALAR:1,VEC3:3,VEC4:4}[a.type];
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
    const gl=canvas.getContext("webgl2",{antialias:true,alpha:false});
    if(!gl)throw Error("WebGL2 nicht verfügbar");
    const vert="#version 300 es\nin vec3 p;in vec3 c;out vec3 vc;uniform mat4 m;void main(){vc=c;gl_Position=m*vec4(p,1.);}";
    const frag="#version 300 es\nprecision mediump float;in vec3 vc;out vec4 outColor;void main(){outColor=vec4(vc,1.);}";
    function shader(type,code){const s=gl.createShader(type);gl.shaderSource(s,code);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s}
    const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vert));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,frag));gl.linkProgram(program);
    if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error("GLB shader");
    gl.useProgram(program);
    function attr(name,values){const gpu=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,gpu);gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);const loc=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,3,gl.FLOAT,false,0,0)}
    const vao=gl.createVertexArray();gl.bindVertexArray(vao);attr("p",pos);attr("c",colors);
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
    let yaw=.5,pitch=.2,zoom=3.3,last=null;
    function render(){
      gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(.06,.065,.08,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);
      const cy=Math.cos(yaw),sy=Math.sin(yaw),cx=Math.cos(pitch),sx=Math.sin(pitch),f=1.7;
      const m=new Float32Array([
        cy*scale*f/zoom,sx*sy*scale*f/zoom,sy*cx*scale/zoom,0,
        0,cx*scale*f/zoom,-sx*scale/zoom,0,
        -sy*scale*f/zoom,sx*cy*scale*f/zoom,cy*cx*scale/zoom,0,
        -(cy*center[0]-sy*center[2])*scale*f/zoom,
        -(sx*(sy*center[0]+cy*center[2])+cx*center[1])*scale*f/zoom,
        0,1
      ]);
      gl.uniformMatrix4fv(gl.getUniformLocation(program,"m"),false,m);
      gl.bindVertexArray(vao);
      if(indices)mode();else gl.drawArrays(gl.TRIANGLES,0,count);
    }
    canvas.onpointerdown=e=>{canvas.setPointerCapture(e.pointerId);last=[e.clientX,e.clientY]};
    canvas.onpointermove=e=>{if(!last)return;yaw+=(e.clientX-last[0])*.008;pitch=Math.max(-1.5,Math.min(1.5,pitch+(e.clientY-last[1])*.008));last=[e.clientX,e.clientY];render()};
    canvas.onpointerup=()=>last=null;canvas.onpointercancel=()=>last=null;
    canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(1.2,Math.min(9,zoom*Math.exp(e.deltaY*.001)));render()};
    render();
  }
})();
