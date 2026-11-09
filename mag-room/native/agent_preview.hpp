#pragma once
// MAG agent preview: persistent {bot,path} -> last successful file contents.
// All paths and data remain private host-side. Only the authenticated owner
// can use the corresponding routes. No extra server, socket or secrets.
#include "json.hpp"
#include <openssl/sha.h>
#include <atomic>
#include <cerrno>
#include <cctype>
#include <fcntl.h>
#include <unistd.h>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <mutex>
#include <regex>
#include <string>
#include <utility>
#include <vector>

namespace mag_preview {
static std::mutex MUTEX;
inline std::string read_text(const std::string &path, bool &ok) {
  std::ifstream in(path,std::ios::binary);ok=(bool)in;
  if (!ok) return {};
  std::string value((std::istreambuf_iterator<char>(in)),std::istreambuf_iterator<char>());
  ok=!in.bad();return value;
}
inline bool write_atomic(const std::string &path,const std::string &value) {
  static std::atomic<unsigned> serial{0};
  const std::string tmp=path+".tmp."+std::to_string((long)getpid())+"."+std::to_string(serial++);
  int fd=::open(tmp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
  if(fd<0)return false;
  size_t written=0;
  while(written<value.size()) {
    ssize_t n=::write(fd,value.data()+written,value.size()-written);
    if(n<0&&errno==EINTR)continue;
    if(n<=0)break;
    written+=(size_t)n;
  }
  bool ok=written==value.size() && ::fsync(fd)==0;
  if(::close(fd)!=0)ok=false;
  if(ok)ok=::rename(tmp.c_str(),path.c_str())==0;
  if(!ok)::unlink(tmp.c_str());
  return ok;
}
inline std::string hash(const std::string &s) {
  unsigned char h[SHA256_DIGEST_LENGTH];
  SHA256((const unsigned char*)s.data(), s.size(), h);
  static const char hex[] = "0123456789abcdef";
  std::string out; out.reserve(64);
  for (auto c:h) { out += hex[c>>4]; out += hex[c&15]; }
  return out;
}
inline bool valid_bot(const std::string &s) {
  if (s.empty() || s.size()>32 || s[0]<'a' || s[0]>'z') return false;
  for (char c:s) if (!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-')) return false;
  return true;
}
inline bool valid_hex(const std::string &s) {
  if (s.size()!=64) return false;
  for(char c:s) if (!((c>='0'&&c<='9')||(c>='a'&&c<='f'))) return false;
  return true;
}
inline bool valid_path(const std::string &path) {
  if (path.empty() || path.size()>2048) return false;
  for(unsigned char c:path) if(c<32 || c==127) return false;
  return true;
}
inline std::string id(const std::string &path) { return hash(path); }
inline std::string index_path(const std::string &root, const std::string &bot,
                              const std::string &fileid) {
  return root+"/agent-previews/index/"+hash(bot)+"/"+fileid+".json";
}
inline std::string blob_path(const std::string &root, const std::string &digest) {
  return root+"/agent-previews/blobs/"+digest.substr(0,2)+"/"+digest+".txt";
}
inline std::string mime(const std::string &path) {
  std::string p=path;
  std::transform(p.begin(),p.end(),p.begin(),[](unsigned char c){return (char)std::tolower(c);});
  if(p.size()>=5 && p.compare(p.size()-5,5,".html")==0) return "text/html; charset=utf-8";
  if(p.size()>=4 && p.compare(p.size()-4,4,".htm")==0) return "text/html; charset=utf-8";
  if(p.size()>=4 && p.compare(p.size()-4,4,".css")==0) return "text/css; charset=utf-8";
  if(p.size()>=3 && p.compare(p.size()-3,3,".js")==0) return "text/javascript; charset=utf-8";
  if(p.size()>=4 && p.compare(p.size()-4,4,".mjs")==0) return "text/javascript; charset=utf-8";
  if(p.size()>=5 && p.compare(p.size()-5,5,".json")==0) return "application/json; charset=utf-8";
  if(p.size()>=4 && p.compare(p.size()-4,4,".svg")==0) return "image/svg+xml";
  if(p.size()>=4 && p.compare(p.size()-4,4,".txt")==0) return "text/plain; charset=utf-8";
  return "text/plain; charset=utf-8";
}
struct File { std::string bot,path,digest,content,mimetype; };
// Index + blob are both immutable-addressed except for the small atomic index
// pointing at latest version. Always verify SHA-256 before returning bytes.
inline bool get(const std::string &root, const std::string &bot,
                const std::string &fileid, File &out) {
  if(!valid_bot(bot)||!valid_hex(fileid))return false;
  std::lock_guard<std::mutex> lock(MUTEX);
  mj::Val entry;
  bool index_ok=false;
  const std::string index=read_text(index_path(root,bot,fileid),index_ok);
  if(!index_ok||!mj::parse(index,entry)||!entry.isObj())return false;
  const std::string path=(entry.find("path")?entry.find("path")->asStr():""), sha=(entry.find("sha256")?entry.find("sha256")->asStr():"");
  if(!valid_path(path)||!valid_hex(sha)||id(path)!=fileid||
     (entry.find("bot")?entry.find("bot")->asStr():"")!=bot)return false;
  bool ok=false;
  const std::string blob=read_text(blob_path(root,sha),ok);
  if(!ok||hash(blob)!=sha)return false;
  out={bot,path,sha,blob,mime(path)};
  return true;
}
inline bool put(const std::string &root, const std::string &bot,
                const std::string &path, const std::string &content,
                bool only_if_absent=false, uint64_t revision=0) {
  if(!valid_bot(bot)||!valid_path(path)||content.size()>4ULL*1024*1024)return false;
  const std::string sha=hash(content), key=id(path);
  std::lock_guard<std::mutex> lock(MUTEX);
  const std::string ref=index_path(root,bot,key), blob=blob_path(root,sha);
  if (std::filesystem::exists(ref)) {
    if(only_if_absent) return true;
    if(revision>0) {
      bool entry_ok=false; mj::Val previous;
      const std::string serialized=read_text(ref,entry_ok);
      if(entry_ok && mj::parse(serialized,previous) && previous.isObj()) {
        const mj::Val *older=previous.find("revision");
        // A stale chat bubble may never overwrite a newer version.
        if(older && older->type==mj::Val::NUM &&
           older->asNum()>(double)revision) return true;
      }
    }
  }
  std::error_code ec;
  std::filesystem::create_directories(std::filesystem::path(blob).parent_path(),ec);
  std::filesystem::create_directories(std::filesystem::path(ref).parent_path(),ec);
  if(ec)return false;
  if(!std::filesystem::exists(blob,ec) && !write_atomic(blob,content))return false;
  bool readok=false;
  const std::string verify=read_text(blob,readok);
  if(!readok || hash(verify)!=sha || verify!=content)return false;
  mj::Val entry=mj::Val::object();
  entry["bot"]=bot;entry["path"]=path;entry["sha256"]=sha;entry["revision"]=(double)revision;
  return write_atomic(ref,mj::dump(entry));
}
inline bool append(const std::string &root, const std::string &bot,
                   const std::string &path, const std::string &piece) {
  File f;
  if(!get(root,bot,id(path),f))return false;
  if(f.content.size()+piece.size()>4ULL*1024*1024)return false;
  return put(root,bot,path,f.content+piece);
}
inline std::string url(const std::string &bot,const std::string &path) {
  return "/agent-preview/"+bot+"/"+id(path);
}
inline std::string instrument(const std::string &source) {
  // The raw document is CSP-sandboxed (opaque origin); this reports only to
  // its own parent window. The parent verifies event.source before displaying.
  static const std::string tracer=R"TRACE(<script>
(()=>{'use strict';
 const send=(kind,value)=>{try{
  const s=typeof value==='string'?value: value instanceof Error?(value.stack||value.message):
    Array.from(value||[]).map(x=>x instanceof Error?(x.stack||x.message):
      typeof x==='string'?x:JSON.stringify(x)).join(' ');
  parent.postMessage({magPreviewError:true,kind,text:String(s||'').slice(0,10000)},'*');
 }catch(e){parent.postMessage({magPreviewError:true,kind,text:'[non-serializable error]'},'*')}};
 const old=console.error;
 console.error=function(...args){send('console.error',args);return old.apply(this,args)};
 addEventListener('error',e=>send('uncaught exception',e.message+(e.filename?' @ '+e.filename+':'+e.lineno+':'+e.colno:'')));
 addEventListener('unhandledrejection',e=>send('unhandled rejection',e.reason));
})();
</script>)TRACE";
  std::string output=source;
  auto low=output;std::transform(low.begin(),low.end(),low.begin(),[](unsigned char c){return (char)std::tolower(c);});
  auto pos=low.find("<head");
  if(pos!=std::string::npos) {
    pos=low.find('>',pos);
    if(pos!=std::string::npos){output.insert(pos+1,tracer);return output;}
  }
  pos=low.find("<!doctype");
  if(pos!=std::string::npos){pos=low.find('>',pos);if(pos!=std::string::npos)++pos;}
  else pos=0;
  output.insert(pos,tracer);
  return output;
}
// A complete, authenticated outer page. The untrusted code is rendered in an
// opaque-origin iframe; it cannot access room storage, cookies or the opener.
inline std::string shell() {
  return R"PREVIEW(<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAG · Agenten-Vorschau</title>
<style>
:root{color-scheme:dark;font:14px system-ui,Arial,sans-serif}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#15171b;color:#f5f5f5}
header{display:flex;align-items:center;gap:12px;padding:10px 14px;background:#20232a;border-bottom:1px solid #343842}
h1{font-size:14px;margin:0;flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
button{background:#343944;border:1px solid #515967;border-radius:7px;color:inherit;padding:7px 10px;cursor:pointer}
main{display:grid;grid-template-rows:minmax(180px,1fr) auto;height:calc(100% - 55px)}
iframe{width:100%;height:100%;background:white;border:0}
section{background:#121419;border-top:1px solid #363a44;min-height:40px;max-height:36vh;display:flex;flex-direction:column}
section[hidden]{display:none}
#console-head{display:flex;align-items:center;gap:10px;padding:6px 12px}
#console{white-space:pre-wrap;overflow:auto;flex:1;margin:0;padding:6px 12px 12px;font:12px/1.55 ui-monospace,monospace;color:#f4b6b6}
#notice{color:#b6b9be;font-size:12px}
</style></head><body>
<header><h1 id="name">MAG · Vorschau wird geladen…</h1><span id="notice">Live</span>
<button id="reload">Neu laden</button><button id="toggle">Konsole (0)</button></header>
<main><iframe id="render" sandbox="allow-scripts" title="Isolierte Agenten-Datei" referrerpolicy="no-referrer"></iframe>
<section id="errors" hidden><div id="console-head"><strong>JavaScript-Fehler</strong><button id="clear">Leeren</button></div>
<pre id="console" role="log" aria-live="polite"></pre></section></main>
<script>
(()=>{'use strict';
const frame=document.getElementById('render'),panel=document.getElementById('errors'),
  output=document.getElementById('console'),button=document.getElementById('toggle'),
  status=document.getElementById('notice'),label=document.getElementById('name');
let digest='',errors=0,poll=null;
const show=()=>{button.textContent='Konsole ('+errors+')'};
document.getElementById('toggle').onclick=()=>panel.hidden=!panel.hidden;
document.getElementById('clear').onclick=()=>{output.textContent='';errors=0;show()};
const reload=force=>{
  fetch(location.pathname+'?meta=1',{cache:'no-store',credentials:'same-origin'})
    .then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json()})
    .then(x=>{
      label.textContent=x.path||'Agenten-Vorschau';
      if(force||digest!==x.sha256){
        digest=x.sha256;output.textContent='';errors=0;show();
        frame.src=location.pathname+'?raw=1&v='+encodeURIComponent(digest);
        status.textContent='Aktuell';
      }
    }).catch(e=>{status.textContent='Nicht erreichbar: '+e.message});
};
document.getElementById('reload').onclick=()=>reload(true);
window.addEventListener('message',e=>{
  if(e.source!==frame.contentWindow||!e.data||e.data.magPreviewError!==true)return;
  errors++;show();panel.hidden=false;
  const line='['+new Date().toLocaleTimeString('de-DE')+'] '+String(e.data.kind||'error')+': '+String(e.data.text||'')+'\n';
  output.textContent=(output.textContent+line).slice(-60000);
  output.scrollTop=output.scrollHeight;
});
reload(true);poll=setInterval(()=>{if(!document.hidden)reload(false)},3000);
window.addEventListener('pagehide',()=>clearInterval(poll));
})();
</script></body></html>)PREVIEW";
}
} // namespace mag_preview
