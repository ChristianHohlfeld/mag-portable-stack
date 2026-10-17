// Private model-switch client for magroom. Only the room's owner-facing route
// can invoke PUT; the Unix socket is mode 0660 and restricted to group magbot.
#pragma once
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <cerrno>
static Val model_switch_request(const std::string &method,const std::string &payload=""){
  Val out=Val::object();
  const std::string path=env("MAG_MODEL_CONTROL_SOCKET","/run/mag-model-switch/control.sock");
  if(path.size()>=sizeof(sockaddr_un::sun_path)){out["error"]="Model control unavailable";return out;}
  int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);
  if(fd<0){out["error"]="Model control unavailable";return out;}
  timeval tv{2,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
  sockaddr_un a{};a.sun_family=AF_UNIX;strncpy(a.sun_path,path.c_str(),sizeof(a.sun_path)-1);
  if(connect(fd,(sockaddr*)&a,sizeof(a))!=0){close(fd);out["error"]="Model control unavailable";return out;}
  std::string req=method+" /models HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: "+
      std::to_string(payload.size())+"\r\n\r\n"+payload;
  size_t n=0;while(n<req.size()){ssize_t k=send(fd,req.data()+n,req.size()-n,MSG_NOSIGNAL);if(k<=0){close(fd);out["error"]="Model control unavailable";return out;}n+=(size_t)k;}
  std::string raw;char buf[4096];
  for(;;){ssize_t k=recv(fd,buf,sizeof(buf),0);if(k<=0)break;raw.append(buf,(size_t)k);if(raw.size()>65536)break;}
  close(fd);
  auto p=raw.find("\r\n\r\n");if(p==std::string::npos||raw.size()>65536){out["error"]="Model control unavailable";return out;}
  Val v;if(!mj::parse(raw.substr(p+4),v)||!v.isObj()){out["error"]="Model control unavailable";return out;}
  if(raw.rfind("HTTP/1.1 200",0)!=0&&raw.rfind("HTTP/1.1 202",0)!=0){if(!v.find("error"))v["error"]="Model control failed";}
  return v;
}
static std::string model_variant_cached() {
  Val v=model_switch_request("GET");
  const Val *a=v.find("active");
  return a?a->asStr():"";
}
