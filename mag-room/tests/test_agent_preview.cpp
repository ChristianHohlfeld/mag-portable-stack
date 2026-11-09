#include "mag_deadline.hpp"
#include "../native/agent_preview.hpp"
#include <cassert>
#include <filesystem>
#include <iostream>
#include <unistd.h>
int main(){
  mag_test_deadline(60); // mag-test-timeout: 60
  const std::string root="/tmp/mag-agent-preview-"+std::to_string((long)getpid());
  const std::string bot="qwen-numa0", path="/home/bot/site/index.html";
  assert(mag_preview::valid_bot(bot));
  assert(!mag_preview::valid_bot("../secrets"));
  assert(!mag_preview::valid_path("bad\npath"));
  assert(mag_preview::put(root,bot,path,"<html><script>console.error('x')</script></html>"));
  const std::string key=mag_preview::id(path);
  mag_preview::File file;
  assert(mag_preview::get(root,bot,key,file));
  assert(file.content.find("console.error('x')")!=std::string::npos);
  assert(file.path==path);
  assert(!mag_preview::get(root,"qwen-numa1",key,file));
  assert(mag_preview::put(root,bot,path,"<p>Aktuell</p>"));
  assert(mag_preview::get(root,bot,key,file));
  assert(file.content=="<p>Aktuell</p>"); // same stable URL, newest version
  assert(mag_preview::put(root,bot,path,"<p>Neue Version</p>",false,200));
  assert(mag_preview::put(root,bot,path,"<p>Alte Nachricht</p>",false,100));
  assert(mag_preview::get(root,bot,key,file));
  assert(file.content=="<p>Neue Version</p>");
  assert(mag_preview::url(bot,path)=="/agent-preview/"+bot+"/"+key);
  assert(mag_preview::instrument("<html><head></head><body></body></html>")
         .find("unhandledrejection")!=std::string::npos);
  std::filesystem::remove_all(root);
  std::cout<<"agent preview OK\n";
}
