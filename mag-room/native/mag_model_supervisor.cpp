// MAG model supervisor: single-purpose privileged C++17 process.
// Only allowlisted systemd stop/start operations, no shell, no arbitrary paths.
// In production, peer UID must be mag-audit and startup requires root.
// MODEL_SUPERVISOR_TEST=1 works only for non-root CI (fake models, no systemctl).
#include "json.hpp"
#include <arpa/inet.h>
#include <fcntl.h>
#include <grp.h>
#include <netinet/in.h>
#include <pwd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdlib>
#include <ctime>
#include <stdexcept>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <regex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
using mj::Val;
namespace fs=std::filesystem;
static std::string env(const char *k,const std::string &d){auto p=getenv(k);return p&&*p?p:d;}
static bool testing=false;
static std::string state_file, socket_path;
static int ports[2]{8090,8091},timeout_s=1800,interval_ms=1000;
static std::atomic<bool> online[2]{{true},{false}},ever_dual{false};
static std::mutex mtx;
static std::string active="q4",pending="",last_good="q4",failure="";
static std::string unit(int i){return i==0?"mag-llm.service":"mag-llm-iq3.service";}
static std::string name(int i){return i==0?"q4":"iq3";}
static int idx(const std::string &x){return x=="q4"?0:x=="iq3"?1:-1;}
static bool write_all(int fd,const std::string &s){size_t n=0;while(n<s.size()){ssize_t z=send(fd,s.data()+n,s.size()-n,MSG_NOSIGNAL);if(z<=0)return false;n+=z;}return true;}
static std::string read_file(const std::string &p){std::ifstream f(p);return f?std::string(std::istreambuf_iterator<char>(f),{}):"";}
static void atomic_write(const std::string &p,const std::string &s){
  fs::create_directories(fs::path(p).parent_path());
  std::string tmp=p+".tmp."+std::to_string(getpid());
  int fd=open(tmp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
  if(fd<0)throw std::runtime_error("unable to create model selector");
  size_t n=0;while(n<s.size()){ssize_t k=write(fd,s.data()+n,s.size()-n);if(k<=0)break;n+=k;}
  bool ok=n==s.size()&&!fsync(fd);close(fd);
  if(!ok||rename(tmp.c_str(),p.c_str())){unlink(tmp.c_str());throw std::runtime_error("unable to persist model selector");}
  int dir=open(fs::path(p).parent_path().c_str(),O_RDONLY|O_DIRECTORY);if(dir>=0){fsync(dir);close(dir);}
}
static Val state_locked(){
  Val j=Val::object();j["active"]=active;j["pending"]=pending;j["last_good"]=last_good;j["state"]=pending.empty()?"ready":"loading";j["error"]=failure;
  Val a=Val::array();
  for(int i=0;i<2;i++){Val x=Val::object();x["id"]=name(i);x["name"]=i?"DeepSeek V4 · IQ3_XXS":"DeepSeek V4 · Q4_K_XL";x["selected"]=active==name(i);x["loading"]=pending==name(i);a.push(x);}
  j["models"]=a;if(testing){j["resident_q4"]=online[0].load();j["resident_iq3"]=online[1].load();j["overlap_detected"]=ever_dual.load();}
  return j;
}
static void save_locked(){atomic_write(state_file,mj::dump(state_locked())+"\n");}
static void load(){
  std::string s=read_file(state_file);if(s.empty())return;
  Val j;if(!mj::parse(s,j)||!j.isObj())throw std::runtime_error("bad persisted model selector");
  auto field=[&](const char *k){auto v=j.find(k);return v?v->asStr():"";};
  active=field("active");pending=field("pending");last_good=field("last_good");failure=field("error");
  if(testing){online[0]=active=="q4";online[1]=active=="iq3";}
  if((idx(active)<0&&!active.empty())||idx(last_good)<0||(idx(pending)<0&&!pending.empty()))throw std::runtime_error("unknown model selector ID");
}
static int command(const std::string &verb,int i,std::string *capture=nullptr){
  if(testing){
    if(verb=="stop"){online[i]=false;return 0;}
    if(verb=="start"){if(online[1-i]){ever_dual=true;return 1;}online[i]=true;return 0;}
    if(verb=="is-active")return online[i]?0:3;
    if(verb=="show"){if(capture)*capture="0\n";return 0;}
    return 1;
  }
  std::string u=unit(i);
  int fds[2];if(pipe(fds))return -1;
  pid_t pid=fork();
  if(pid<0){close(fds[0]);close(fds[1]);return -1;}
  if(pid==0){
    close(fds[0]);dup2(fds[1],STDOUT_FILENO);dup2(fds[1],STDERR_FILENO);close(fds[1]);
    // Explicit argv whitelist, never a shell. No arbitrary units or paths.
    if(verb=="show")execl("/usr/bin/systemctl","systemctl","show","-p","MainPID","--value",u.c_str(),(char*)nullptr);
    else execl("/usr/bin/systemctl","systemctl",verb.c_str(),u.c_str(),(char*)nullptr);
    _exit(127);
  }
  close(fds[1]);std::string text;char b[512];ssize_t n;
  while((n=read(fds[0],b,sizeof(b)))>0)if(text.size()<8192)text.append(b,(size_t)std::min(n,(ssize_t)(8192-text.size())));
  close(fds[0]);int status;while(waitpid(pid,&status,0)<0&&errno==EINTR){}
  if(capture)*capture=text;
  return WIFEXITED(status)?WEXITSTATUS(status):1;
}
static bool is_up(int i){return command("is-active",i)==0;}
static bool probe(int i){
  if(testing&&(!online[i]||read_file(env("MAG_SUPERVISOR_TEST_FAIL_FILE","/nonexistent"))==name(i)))return false;
  int fd=socket(AF_INET,SOCK_STREAM|SOCK_CLOEXEC,0);if(fd<0)return false;
  timeval tv{2,0};setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
  sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=htons((uint16_t)ports[i]);inet_pton(AF_INET,"127.0.0.1",&a.sin_addr);
  if(connect(fd,(sockaddr*)&a,sizeof(a))){close(fd);return false;}
  bool wrote=write_all(fd,"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
  char b[256];ssize_t k=wrote?recv(fd,b,sizeof(b),0):-1;close(fd);
  if(k<12)return false;std::string s(b,k);return s.rfind("HTTP/1.1 200",0)==0||s.rfind("HTTP/1.0 200",0)==0;
}
static bool wait_state(int i,bool running,int ms){
  auto end=std::chrono::steady_clock::now()+std::chrono::milliseconds(ms);
  do{
    if(is_up(i)==running &&(running?probe(i):!probe(i)))return true;
    std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
  }while(std::chrono::steady_clock::now()<end);
  return false;
}
static bool numa_local(int i){
  if(testing)return true;
  std::string out;if(command("show",i,&out))return false;
  int pid=atoi(out.c_str());if(pid<=0)return false;
  std::string status=read_file("/proc/"+std::to_string(pid)+"/status");
  if(status.find("Cpus_allowed_list:\t0-13,28-41")==std::string::npos)return false;
  std::istringstream in(read_file("/proc/"+std::to_string(pid)+"/numa_maps"));
  size_t local=0,remote=0;bool bound=false;std::string line;
  std::regex anon("(^| )anon=([0-9]+)"),n0("(^| )N0=([0-9]+)"),n1("(^| )N1=([0-9]+)");
  while(std::getline(in,line)){
    if(line.find("bind:0")!=std::string::npos)bound=true;
    std::smatch m;if(!std::regex_search(line,m,anon)||std::stoull(m[2].str())<131072)continue;
    if(std::regex_search(line,m,n0))local+=std::stoull(m[2].str());
    if(std::regex_search(line,m,n1))remote+=std::stoull(m[2].str());
  }
  // 8 million resident local 4k pages ~= 32 GiB; reject remote large anonymous weights.
  return bound&&local>=8*1024*1024&&remote==0;
}
static bool node_capacity(int i){
  if(testing)return true;
  std::string txt=read_file("/sys/devices/system/node/node0/meminfo");
  std::istringstream in(txt);std::string line;
  unsigned long long total=0,free=0,file=0,shmem=0;
  while(std::getline(in,line)){
    std::istringstream v(line);std::string node,id,key;unsigned long long n=0;
    if(!(v>>node>>id>>key>>n))continue;
    if(key=="MemTotal:")total=n;
    else if(key=="MemFree:")free=n;
    else if(key=="FilePages:")file=n;
    else if(key=="Shmem:")shmem=n;
  }
  // -rtr model weights are anonymous private and already fully unloaded.
  // Disk-read page cache left by the previous GGUF is reclaimable, unlike
  // locked model weights. Count it conservatively; still reserve >20 GiB.
  unsigned long long reclaim=file>shmem?file-shmem:0;
  unsigned long long usable=std::min(total,free+reclaim);
  unsigned long long required=(i==0?175ull:125ull)*1024*1024;
  return total>=230ull*1024*1024&&usable>=required;
}
static void transition(std::string wanted,bool recovering=false){
  int target=idx(wanted);std::string previous;
  {std::lock_guard<std::mutex>lock(mtx);previous=active.empty()?last_good:active;}
  int old=idx(previous);
  bool success=false,restored=false;
  try{
    // No model process may remain resident before the other starts.
    if(old>=0 && is_up(old)){
      if(command("stop",old)||!wait_state(old,false,120000))throw std::runtime_error("failed to unload previous model");
    }
    if(is_up(1-target))throw std::runtime_error("other model remains resident");
    if(!node_capacity(target))throw std::runtime_error("node0 RAM headroom unavailable");
    if(command("start",target)||!wait_state(target,true,timeout_s*1000)||!numa_local(target))
      throw std::runtime_error("new model did not become healthy and NUMA-local");
    success=true;
  }catch(const std::exception&e){
    std::cerr<<"model switch: "<<e.what()<<"\n";
    if(target>=0&&is_up(target))command("stop",target);
    if(target>=0)wait_state(target,false,120000);
    if(old>=0){
      if(!is_up(old)){if(command("start",old)==0)restored=wait_state(old,true,timeout_s*1000)&&numa_local(old);}
      else restored=probe(old)&&numa_local(old);
    }
  }
  std::lock_guard<std::mutex>lock(mtx);
  if(success){active=wanted;last_good=previous;failure="";}
  else{
    active=restored?previous:"";
    failure=restored?"Modellwechsel fehlgeschlagen; vorheriges Modell wiederhergestellt":"Modellwechsel fehlgeschlagen; manuelle Wiederherstellung erforderlich";
  }
  pending.clear();save_locked();
  (void)recovering;
}
static void monitor(){
  // Health might be delayed by long prompt prefill. Do not restart due to
  // short /health timeouts: 15 min before automatic failback.
  auto last=std::chrono::steady_clock::now();
  std::string last_model;
  for(;;){
    std::this_thread::sleep_for(std::chrono::seconds(testing?1:10));
    std::string current,old;
    {std::lock_guard<std::mutex>lock(mtx);if(!pending.empty())continue;current=active;old=last_good;}
    if(current.empty())continue;
    if(current!=last_model){last_model=current;last=std::chrono::steady_clock::now();}
    if(probe(idx(current))){last=std::chrono::steady_clock::now();continue;}
    if(std::chrono::steady_clock::now()-last<std::chrono::minutes(15))continue;
    {std::lock_guard<std::mutex>lock(mtx);if(!pending.empty())continue;pending=old;save_locked();}
    transition(old,true);last=std::chrono::steady_clock::now();
  }
}
struct Req {std::string method,path,body;};
static bool recv_request(int fd,Req &r){
  timeval tv{3,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
  std::string s;char b[4096];size_t pos;
  while((pos=s.find("\r\n\r\n"))==std::string::npos){
    if(s.size()>4096)return false;
    ssize_t n=recv(fd,b,sizeof(b),0);if(n<=0)return false;s.append(b,n);
  }
  std::istringstream line(s.substr(0,s.find("\r\n")));line>>r.method>>r.path;
  if(r.path!="/models"||(r.method!="GET"&&r.method!="PUT"))return false;
  auto p=s.find("Content-Length:");if(p==std::string::npos&&r.method=="PUT")return false;
  size_t want=0;if(p!=std::string::npos){try{want=std::stoul(s.substr(p+15));}catch(...){return false;}}
  if(want>256)return false;
  r.body=s.substr(pos+4);
  if(r.body.size()>want)return false;
  while(r.body.size()<want){ssize_t n=recv(fd,b,std::min(sizeof(b),want-r.body.size()),0);if(n<=0)return false;r.body.append(b,n);}
  return true;
}
static void reply(int fd,int code,const Val &v){
  std::string b=mj::dump(v);
  std::string h="HTTP/1.1 "+std::to_string(code)+" "+(code==200?"OK":code==202?"Accepted":"Conflict")+
  "\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nContent-Length: "+std::to_string(b.size())+"\r\nConnection: close\r\n\r\n";
  write_all(fd,h+b);
}
static Val error(const std::string &x){Val v=Val::object();v["error"]=x;return v;}
static void handle(int fd){
  struct ucred peer{};socklen_t sz=sizeof(peer);
  if(getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&sz))return;
  struct passwd *pw=getpwnam(env("MAG_SUPERVISOR_CLIENT","mag-audit").c_str());
  if(!testing&&(!pw||(peer.uid!=pw->pw_uid&&peer.uid!=0))) {reply(fd,403,error("unauthorized peer"));return;}
  if(testing&&peer.uid!=getuid())return;
  Req r;if(!recv_request(fd,r)){reply(fd,400,error("invalid request"));return;}
  if(r.method=="GET"){std::lock_guard<std::mutex>lock(mtx);reply(fd,200,state_locked());return;}
  Val body;if(!mj::parse(r.body,body)||!body.isObj()||!body.find("id")){reply(fd,400,error("invalid JSON"));return;}
  std::string wanted=body.find("id")->asStr();
  if(idx(wanted)<0){reply(fd,400,error("unknown model"));return;}
  {
    std::lock_guard<std::mutex>lock(mtx);
    if(!pending.empty()){reply(fd,409,error("switch already in progress"));return;}
    if(wanted==active){reply(fd,200,state_locked());return;}
    pending=wanted;failure="";
    try{save_locked();}catch(...){pending.clear();reply(fd,500,error("state persistence failed"));return;}
    reply(fd,202,state_locked());
  }
  std::thread([wanted]{transition(wanted);}).detach();
}
int main(){
  try{
    testing=env("MAG_SUPERVISOR_TEST","")=="1";
    if(testing&&geteuid()==0)throw std::runtime_error("never run fake mode as root");
    if(!testing&&geteuid()!=0)throw std::runtime_error("supervisor requires root");
    state_file=env("MAG_MODEL_STATE_FILE","/var/lib/mag-llm/hotswap-selector.json");
    socket_path=env("MAG_MODEL_SUPERVISOR_SOCKET","/run/mag-model-supervisor/supervisor.sock");
    ports[0]=std::stoi(env("MAG_MODEL_Q4_PORT","8090"));ports[1]=std::stoi(env("MAG_MODEL_IQ3_PORT","8091"));
    timeout_s=std::stoi(env("MAG_MODEL_SWITCH_TIMEOUT","1800"));
    interval_ms=testing?50:1000;
    if(timeout_s<1||timeout_s>10800)throw std::runtime_error("bad model timeout");
    if(socket_path.size()>=sizeof(sockaddr_un::sun_path))throw std::runtime_error("socket path too long");
    load();
    fs::create_directories(fs::path(socket_path).parent_path());
    // If a previous switch died between unload and load, restore old selection.
    if(!pending.empty()){
      const std::string previous=active.empty()?last_good:active;
      int old=idx(previous),other=1-old;
      if(old<0)throw std::runtime_error("no recoverable model");
      if(is_up(other)) {if(command("stop",other)!=0||!wait_state(other,false,120000))throw std::runtime_error("cannot unload interrupted target");}
      if(is_up(old)){
        active=previous;pending.clear();failure="Unterbrochener Wechsel zurückgesetzt";save_locked();
      }else{
        pending=previous;
        std::thread([previous]{transition(previous,true);}).detach();
      }
    }
    int server=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);
    sockaddr_un addr{};addr.sun_family=AF_UNIX;strcpy(addr.sun_path,socket_path.c_str());unlink(socket_path.c_str());
    if(bind(server,(sockaddr*)&addr,sizeof(addr))||listen(server,16))throw std::runtime_error("cannot bind supervisor socket");
    struct group *grp=getgrnam(env("MAG_SUPERVISOR_GROUP","mag-audit").c_str());
    if(!grp||chown(socket_path.c_str(),-1,grp->gr_gid)||chmod(socket_path.c_str(),0660))
      throw std::runtime_error("supervisor socket permissions failed");
    std::thread(monitor).detach();
    for(;;){int fd=accept4(server,nullptr,nullptr,SOCK_CLOEXEC);if(fd<0){if(errno==EINTR)continue;break;}
      std::thread([fd]{try{handle(fd);}catch(const std::exception&e){std::cerr<<"model supervisor request: "<<e.what()<<"\n";}close(fd);}).detach();
    }
  }catch(const std::exception&e){std::cerr<<"model supervisor: "<<e.what()<<"\n";return 1;}
  return 0;
}
