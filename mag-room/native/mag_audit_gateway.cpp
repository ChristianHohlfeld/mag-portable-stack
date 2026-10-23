// Native MAG audit gateway. One public loopback listener :8080.
// Requests name a model; Q4 stays on :8090 and selected NUMA1 on :8091. Neither process
// is started, stopped, or restarted here.
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
  std::string secondary="iq3"; // qwen only after verified operator cutover
  std::string logs="/var/log/mag-audit";
  std::string secrets="/etc/mag-audit/secrets.list";
} cfg;
static std::mutex audit_mtx;
static std::string iso_time(){
  auto now=std::chrono::system_clock::now();auto t=std::chrono::system_clock::to_time_t(now);
  std::tm tm{};gmtime_r(&t,&tm);char b[40];strftime(b,sizeof(b),"%Y-%m-%dT%H:%M:%SZ",&tm);return b;
}
static bool upstream_up(int port){
  int fd=connect_loopback(port,1); if(fd<0)return false;
  bool ok=write_all(fd,"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
  std::string s; char b[512];
  if(ok){ssize_t n=recv(fd,b,sizeof(b),0); if(n>0)s.append(b,(size_t)n);}
  close(fd);
  return s.find(" 200 ")!=std::string::npos;
}
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
// Hot-swap (mag-numa-swap): the model a NUMA node serves is in its state file.
// A request naming that model (or "numa0"/"numa1") goes to the node's port.
static bool numa_route(const std::string &asked,std::string &selected,int &port){
  const char *path=std::getenv("MAG_NUMA_STATE");
  std::ifstream f(path&&*path?path:"/var/lib/mag-numa/state.json");
  if(!f||asked.empty())return false;
  std::stringstream ss;ss<<f.rdbuf();
  Val st;if(!mj::parse(ss.str(),st)||!st.isObj())return false;
  auto nodes=st.find("nodes");if(!nodes||!nodes->isObj())return false;
  for(int n=0;n<2;n++){
    auto node=nodes->find(std::to_string(n));if(!node||!node->isObj())continue;
    auto model=node->find("model");if(!model)continue;
    if(asked==model->asStr()||asked=="numa"+std::to_string(n)){
      selected="numa:"+model->asStr();port=n==0?cfg.q4:cfg.iq3;return true;
    }
  }
  return false;
}
static bool choose_model(const Http &r,std::string &selected,int &port){
  selected="q4"; port=cfg.q4;
  std::string asked;
  auto header=r.headers.find("x-mag-model");
  if(header!=r.headers.end()) asked=header->second;
  if(asked.empty()&&!r.body.empty()){
    Val body; if(mj::parse(r.body,body)&&body.isObj()) if(auto model=body.find("model")) asked=model->asStr();
  }
  if(numa_route(asked,selected,port))return true;
  if((asked=="iq3"||asked=="deepseek-v4-flash-0731-iq3")&&cfg.secondary=="iq3"){selected="iq3"; port=cfg.iq3;return true;}
  if((asked=="qwen"||asked=="qwen3.8-flash-next-uncensored")&&cfg.secondary=="qwen"){selected="qwen"; port=cfg.iq3;return true;}
  // No fallback on an explicitly unknown or inactive secondary model.
  return asked.empty()||asked=="q4"||asked=="deepseek-v4-flash-0731";
}
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
  std::string id=audit_id(),selected="q4";
  int upstream=cfg.q4;
  if((r.method=="GET"||r.method=="HEAD")&&(r.path=="/v1/models"||r.path=="/v1/models/")){
    Val data=Val::array();
    auto add=[&](const char *name,const char *label,int port){
      Val row=Val::object(); row["id"]=name; row["object"]="model"; row["owned_by"]="mag"; row["label"]=label; row["ready"]=upstream_up(port); data.push(row);
    };
    add("deepseek-v4-flash-0731","Q4 NUMA0",cfg.q4);
    if(cfg.secondary=="qwen")add("qwen3.8-flash-next-uncensored","Qwen3.8 NUMA1",cfg.iq3);
    else add("deepseek-v4-flash-0731-iq3","IQ3 NUMA1",cfg.iq3);
    Val listed=Val::object(); listed["object"]="list"; listed["data"]=data;
    json_response(fd,200,listed); return;
  }
  if(!choose_model(r,selected,upstream)){
    json_response(fd,400,message("unknown model (no fallback)"));return;
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
  int up=connect_loopback(upstream,3);
  if(up<0){json_response(fd,502,message("upstream unavailable"));return;}
  // Preserve transparent SSE/chunked framing, request bodies, and cancellation.
  std::ostringstream request;request<<r.method<<" "<<r.path<<" HTTP/1.1\r\nHost: 127.0.0.1:"<<upstream<<"\r\n";
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
      j["model"]=selected.rfind("numa:",0)==0?selected.substr(5):selected=="qwen"?"qwen3.8-flash-next-uncensored":selected=="iq3"?"deepseek-v4-flash-0731-iq3":"deepseek-v4-flash-0731";
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
int main(){
  try{
    cfg.listen=number("MAG_AUDIT_LISTEN_PORT",8080);cfg.q4=number("MAG_MODEL_Q4_PORT",8090);cfg.iq3=number("MAG_MODEL_IQ3_PORT",8091);cfg.secondary=env("MAG_SECONDARY_MODEL","iq3");
    if(cfg.secondary!="iq3"&&cfg.secondary!="qwen")throw std::runtime_error("invalid MAG_SECONDARY_MODEL (fail closed)");
    cfg.logs=env("MAG_AUDIT_LOG_DIR",cfg.logs);cfg.secrets=env("MAG_AUDIT_SECRETS_FILE",cfg.secrets);
    int public_fd=server(cfg.listen);
    Val start=Val::object();start["type"]="gateway";start["event"]="start";start["upstream"]="8090";audit(start);
    for(;;){
      pollfd pfd{public_fd,POLLIN,0};
      if(poll(&pfd,1,-1)<0){if(errno==EINTR)continue;break;}
      if(pfd.revents&POLLIN){
        int fd=accept4(public_fd,nullptr,nullptr,SOCK_CLOEXEC);
        if(fd<0)continue;
        std::thread([fd]{timeval timeout{7200,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));proxy(fd);close(fd);}).detach();
      }
    }
  }catch(const std::exception &e){std::cerr<<"mag-audit-gateway: "<<e.what()<<"\n";return 1;}
  return 0;
}
