#include "ci_common.hpp"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <poll.h>
#include <unistd.h>
#include <atomic>
#include <thread>
#include <chrono>
#include <iostream>
#include <csignal>
using ci::Val;
static std::atomic<bool> alive{true},wake{true};
static const std::string upstream="ChristianHohlfeld/mag-portable-stack";
struct Bridge {
  Val policy,state=Val::object();std::string dir,gh,gt,hook,ga,ta,repo,control;
  std::vector<std::string> contexts;
  Bridge(const std::string &p,const std::string &d):policy(ci::json(ci::read(p))),dir(d) {
    if(ci::str(policy,"github_repository")!=upstream||ci::str(policy,"gitea_repository")!="mag-ci/mag-portable-stack"||ci::str(policy,"control_ref")!="mag-ci/control")throw std::runtime_error("repository policy mismatch");
    ga=ci::str(policy,"github_api");ta=ci::str(policy,"gitea_api");repo=ci::str(policy,"gitea_repository");control=ci::str(policy,"control_ref");
    if(!mag::env("MAG_CI_TEST","").empty()){}else if(ga!="https://api.github.com"||ta!="http://127.0.0.1:3000/api/v1")throw std::runtime_error("API origin mismatch");
    gh=ci::secret("github");gt=ci::secret("gitea");hook=ci::secret("webhook");
    auto saved=ci::read(dir+"/state.json");if(!saved.empty())state=ci::json(saved);
    auto c=policy.find("contexts");if(!c||!c->isArr())throw std::runtime_error("missing contexts");
    for(const auto &v:c->asArr())contexts.push_back(v.asStr());
    const std::set<std::string> required={"native","pages","nginx","recovery","room","chromium","speech","e2e-bootstrap","gitleaks","secrets"};
    if(std::set<std::string>(contexts.begin(),contexts.end())!=required||contexts.size()!=required.size())throw std::runtime_error("check policy mismatch");
  }
  ci::Reply github(const std::string &p,const std::string &m="GET",const Val &b=Val()){return ci::request(ga+"/repos/"+upstream+p,gh,m,b);}
  ci::Reply gitea(const std::string &p,const std::string &m="GET",const Val &b=Val()){return ci::request(ta+"/repos/"+repo+p,gt,m,b);}
  void persist(){ci::save(dir+"/state.json",mj::dump(state));}
  void git(const std::vector<std::string> &args){auto cmd=std::vector<std::string>{"git","-c","pack.threads=1","-c","pack.windowMemory=64m","-c","core.deltaBaseCacheLimit=32m","-C",dir+"/mirror.git"};cmd.insert(cmd.end(),args.begin(),args.end());if(mag::run(cmd,180).code)throw std::runtime_error("mirror operation failed");}
  void mirror(const std::string &ref,const std::string &sha) {
    if(!ci::sha(sha)||(!std::regex_match(ref,std::regex("refs/heads/[A-Za-z0-9_./-]+"))&&!std::regex_match(ref,std::regex("refs/pull/[0-9]+/head")))||ref.find("..")!=ref.npos||ref.find("refs/heads/mag-ci/")==0)throw std::runtime_error("invalid source ref");
    if(mag::env("MAG_CI_TEST","").empty()) {
      if(!std::filesystem::exists(dir+"/mirror.git/HEAD"))if(mag::run({"git","init","--bare",dir+"/mirror.git"}).code)throw std::runtime_error("mirror init failed");
      git({"-c","credential.helper=","-c","core.askPass=/usr/local/lib/mag-ci/git-askpass.sh","fetch","--no-tags","--force","https://github.com/"+upstream+".git",ref+":refs/ci/fetched"});
      auto actual=mag::run({"git","-C",dir+"/mirror.git","rev-parse","refs/ci/fetched"});if(actual.code||mag::trim(actual.out)!=sha)throw std::runtime_error("source moved; retry reconciliation");
      std::string target=ref.rfind("refs/heads/",0)==0?ref:"refs/ci/pulls/"+ref.substr(10,ref.size()-15);
      git({"-c","credential.helper=","-c","core.askPass=/usr/local/lib/mag-ci/git-askpass.sh","push","--atomic","http://127.0.0.1:3000/"+repo+".git","+"+sha+":"+target,"+"+sha+":refs/ci/source/"+sha});
    }
  }
  void status(const std::string &sha,const std::string &context,const std::string &s,const std::string &url) {
    auto &sent=state[sha]["statuses"];if(ci::str(sent,context)==s)return;
    Val b=Val::object();b["state"]=s;b["context"]=context;b["description"]=s=="pending"?"Gitea/MAG: queued or running":s=="success"?"Gitea/MAG: passed":"Gitea/MAG: failed or incomplete";b["target_url"]=url;
    if(github("/statuses/"+sha,"POST",b).code!=201)throw std::runtime_error("GitHub status rejected");sent[context]=s;persist();
  }
  void enqueue(const std::string &sha,const std::string &ref,const std::string &base) {
    if(!ci::sha(sha)||!ci::sha(base))throw std::runtime_error("invalid commit");
    auto known=state.find(sha);if(known&&ci::num(*known,"run_id")>0)return;
    if(known&&known->find("dispatch_pending")) {
      auto runs=gitea("/actions/workflows/mag-ci.yml/runs?limit=100");
      auto list=runs.value.find("workflow_runs");
      if(runs.code==200&&list&&list->isArr())for(const auto &run:list->asArr())
        if(ci::str(run,"display_title")=="MAG CI "+sha&&ci::str(run,"head_sha")==ci::str(*known,"control_sha")&&ci::str(run,"event")=="workflow_dispatch") {
          state[sha]["run_id"]=ci::num(run,"id");state[sha]["dispatch_pending"]=false;persist();return;
        }
    }
    size_t pending=0;for(const auto &kv:*state.obj)if(ci::sha(kv.first)&&!kv.second.find("finished"))++pending;
    if(!known&&pending>=8)return;
    mirror(ref,sha);
    auto branch=gitea("/branches/mag-ci%2Fcontrol");if(branch.code!=200)throw std::runtime_error("control workflow unavailable");
    auto c=branch.value.find("commit");const auto control_sha=c?ci::str(*c,"id"):"";if(!ci::sha(control_sha))throw std::runtime_error("invalid control commit");
    auto &entry=state[sha];entry["ref"]=ref;entry["control_sha"]=control_sha;
    const auto url="https://git.mag.stardenburg.de/"+repo+"/actions";
    for(const auto &ctx:contexts)status(sha,ctx,"pending",url);
    // Record before dispatch. If a response is lost, recovery reconciles the
    // unique run title; it never treats an unrelated run as proof of success.
    Val b=Val::object(),inputs=Val::object();inputs["sha"]=sha;inputs["base_sha"]=base;b["ref"]=control;b["inputs"]=inputs;
    entry["dispatch_pending"]=true;persist();
    auto r=gitea("/actions/workflows/mag-ci.yml/dispatches?return_run_details=true","POST",b);
    if(r.code!=200||ci::num(r.value,"workflow_run_id")<=0)throw std::runtime_error("Gitea dispatch failed");
    entry["run_id"]=ci::num(r.value,"workflow_run_id");entry["dispatch_pending"]=false;persist();
  }
  void poll(const std::string &sha) {
    auto &entry=state[sha];long id=ci::num(entry,"run_id");if(id<=0)return;
    auto r=gitea("/actions/runs/"+std::to_string(id)+"/jobs?limit=100");if(r.code!=200)throw std::runtime_error("Gitea job lookup failed");
    auto jobs=r.value.find("jobs");if(!jobs||!jobs->isArr()||jobs->size()!=1)return;
    const auto &job=jobs->asArr()[0];
    if(ci::num(job,"run_id")!=id||ci::str(job,"head_sha")!=ci::str(entry,"control_sha")||ci::str(job,"name")!="checks")throw std::runtime_error("job identity mismatch");
    auto labels=job.find("labels");if(!labels||!labels->isArr()||labels->size()!=1||labels->asArr()[0].asStr()!="mag-ci-vm")throw std::runtime_error("worker identity mismatch");
    std::map<std::string,std::vector<Val>> steps;auto a=job.find("steps");if(a&&a->isArr())for(const auto &s:a->asArr())steps[ci::str(s,"name")].push_back(s);
    bool all=true,finished=ci::str(job,"status")=="completed";const auto url="https://git.mag.stardenburg.de/"+repo+"/actions/runs/"+std::to_string(id);
    for(const auto &ctx:contexts) {
      const auto &matches=steps[ctx];std::string result="pending";
      if(matches.size()==1) {result=ci::conclusion(ci::str(matches[0],"conclusion"));if(ci::str(matches[0],"status")!="completed")result="pending";}
      if(finished&&result=="pending")result="failure";
      status(sha,ctx,result,url);all=all&&result=="success";
    }
    if(!finished) {if(ci::str(job,"status")=="queued"){std::ofstream request(dir+"/../request");request<<id<<"\n";if(!request)throw std::runtime_error("worker request failed");}return;}
    if(all&&ci::str(job,"conclusion")=="success")entry["passed"]=true;
    entry["finished"]=true;persist();
  }
  void promote(const std::string &sha) {
    auto found=state.find(sha);if(!found||!found->find("passed")||found->find("promoted"))return;
    auto &entry=state[sha];
    {
      auto head=github("/git/ref/heads/main");auto object=head.value.find("object");if(head.code!=200||!object||ci::str(*object,"sha")!=sha)return;
      Val body=Val::object();body["sha"]=sha;body["force"]=false;
      auto old=github("/git/ref/heads/green");ci::Reply promoted;
      if(old.code==404){body["ref"]="refs/heads/green";promoted=github("/git/refs","POST",body);}else if(old.code==200)promoted=github("/git/refs/heads/green","PATCH",body);else throw std::runtime_error("green lookup failed");
      if(promoted.code!=200&&promoted.code!=201)throw std::runtime_error("green promotion rejected");
      entry["promoted"]=true;persist();
    }


  }
  void reconcile() {
    auto main=github("/branches/main");auto c=main.value.find("commit");std::string main_sha=c?ci::str(*c,"sha"):"";if(main.code!=200||!ci::sha(main_sha))throw std::runtime_error("GitHub main unavailable");
    enqueue(main_sha,"refs/heads/main",main_sha);
    for(int page=1;page<=10;++page) {
      auto prs=github("/pulls?state=open&sort=updated&direction=desc&per_page=100&page="+std::to_string(page));if(prs.code!=200||!prs.value.isArr())throw std::runtime_error("GitHub PR lookup failed");
      for(const auto &p:prs.value.asArr()) {auto h=p.find("head"),b=p.find("base");if(!h||!b)continue;auto number=ci::num(p,"number");if(number<=0)continue;enqueue(ci::str(*h,"sha"),"refs/pull/"+std::to_string(number)+"/head",ci::str(*b,"sha"));}
      if(prs.value.size()<100)break;
    }
    for(int page=1;page<=10;++page) {
      auto branches=github("/branches?per_page=100&page="+std::to_string(page));
      if(branches.code!=200||!branches.value.isArr())throw std::runtime_error("branch mirror unavailable");
      for(const auto &b:branches.value.asArr()) {
        auto c=b.find("commit");const auto name=ci::str(b,"name");if(name=="main"||name.rfind("mag-ci/",0)==0||!c)continue;
        const auto sha=ci::str(*c,"sha"),previous=ci::str(state["_branches"],name);
        if(previous!=sha) {
          mirror("refs/heads/"+name,sha);
          if(!previous.empty()||state.find("_initialized"))state["_waiting"][name]=sha;
          state["_branches"][name]=sha;persist();
        }
      }
      if(branches.value.size()<100)break;
    }
    state["_initialized"]=true;
    auto waiting_copy=state["_waiting"];
    if(waiting_copy.isObj())for(const auto &kv:*waiting_copy.obj) {
      auto checked=state.find(kv.second.asStr());if(!checked||ci::num(*checked,"run_id")==0)enqueue(kv.second.asStr(),"refs/heads/"+kv.first,main_sha);
    }
    persist();
    std::vector<std::string> ids;for(const auto &kv:*state.obj)if(ci::sha(kv.first)&&!kv.second.find("finished"))ids.push_back(kv.first);
    for(const auto &s:ids)poll(s);
    promote(main_sha);
  }
};
static void hooks(const std::string &secret) {
  int fd=socket(AF_INET,SOCK_STREAM,0),yes=1;setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof yes);sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=htons(3011);inet_pton(AF_INET,"127.0.0.1",&a.sin_addr);
  if(bind(fd,(sockaddr*)&a,sizeof a)||listen(fd,16)){close(fd);return;}
  while(alive) {
    pollfd p{fd,POLLIN,0};if(::poll(&p,1,1000)<=0)continue;int client=accept(fd,nullptr,nullptr);if(client<0)continue;
    timeval timeout{3,0};setsockopt(client,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof timeout);
    std::string raw;char b[8192];size_t split=raw.npos,length=0;bool valid=false;
    while(raw.size()<2*1024*1024) {int n=recv(client,b,sizeof b,0);if(n<=0)break;raw.append(b,n);split=raw.find("\r\n\r\n");if(split==raw.npos)continue;
      auto headers=raw.substr(0,split);std::smatch m;if(!std::regex_search(headers,m,std::regex("[Cc]ontent-[Ll]ength: ([0-9]+)")))break;if(m[1].length()>7)break;length=std::stoul(m[1]);if(length>2*1024*1024)break;if(raw.size()<split+4+length)continue;
      std::smatch sig;const auto body=raw.substr(split+4,length);
      if(raw.rfind("POST /github HTTP/1.",0)==0&&std::regex_search(headers,sig,std::regex("[Xx]-[Hh]ub-[Ss]ignature-256: (sha256=[0-9a-f]{64})"))&&ci::signature(secret,body,sig[1]))try {auto event=ci::json(body);auto repo=event.find("repository");valid=repo&&ci::str(*repo,"full_name")==upstream;}catch(...){}
      break;
    }
    if(valid)wake=true;const char *response=valid?"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n":"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";send(client,response,strlen(response),MSG_NOSIGNAL);close(client);
  }close(fd);
}
int main(int argc,char **argv) {
  signal(SIGTERM,[](int){alive=false;});signal(SIGINT,[](int){alive=false;});
  if(argc!=3){std::cerr<<"usage: mag-ci-bridge policy.json state-dir\n";return 64;}
  try {Bridge b(argv[1],argv[2]);if(!mag::env("MAG_CI_ONCE","").empty()){b.reconcile();return 0;}
    std::thread listener(hooks,b.hook);auto next=std::chrono::steady_clock::now();
    while(alive) {auto now=std::chrono::steady_clock::now();if(wake.exchange(false)||now>=next){try{b.reconcile();}catch(...){std::cerr<<"CI reconciliation failed; no success/promotion inferred\n";}next=now+std::chrono::seconds(30);}std::this_thread::sleep_for(std::chrono::milliseconds(200));}
    listener.join();return 0;
  }catch(...){std::cerr<<"CI bridge unavailable (configuration/API/credentials); details suppressed\n";return 1;}
}
