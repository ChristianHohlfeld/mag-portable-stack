// Native MAG audit gateway and model switch. One public loopback listener :8080,
// one private, group-restricted Unix control socket for the C++ room.
// No subprocesses, model restarts, shell execution, or external dependencies at runtime.
#include "json.hpp"
#include <arpa/inet.h>
#include <fcntl.h>
#include <grp.h>
#include <pwd.h>
#include <netinet/in.h>
#include <openssl/sha.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <cctype>
#include <cerrno>
#include <stdexcept>
#include <iterator>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
using mj::Val;
namespace fs=std::filesystem;
static std::string env(const char *k,const std::string &def) {const char *p=getenv(k);return p&&*p?p:def;}
static int number(const char *k,int d){try{return std::stoi(env(k,std::to_string(d)));}catch(...){return d;}}
static std::string value(const Val &v,const char *k,const std::string &def=""){auto p=v.find(k);return p?p->asStr():def;}
static bool write_all(int fd,const std::string &s){size_t n=0;while(n<s.size()){ssize_t v=send(fd,s.data()+n,s.size()-n,MSG_NOSIGNAL);if(v<=0)return false;n+=(size_t)v;}return true;}
static std::string lower(std::string v){for(auto &c:v)c=(char)tolower((unsigned char)c);return v;}
static std::string sha(const std::string &s){unsigned char dig[SHA256_DIGEST_LENGTH];SHA256((const unsigned char*)s.data(),s.size(),dig);char out[65];for(unsigned i=0;i<32;i++)snprintf(out+2*i,3,"%02x",dig[i]);return std::string(out,64);}
static int connect_loopback(int port,int timeout_s=3){
  int fd=socket(AF_INET,SOCK_STREAM|SOCK_CLOEXEC,0);if(fd<0)return -1;
  timeval tv{timeout_s,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
  sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=htons((uint16_t)port);inet_pton(AF_INET,"127.0.0.1",&a.sin_addr);
  if(connect(fd,(sockaddr*)&a,sizeof(a))){close(fd);return -1;}return fd;
}
struct Config {
  int q4=8090,iq3=8091,listen=8080;
  std::string control="/run/mag-model-switch/control.sock";
  std::string logs="/var/log/mag-audit";
  std::string secrets="/etc/mag-audit/secrets.list";
} cfg;
static std::mutex audit_mtx, traffic_mtx;
static unsigned active_inference=0;
static bool gate_switch=false;
static std::string iso_time(){
  auto now=std::chrono::system_clock::now();auto t=std::chrono::system_clock::to_time_t(now);
  std::tm tm{};gmtime_r(&t,&tm);char b[40];strftime(b,sizeof(b),"%Y-%m-%dT%H:%M:%SZ",&tm);return b;
}
static int port_of(const std::string &id){return id=="iq3"?cfg.iq3:cfg.q4;}
static std::string redact(std::string s){
  std::ifstream in(cfg.secrets);std::vector<std::pair<std::string,std::string>> pairs;
  std::string line;while(std::getline(in,line)){auto p=line.find('=');if(p!=std::string::npos&&p+7<=line.size()&&line[0]!='#')pairs.push_back({line.substr(p+1),line.substr(0,p)});}
  std::sort(pairs.begin(),pairs.end(),[](auto&a,auto&b){return a.first.size()>b.first.size();});
  for(auto&p:pairs){size_t n=0;while((n=s.find(p.first,n))!=std::string::npos){std::string r="[SECRET:"+p.second+"]";s.replace(n,p.first.size(),r);n+=r.size();}}
  return s;
}
static std::string prev_hash(const std::string &path){
  std::ifstream in(path);if(!in)return std::string(64,'0');
  std::string line,last;
  while(std::getline(in,line))if(!line.empty())last=line;
  Val o;if(mj::parse(last,o)&&o.isObj()) {auto h=o.find("hash");if(h&&h->asStr().size()==64)return h->asStr();}
  return std::string(64,'0');
}
static std::string prior_log_hash(const fs::path &p){
  if(fs::exists(p))return prev_hash(p.string());
  std::string best;
  try{for(auto &e:fs::recursive_directory_iterator(cfg.logs)){if(e.is_regular_file()&&e.path().filename()=="events.jsonl"&&e.path().string()<p.string()&&e.path().string()>best)best=e.path().string();}}
  catch(const fs::filesystem_error&){}
  return best.empty()?std::string(64,'0'):prev_hash(best);
}
static void audit(Val entry){
  if(cfg.logs.empty())return;
  std::lock_guard<std::mutex> lk(audit_mtx);
  auto now=std::chrono::system_clock::now();auto t=std::chrono::system_clock::to_time_t(now);
  std::tm tm{};gmtime_r(&t,&tm);char dir[20];strftime(dir,sizeof(dir),"%Y/%m/%d",&tm);
  fs::path p=fs::path(cfg.logs)/dir/"events.jsonl";fs::create_directories(p.parent_path());
  static std::string previous_file,previous;
  if(previous_file!=p.string()){previous_file=p.string();previous=prior_log_hash(p);}
  entry["ts"]=iso_time();entry["prev_hash"]=previous;
  // Backwards compatible JSONL shape; raw pre-hash bytes are deterministic.
  Val clean;std::string serialized=redact(mj::dump(entry));
  if(!mj::parse(serialized,clean))throw std::runtime_error("audit redaction failed");
  serialized=mj::dump(clean);
  std::string digest=sha(previous+serialized);
  std::string line=serialized.substr(0,serialized.size()-1)+",\"hash\":\""+digest+"\"}\n";
  int fd=open(p.c_str(),O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC,0640);
  if(fd<0)throw std::runtime_error("audit file inaccessible");
  size_t n=0;while(n<line.size()){ssize_t k=write(fd,line.data()+n,line.size()-n);if(k<=0)break;n+=(size_t)k;}
  bool ok=n==line.size()&&fsync(fd)==0;close(fd);
  if(!ok)throw std::runtime_error("audit write failed");
  previous=digest;
}
static Val sse_assemble(const std::string &wire){
  std::string decoded;
  // HTTP/1.1 upstream may use chunked transfer encoding; do not log chunk sizes as model text.
  size_t off=0;bool chunked=!wire.empty();
  while(off<wire.size()){
    auto e=wire.find("\r\n",off);if(e==std::string::npos){chunked=false;break;}
    std::string size=wire.substr(off,e-off);auto semi=size.find(';');if(semi!=std::string::npos)size.resize(semi);
    if(size.empty()||size.size()>16||size.find_first_not_of("0123456789abcdefABCDEF")!=std::string::npos){chunked=false;break;}
    size_t n=0;try{n=std::stoull(size,nullptr,16);}catch(...){chunked=false;break;}
    off=e+2;
    if(n==0)break;
    if(n>wire.size()-off||wire.size()-off-n<2||wire.substr(off+n,2)!="\r\n"){chunked=false;break;}
    decoded.append(wire,off,n);off+=n+2;
  }
  if(!chunked)decoded=wire;
  Val meta=Val::object();Val choices=Val::array();
  std::map<int,Val> acc;
  std::istringstream lines(decoded);std::string l;
  while(std::getline(lines,l)){
    if(!l.empty()&&l.back()=='\r')l.pop_back();
    if(l.rfind("data:",0)!=0)continue;
    std::string data=l.substr(5);if(!data.empty()&&data[0]==' ')data.erase(0,1);
    if(data=="[DONE]")break;
    Val ev;if(!mj::parse(data,ev)||!ev.isObj())continue;
    for(const char *k:{"id","model","created","system_fingerprint","usage","timings"}){
      if(auto f=ev.find(k))meta[k]=*f;
    }
    if(auto err=ev.find("error"))meta["error"]=*err;
    auto arr=ev.find("choices");if(!arr||!arr->isArr())continue;
    for(auto &ch:arr->asArr()){
      int idx=ch.find("index")?(int)ch.find("index")->asNum():0;
      Val &item=acc[idx];if(!item.isObj()){
        item=Val::object();item["index"]=idx;item["finish_reason"]=Val();
        Val message=Val::object();message["role"]="assistant";message["content"]="";message["reasoning_content"]="";message["tool_calls"]=Val::array();item["message"]=message;
      }
      if(auto finish=ch.find("finish_reason");finish&&finish->type!=Val::NUL)item["finish_reason"]=*finish;
      auto delta=ch.find("delta");if(!delta||!delta->isObj())continue;
      Val &msg=item["message"];
      for(const char *k:{"content","reasoning_content","reasoning"}){
        auto v=delta->find(k);if(!v||v->type!=Val::STR)continue;
        std::string target=std::string(k)=="reasoning"?"reasoning_content":k;
        msg[target]=msg[target].asStr()+v->asStr();
      }
      auto tc=delta->find("tool_calls");
      if(tc&&tc->isArr()){
        Val &calls=msg["tool_calls"];
        for(const auto &t:tc->asArr()){
          int ti=t.find("index")?(int)t.find("index")->asNum():0;
          if(ti<0||ti>255)continue;
          while(calls.asArr().size()<=(size_t)ti){Val blank=Val::object();blank["type"]="function";Val fun=Val::object();fun["name"]="";fun["arguments"]="";blank["function"]=fun;calls.push(blank);}
          Val &cur=(*calls.arr)[ti];
          if(auto id=t.find("id"))cur["id"]=*id;
          if(auto typ=t.find("type"))cur["type"]=*typ;
          auto fn=t.find("function");if(fn&&fn->isObj()){
            for(const char *k:{"name","arguments"})if(auto v=fn->find(k))cur["function"][k]=cur["function"][k].asStr()+v->asStr();
          }
        }
      }
    }
  }
  for(auto &kv:acc)choices.push(kv.second);
  meta["choices"]=choices;return meta;
}
struct Http {
  std::string method,path,head,body;std::map<std::string,std::string> headers;
};
static bool request_read(int fd,Http &r){
  std::string s;char b[8192];size_t pos=std::string::npos;
  while((pos=s.find("\r\n\r\n"))==std::string::npos){
    if(s.size()>65536)return false;
    ssize_t n=recv(fd,b,sizeof(b),0);if(n<=0)return false;s.append(b,(size_t)n);
  }
  r.head=s.substr(0,pos+4);std::istringstream ss(r.head);std::string first;std::getline(ss,first);if(!first.empty()&&first.back()=='\r')first.pop_back();
  std::istringstream fl(first);fl>>r.method>>r.path; if(r.method.empty()||r.path.empty()||r.path[0]!='/')return false;
  std::string l;while(std::getline(ss,l)&&l!="\r"){if(!l.empty()&&l.back()=='\r')l.pop_back();auto p=l.find(':');if(p!=std::string::npos){auto k=lower(l.substr(0,p));auto v=l.substr(p+1);v.erase(0,v.find_first_not_of(' '));r.headers[k]=v;}}
  size_t expected=0;auto cl=r.headers.find("content-length");if(cl!=r.headers.end()){try{expected=(size_t)std::stoull(cl->second);}catch(...){return false;}}
  if(expected>64*1024*1024||r.headers.count("transfer-encoding"))return false;
  r.body=s.substr(pos+4);if(r.body.size()>expected)return false;
  while(r.body.size()<expected){ssize_t n=recv(fd,b,std::min(sizeof(b),expected-r.body.size()),0);if(n<=0)return false;r.body.append(b,(size_t)n);}
  return true;
}
static void json_response(int fd,int code,const Val &body){
  std::string payload=mj::dump(body);std::string status=code==200?"OK":code==202?"Accepted":code==403?"Forbidden":code==400?"Bad Request":code==409?"Conflict":"Bad Gateway";
  write_all(fd,"HTTP/1.1 "+std::to_string(code)+" "+status+"\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nContent-Length: "+std::to_string(payload.size())+"\r\nConnection: close\r\n\r\n"+payload);
}
static Val message(const std::string &e){Val v=Val::object();v["error"]=e;return v;}
// Root-owned C++ model supervisor is the only process allowed to manage
// systemd model units. A 0660 Unix socket and SO_PEERCRED on the supervisor
// separate the unprivileged audit gateway from systemd.
static Val supervisor_request(const std::string &method,const std::string &body=""){
  Val err=Val::object();err["error"]="Modellverwaltung nicht erreichbar";
  std::string path=env("MAG_MODEL_SUPERVISOR_SOCKET","/run/mag-model-supervisor/supervisor.sock");
  if(path.size()>=sizeof(sockaddr_un::sun_path))return err;
  int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);if(fd<0)return err;
  timeval timeout{3,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
  sockaddr_un a{};a.sun_family=AF_UNIX;strncpy(a.sun_path,path.c_str(),sizeof(a.sun_path)-1);
  if(connect(fd,(sockaddr*)&a,sizeof(a))){close(fd);return err;}
  std::string req=method+" /models HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Length: "+
      std::to_string(body.size())+"\r\n\r\n"+body;
  if(!write_all(fd,req)){close(fd);return err;}
  char b[4096];std::string raw;
  for(;;){ssize_t n=recv(fd,b,sizeof(b),0);if(n<=0)break;raw.append(b,n);if(raw.size()>65536)break;}
  close(fd);auto p=raw.find("\r\n\r\n");
  Val result;if(p==std::string::npos||raw.size()>65536||!mj::parse(raw.substr(p+4),result)||!result.isObj())return err;
  if(raw.rfind("HTTP/1.1 200",0)!=0&&raw.rfind("HTTP/1.1 202",0)!=0){
    if(!result.find("error"))result["error"]="Modellverwaltung nicht bereit";
  }
  return result;
}
static bool switching_or_unready(const Val &state){
  const std::string id=value(state,"active");
  return (id!="q4"&&id!="iq3") || !value(state,"pending").empty();
}
static void control(int fd){
  Http r;if(!request_read(fd,r))return;
  if(r.path!="/models"){json_response(fd,400,message("unknown control route"));return;}
  if(r.method=="GET"){
    Val result=supervisor_request("GET");
    // A completed switch must clear the traffic gate even if no new chat
    // arrived yet. Otherwise the owner cannot switch back without first
    // sending a (potentially unwanted) inference request.
    if(result.find("active") && !value(result,"active").empty() &&
       result.find("pending") && value(result,"pending").empty()){
      std::lock_guard<std::mutex> lock(traffic_mtx);
      gate_switch=false;
    }
    json_response(fd,result.find("error")&& !result.find("active")?503:200,result);
    return;
  }
  if(r.method!="PUT"){json_response(fd,400,message("invalid method"));return;}
  Val data;if(!mj::parse(r.body,data)||!data.isObj()||!data.find("id")){json_response(fd,400,message("invalid model"));return;}
  std::string id=value(data,"id");if(id!="q4"&&id!="iq3"){json_response(fd,400,message("invalid model"));return;}
  {
    std::lock_guard<std::mutex> lock(traffic_mtx);
    if(gate_switch ||active_inference){json_response(fd,409,message("Bitte laufende Antwort beenden, bevor das Modell gewechselt wird"));return;}
    gate_switch=true;
  }
  Val j=Val::object();j["id"]=id;
  Val res=supervisor_request("PUT",mj::dump(j));
  if(res.find("error") && !value(res,"error").empty()){
    std::lock_guard<std::mutex> lock(traffic_mtx);gate_switch=false;
    json_response(fd,503,res);
  } else {
    std::lock_guard<std::mutex> lock(traffic_mtx);
    // Same model requested -> no transaction was opened.
    gate_switch=!res.find("pending")?false:!res.find("pending")->asStr().empty();
    json_response(fd,202,res);
  }
}
static bool await_active(std::string &choice,int fd){
  for(int i=0;i<1800;i++){
    Val state=supervisor_request("GET");
    if(!switching_or_unready(state)){
      std::lock_guard<std::mutex> lock(traffic_mtx);
      if(!gate_switch ||(state.find("pending")&&state.find("pending")->asStr().empty())) {
        gate_switch=false;
        choice=value(state,"active");
        if(choice=="q4"||choice=="iq3"){++active_inference;return true;}
      }
    }
    if(i%10==0){char tmp;ssize_t n=recv(fd,&tmp,1,MSG_PEEK|MSG_DONTWAIT);if(n==0)return false;}
    std::this_thread::sleep_for(std::chrono::seconds(1));
  }
  return false;
}
struct InferenceLease{
  bool held=false;
  ~InferenceLease(){if(held){std::lock_guard<std::mutex>lock(traffic_mtx);if(active_inference) --active_inference;}}
};
static std::string audit_id(){return sha(std::to_string(std::time(nullptr))+"-"+std::to_string(getpid())+"-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())).substr(0,24);}
static Val parse_or_text(const std::string &s){
  Val j;if(mj::parse(s,j))return j;return Val(s);
}
static int peer_uid(int fd,int server_port){
  sockaddr_in addr{};socklen_t sz=sizeof(addr);
  if(getpeername(fd,(sockaddr*)&addr,&sz)!=0||addr.sin_family!=AF_INET)return -1;
  unsigned from=ntohs(addr.sin_port);
  for(const char *path:{"/proc/net/tcp","/proc/net/tcp6"}){
    std::ifstream input(path);std::string line;std::getline(input,line);
    while(std::getline(input,line)){
      std::istringstream stream(line);
      std::string slot,local,remote,state,tx,tm,retrans,uid;
      if(!(stream>>slot>>local>>remote>>state>>tx>>tm>>retrans>>uid))continue;
      auto cp=local.find(':');auto sp=remote.find(':');
      if(cp==std::string::npos||sp==std::string::npos)continue;
      try{
        int local_port=std::stoi(local.substr(cp+1),nullptr,16);
        int remote_port=std::stoi(remote.substr(sp+1),nullptr,16);
        if(local_port==(int)from&&remote_port==server_port)return std::stoi(uid);
      }catch(...){}
    }
  }
  return -1;
}
static void proxy(int fd){
  Http r;if(!request_read(fd,r))return;
  const bool quiet=r.path=="/health"||r.path=="/v1/models"||r.path=="/props"||r.path=="/metrics"||r.path=="/slots";
  std::string id=audit_id(),selected;
  InferenceLease lease;
  // A model transaction temporarily halts only inference; the control/status
  // endpoints and the room itself remain available throughout the load.
  const bool inference=r.path=="/v1/chat/completions"||r.path=="/completion"||r.path=="/chat/completions";
  if(r.path=="/health"){
    Val selection=supervisor_request("GET");
    if(switching_or_unready(selection)){
      Val m=Val::object();m["status"]="loading";
      json_response(fd,503,m);return;
    }
  }
  if(inference){
    if(!await_active(selected,fd)){
      Val m=Val::object();m["error"]="Modell wird geladen. Anfrage bitte erneut senden.";
      json_response(fd,503,m);return;
    }
    lease.held=true;
  }else{
    Val selection=supervisor_request("GET");
    if(switching_or_unready(selection)){Val m=Val::object();m["status"]="loading";json_response(fd,503,m);return;}
    selected=value(selection,"active");
  }
  if(r.path=="/audit/v1/health"){Val v=Val::object();v["ok"]=true;json_response(fd,200,v);return;}
  if(r.path=="/audit/v1/event"&&r.method=="POST"){
    Val ev=parse_or_text(r.body);Val v=Val::object();v["type"]="bot_event";v["id"]=id;v["event"]=ev;audit(v);
    Val ok=Val::object();ok["ok"]=true;ok["id"]=id;json_response(fd,200,ok);return;
  }
  if(r.path.rfind("/audit/",0)==0){json_response(fd,400,message("unknown audit route"));return;}
  auto started=std::chrono::steady_clock::now();
  bool is_llm=r.path.rfind("/v1/",0)==0||r.path.rfind("/completion",0)==0||r.path.rfind("/chat",0)==0;
  Val caller=Val::object();caller["peer"]="127.0.0.1";
  int uid=peer_uid(fd,cfg.listen);
  if(uid>=0){caller["uid"]=uid;const struct passwd *pwd=getpwuid(uid);if(pwd&&pwd->pw_name)caller["user"]=pwd->pw_name;}
  caller["bot"]=r.headers.count("x-mag-bot")?r.headers["x-mag-bot"]:"";
  caller["session"]=r.headers.count("x-mag-session")?r.headers["x-mag-session"]:"";
  if(!quiet){
    Val v=Val::object();v["type"]=is_llm?"llm_request":"http_request";v["id"]=id;v["caller"]=caller;v["method"]=r.method;v["path"]=r.path;v["model_variant"]=selected;v["body"]=parse_or_text(r.body);
    Val hs=Val::object();for(auto &kv:r.headers){std::string k=kv.first;hs[k]=(k=="authorization"||k=="cookie"||k=="x-api-key"||k=="proxy-authorization"||k=="x-auth-token")?"[REDACTED]":kv.second;}v["headers"]=hs;audit(v);
  }
  int up=connect_loopback(port_of(selected),3);
  if(up<0){json_response(fd,502,message("upstream unavailable"));return;}
  // Preserve transparent SSE/chunked framing, request bodies, and cancellation.
  std::ostringstream request;request<<r.method<<" "<<r.path<<" HTTP/1.1\r\nHost: 127.0.0.1:"<<port_of(selected)<<"\r\n";
  for(auto &kv:r.headers){
    const std::string &k=kv.first;
    if(k=="host"||k=="connection"||k=="keep-alive"||k=="transfer-encoding"||k=="proxy-authorization"||k=="x-mag-audit-id"||k=="content-length"||k.rfind("x-mag-",0)==0)continue;
    request<<k<<": "<<kv.second<<"\r\n";
  }
  // The second llama instance advertises its own alias. Keep the public
  // gateway contract unchanged while routing OpenAI-compatible model requests.
  std::string forwarded=r.body;
  if(is_llm) {
    Val j;
    if(mj::parse(forwarded,j)&&j.isObj()&&j.find("model")){
      j["model"]=selected=="iq3"?"deepseek-v4-flash-0731-iq3":"deepseek-v4-flash-0731";
      forwarded=mj::dump(j);
    }
  }
  request<<"X-Mag-Audit-Id: "<<id<<"\r\nConnection: close\r\nContent-Length: "<<forwarded.size()<<"\r\n\r\n";
  bool wrote=write_all(up,request.str())&&write_all(up,forwarded);
  std::string full;full.reserve(8192);bool aborted=!wrote;size_t received=0;
  if(wrote){
    char b[32768];for(;;){
      pollfd pfd[2]={{up,POLLIN,0},{fd,POLLIN,0}};
      int result=poll(pfd,2,1000);
      if(result<0){if(errno==EINTR)continue;aborted=true;break;}
      if(!result)continue;
      if(pfd[1].revents&(POLLIN|POLLHUP|POLLERR)){
        char probe;ssize_t n=recv(fd,&probe,1,MSG_PEEK|MSG_DONTWAIT);
        if(n==0||n<0&&(errno!=EAGAIN&&errno!=EWOULDBLOCK)){aborted=true;break;}
      }
      if(pfd[0].revents&(POLLIN|POLLHUP|POLLERR)){
        ssize_t n=recv(up,b,sizeof(b),0);
        if(n==0)break;
        if(n<0){if(errno==EINTR||errno==EAGAIN)continue;aborted=true;break;}
        received+=(size_t)n;
        if(full.size()<64*1024*1024)full.append(b,(size_t)std::min((size_t)n,(size_t)(64*1024*1024-full.size())));
        if(!write_all(fd,std::string(b,(size_t)n))){aborted=true;break;}
      }
    }
  }
  shutdown(up,SHUT_RDWR);close(up);
  if(!quiet){
    Val v=Val::object();v["type"]=is_llm?"llm_response":"http_response";v["id"]=id;v["caller"]=caller;v["path"]=r.path;v["model_variant"]=selected;
    v["aborted"]=aborted;v["client_disconnected"]=aborted;v["duration_s"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
    v["bytes"]=(double)received;
    auto sep=full.find("\r\n\r\n");if(sep!=std::string::npos){
      std::string headers=full.substr(0,sep);std::istringstream h(headers);std::string prot;int status=0;h>>prot>>status;v["status"]=status;
      std::string payload=full.substr(sep+4);
      bool streamed=lower(headers).find("text/event-stream")!=std::string::npos;
      v["streamed"]=streamed;
      v["body"]=streamed?sse_assemble(payload):parse_or_text(payload);
    }
    audit(v);
  }
}
static int server(int port){
  int fd=socket(AF_INET,SOCK_STREAM|SOCK_CLOEXEC,0);
  int v=1;setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&v,sizeof(v));
  sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=htons((uint16_t)port);inet_pton(AF_INET,"127.0.0.1",&a.sin_addr);
  if(bind(fd,(sockaddr*)&a,sizeof(a))||listen(fd,128))throw std::runtime_error("failed to bind loopback gateway");
  return fd;
}
static int unix_server(const std::string &path){
  fs::create_directories(fs::path(path).parent_path());
  int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);sockaddr_un a{};a.sun_family=AF_UNIX;
  if(path.size()>=sizeof(a.sun_path))throw std::runtime_error("control path too long");
  strcpy(a.sun_path,path.c_str());unlink(path.c_str());
  if(bind(fd,(sockaddr*)&a,sizeof(a))||listen(fd,16))throw std::runtime_error("cannot bind private control socket");
  struct group *g=getgrnam(env("MAG_MODEL_CONTROL_GROUP","magbot").c_str());
  if(!g||chown(path.c_str(),-1,g->gr_gid)||chmod(path.c_str(),0660))throw std::runtime_error("control socket owner/group failed (fail closed)");
  return fd;
}
int main(){
  try{
    cfg.listen=number("MAG_AUDIT_LISTEN_PORT",8080);cfg.q4=number("MAG_MODEL_Q4_PORT",8090);cfg.iq3=number("MAG_MODEL_IQ3_PORT",8091);
    cfg.control=env("MAG_MODEL_CONTROL_SOCKET",cfg.control);
    cfg.logs=env("MAG_AUDIT_LOG_DIR",cfg.logs);cfg.secrets=env("MAG_AUDIT_SECRETS_FILE",cfg.secrets);
    int public_fd=server(cfg.listen);int private_fd=unix_server(cfg.control);
    Val start=Val::object();start["type"]="gateway";start["event"]="start";start["upstream"]="8090/8091";audit(start);
    for(;;){
      pollfd fds[2]={{public_fd,POLLIN,0},{private_fd,POLLIN,0}};
      if(poll(fds,2,-1)<0){if(errno==EINTR)continue;break;}
      for(int i=0;i<2;i++)if(fds[i].revents&POLLIN){
        int fd=accept4(fds[i].fd,nullptr,nullptr,SOCK_CLOEXEC);
        if(fd<0)continue;
        std::thread([fd,i]{timeval timeout{7200,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
          if(i==0)proxy(fd);else control(fd);close(fd);}).detach();
      }
    }
  }catch(const std::exception &e){std::cerr<<"mag-audit-gateway: "<<e.what()<<"\n";return 1;}
  return 0;
}
