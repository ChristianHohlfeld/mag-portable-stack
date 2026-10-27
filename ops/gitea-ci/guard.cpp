#include "ci_common.hpp"
#include <thread>
#include <chrono>
#include <csignal>
#include <iostream>
static volatile sig_atomic_t running=1;
int main(int argc,char **argv) {
  if(argc>1&&std::string(argv[1])=="--test")return ci::idle_slots(ci::json(ci::read(argv[2])))?0:1;
  signal(SIGTERM,[](int){running=0;});signal(SIGINT,[](int){running=0;});
  // Freeze through systemd's D-Bus-facing CLI, never by writing to an LLM cgroup.
  bool frozen=true;auto last=std::chrono::steady_clock::now();
  while(running) {
    bool idle=true;
    for(int port:{8191,8192})try {auto r=ci::request("http://127.0.0.1:"+std::to_string(port)+"/slots","","GET",mj::Val(),-150);idle=idle&&r.code==200&&ci::idle_slots(r.value);}catch(...){idle=false;}
    auto now=std::chrono::steady_clock::now();
    if(!idle)last=now;
    // A quiet interval prevents freeze/thaw churn between tool rounds.
    bool freeze=!idle||now-last<std::chrono::seconds(5);
    if(freeze!=frozen) {
      auto r=mag::run({"systemctl",freeze?"freeze":"thaw","mag-ci-workload.slice"},3);
      if(r.code){mag::run({"systemctl","stop","mag-ci-vm.service"},5);}
      else frozen=freeze;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
  }
  // Fail closed: stopping/restarting this guard never lets CPU work continue.
  (void)frozen;mag::run({"systemctl","freeze","mag-ci-workload.slice"},3);
}
