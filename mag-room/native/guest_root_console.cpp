// Root terminal is an ephemeral systemd service INSIDE ONE MAG GUEST.
// QEMU guest-agent (host root) is the sole privileged creator. The bot
// receives a Unix socket, never sudoers entries, passwords or setuid files.
// systemctl stop kills the complete service cgroup and its PTY children.
#include "guest_pty.hpp"
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <poll.h>
#include <pwd.h>
#include <sys/socket.h>
#include <sys/un.h>

static constexpr const char *SOCK = "/run/mag-root-shell.sock";
static volatile sig_atomic_t running = 1;
static void stop(int) { running = 0; }

int main() {
  signal(SIGTERM, stop);
  signal(SIGINT, stop);
  auto pw = getpwnam("bot");
  if (!pw) return 2;
  const uid_t bot_uid = pw->pw_uid;
  const gid_t bot_gid = pw->pw_gid;
  int server = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (server < 0) return 3;
  unlink(SOCK);
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  strncpy(addr.sun_path, SOCK, sizeof(addr.sun_path)-1);
  if (bind(server, reinterpret_cast<sockaddr *>(&addr),sizeof(addr)) != 0 ||
      chown(SOCK,bot_uid,bot_gid) != 0 || chmod(SOCK,0600) != 0 ||
      listen(server,1) != 0) {
    close(server);unlink(SOCK);return 4;
  }
  GuestPTY shell;
  shell.start(100,30);
  int client=-1;
  while(running){
    pollfd p[3]{{server,POLLIN,0},{shell.fd(),POLLIN,0},{client,POLLIN,0}};
    int count=poll(p,3,100);
    if(count<0&&errno==EINTR)continue;
    if(count<0)break;
    if(p[0].revents&POLLIN){
      int next=accept4(server,nullptr,nullptr,SOCK_NONBLOCK|SOCK_CLOEXEC);
      if(next>=0){
        struct ucred peer{};socklen_t size=sizeof(peer);
        if(getsockopt(next,SOL_SOCKET,SO_PEERCRED,&peer,&size)!=0 ||
           peer.uid!=bot_uid){close(next);}
        else{
          if(client>=0)close(client);
          client=next;
          const char notice[]="\r\n[MAG VM root shell · temporary access]\r\n";
          (void)send(client,std::string("O")+std::string(notice,sizeof(notice)-1),MSG_NOSIGNAL|MSG_DONTWAIT);
        }
      }
    }
    if(client>=0&&(p[2].revents&(POLLIN|POLLHUP|POLLERR))){
      unsigned char input[600];ssize_t n=recv(client,input,sizeof(input),MSG_DONTWAIT);
      if(n>1 && input[0]=='I')shell.input(std::string((char *)input+1,(size_t)n-1));
      else if(n>2 && input[0]=='R'){
        unsigned cols=0,rows=0;
        if(sscanf((const char *)input+1,"%u:%u",&cols,&rows)==2)
          shell.resize(cols,rows);
      }else if(n==0||(n<0&&errno!=EAGAIN)){close(client);client=-1;}
    }
    if(p[1].revents&(POLLIN|POLLHUP|POLLERR)){
      for(int i=0;i<16;i++){
        std::string output=shell.output();
        if(output.empty())break;
        if(client>=0&&send(client,std::string("O")+output,
              MSG_DONTWAIT|MSG_NOSIGNAL)<0&&errno!=EAGAIN){
          close(client);client=-1;
        }
      }
    }
  }
  if(client>=0)close(client);
  close(server);unlink(SOCK);
  // systemd's KillMode=control-group also reaps every privileged child.
  return 0;
}
