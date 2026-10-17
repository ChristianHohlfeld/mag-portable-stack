// MAG Pages publisher, compiled into magroom (no daemon, Python or Node).
// The unprivileged room account polls the local Gitea API every 60 seconds,
// stages immutable releases, and atomically swaps nginx webroot symlinks.
// nginx and the existing root-owned ACME timer are the only serving/TLS layer.
#pragma once
#include "json.hpp"
#include <archive.h>
#include <archive_entry.h>
#include <curl/curl.h>
#include <openssl/sha.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <unistd.h>

namespace magpages {
namespace fs = std::filesystem;
using mj::Val;
inline std::string env(const char* k, const std::string& fallback) {
  const char* v = ::getenv(k);
  return v && *v ? std::string(v) : fallback;
}
inline std::string trim(std::string s) {
  auto a=s.find_first_not_of(" \r\n\t"), b=s.find_last_not_of(" \r\n\t");
  return a==s.npos ? "" : s.substr(a,b-a+1);
}
inline std::string read(const fs::path& path) {
  std::ifstream f(path,std::ios::binary);
  if (!f) throw std::runtime_error("MAG Pages missing file: " + path.string());
  return {std::istreambuf_iterator<char>(f),{}};
}
inline void write_atomic(const fs::path& path,const std::string& value) {
  fs::create_directories(path.parent_path());
  auto tmp=path;
  tmp += ".tmp." + std::to_string(::getpid()) + "." + std::to_string(
    std::chrono::steady_clock::now().time_since_epoch().count());
  {
    std::ofstream f(tmp,std::ios::binary|std::ios::trunc);
    if (!f) throw std::runtime_error("MAG Pages cannot create temporary file");
    f.write(value.data(),static_cast<std::streamsize>(value.size()));
    if (!f) {fs::remove(tmp);throw std::runtime_error("MAG Pages write failed");}
  }
  fs::rename(tmp,path);
}
inline Val json(const std::string& data) {
  Val out;
  if (!mj::parse(data,out)) throw std::runtime_error("MAG Pages invalid JSON");
  return out;
}
inline std::string field(const Val& obj, const char* k) {
  const Val* p=obj.find(k);
  return p ? p->asStr() : "";
}
inline bool component(const std::string& s) {
  if (s.empty() || s.size()>120 || s=="." || s==".." || s.front()=='.') return false;
  for (unsigned char c:s) if (!std::isalnum(c) && c!='-' && c!='_' && c!='.') return false;
  return true;
}
inline bool dns(const std::string& s) {
  if (s.empty() || s.size()>253 || s.find('.')==s.npos || s.back()=='.') return false;
  size_t start=0;
  while(start<s.size()) {
    size_t end=s.find('.',start);
    if(end==s.npos) end=s.size();
    size_t n=end-start;
    if(n<1 || n>63 || s[start]=='-' || s[end-1]=='-') return false;
    for(size_t i=start;i<end;++i) {
      char c=s[i];
      if(!(c>='a'&&c<='z') && !(c>='0'&&c<='9') && c!='-') return false;
    }
    start=end+1;
  }
  return true;
}
inline std::string hash8(const std::string& input) {
  unsigned char hash[SHA256_DIGEST_LENGTH];
  SHA256(reinterpret_cast<const unsigned char*>(input.data()),input.size(),hash);
  const char* hex="0123456789abcdef";
  std::string out;
  for(int i=0;i<4;i++) {out+=hex[hash[i]>>4];out+=hex[hash[i]&15];}
  return out;
}
inline std::string slug(const std::string& full) {
  auto slash=full.find('/');
  if(slash==std::string::npos || full.find('/',slash+1)!=std::string::npos) throw std::runtime_error("invalid repo");
  std::string owner=full.substr(0,slash), repo=full.substr(slash+1);
  if(!component(owner)||!component(repo))throw std::runtime_error("invalid repo name");
  auto clean=[](const std::string& s) {
    std::string out;
    for(unsigned char c:s) {
      char l=static_cast<char>(std::tolower(c));
      if((l>='a'&&l<='z')||(l>='0'&&l<='9')||l=='-')out+=l;
      else out+='-';
    }
    return out;
  };
  auto out=clean(repo)+"--"+clean(owner);
  // Always suffix a stable hash when normalization could cause collisions.
  if(out.size()>54 || repo!=clean(repo)||owner!=clean(owner)) out=out.substr(0,53)+"-"+hash8(full);
  if(out.size()>63 || out.front()=='-' || out.back()=='-')throw std::runtime_error("invalid page slug");
  return out;
}
inline std::string encode(const std::string& s) {
  const char* h="0123456789ABCDEF";std::string out;
  for(unsigned char c:s) {
    if(std::isalnum(c)||c=='-'||c=='_'||c=='.'||c=='~')out+=static_cast<char>(c);
    else {out+='%';out+=h[c>>4];out+=h[c&15];}
  }
  return out;
}
struct Response {long status=0;std::string body;};
inline size_t append(char* ptr,size_t size,size_t n,void* ctx) {
  auto* body=static_cast<std::string*>(ctx);
  size_t count=size*n;
  if(count>32*1024*1024 || body->size()>32*1024*1024-count)return 0;
  body->append(ptr,count);return count;
}
inline Response request(const std::string& path,const std::string& token) {
  std::string base=env("MAG_PAGES_GITEA","http://127.0.0.1:3000");
  // Never permit credential forwarding, userinfo, arbitrary host or redirects.
  if(base!="http://127.0.0.1:3000" && base!="http://127.0.0.1:3001")
    throw std::runtime_error("MAG Pages Gitea endpoint must be approved loopback");
  CURL* c=curl_easy_init();
  if(!c)throw std::runtime_error("curl init failed");
  curl_slist* headers=nullptr;
  const std::string authorization="Authorization: token "+token;
  headers=curl_slist_append(headers,authorization.c_str());
  Response r;
  const std::string url=base+"/api/v1/"+path;
  curl_easy_setopt(c,CURLOPT_URL,url.c_str());
  curl_easy_setopt(c,CURLOPT_HTTPHEADER,headers);
  curl_easy_setopt(c,CURLOPT_FOLLOWLOCATION,0L);
  curl_easy_setopt(c,CURLOPT_CONNECTTIMEOUT,4L);
  curl_easy_setopt(c,CURLOPT_TIMEOUT,50L);
  curl_easy_setopt(c,CURLOPT_NOPROXY,"*");
  curl_easy_setopt(c,CURLOPT_WRITEFUNCTION,append);
  curl_easy_setopt(c,CURLOPT_WRITEDATA,&r.body);
  curl_easy_setopt(c,CURLOPT_USERAGENT,"magroom-pages-native/1");
  auto error=curl_easy_perform(c);
  curl_easy_getinfo(c,CURLINFO_RESPONSE_CODE,&r.status);
  curl_slist_free_all(headers);
  curl_easy_cleanup(c);
  if(error!=CURLE_OK)throw std::runtime_error("Gitea request failed/size cap");
  if(r.status!=200 && r.status!=404)throw std::runtime_error("Gitea HTTP "+std::to_string(r.status));
  return r;
}
inline std::map<std::string,std::string> unpack(const std::string& data,bool docs) {
  archive* ar=archive_read_new();
  if(!ar)throw std::runtime_error("archive allocator failed");
  archive_read_support_filter_gzip(ar);
  archive_read_support_format_tar(ar);
  if(archive_read_open_memory(ar,data.data(),data.size())!=ARCHIVE_OK){
    archive_read_free(ar);throw std::runtime_error("invalid archive");
  }
  std::map<std::string,std::string> files;
  size_t total=0,count=0;
  try {
    archive_entry* entry=nullptr;
    int rc;
    while((rc=archive_read_next_header(ar,&entry))==ARCHIVE_OK) {
      const char* raw=archive_entry_pathname(entry);
      if(!raw)throw std::runtime_error("invalid archive path");
      std::string src(raw);
      auto slash=src.find('/');
      if(slash==src.npos) {archive_read_data_skip(ar);continue;}
      std::string path=src.substr(slash+1);
      if(docs) {
        if(path.rfind("docs/",0)!=0){archive_read_data_skip(ar);continue;}
        path.erase(0,5);
      }
      if(path.empty()){archive_read_data_skip(ar);continue;}
      fs::path rel(path);
      if(rel.is_absolute() || path.find('\\')!=std::string::npos || path.find('\0')!=std::string::npos)
        throw std::runtime_error("unsafe archive filename");
      bool safe=true;
      for(const auto& part:rel) {
        auto s=part.string();
        if(s.empty()||s=="."||s==".."||s[0]=='.')safe=false;
      }
      if(!safe){archive_read_data_skip(ar);continue;}
      if(archive_entry_filetype(entry)==AE_IFDIR)continue;
      if(archive_entry_filetype(entry)!=AE_IFREG || archive_entry_hardlink(entry)!=nullptr || archive_entry_symlink(entry)!=nullptr)
        throw std::runtime_error("archive link/special file rejected");
      auto n=archive_entry_size(entry);
      if(n<0 || n>20*1024*1024 || ++count>10000 || total+static_cast<size_t>(n)>100*1024*1024)
        throw std::runtime_error("site size/file limit");
      std::string body(static_cast<size_t>(n),'\0');
      size_t pos=0;
      while(pos<body.size()){
        la_ssize_t got=archive_read_data(ar,&body[pos],body.size()-pos);
        if(got<=0)throw std::runtime_error("truncated archive entry");
        pos+=static_cast<size_t>(got);
      }
      if(!files.emplace(rel.generic_string(),std::move(body)).second)
        throw std::runtime_error("duplicate archive path");
      total+=static_cast<size_t>(n);
    }
    if(rc!=ARCHIVE_EOF)throw std::runtime_error("invalid tar stream");
  }catch(...){archive_read_free(ar);throw;}
  archive_read_free(ar);
  return files;
}
inline std::string custom(std::map<std::string,std::string>& files) {
  auto it=files.find("CNAME");
  if(it==files.end())return "";
  auto host=trim(it->second);
  files.erase(it);
  if(host.size()>253)throw std::runtime_error("CNAME too long");
  std::transform(host.begin(),host.end(),host.begin(),[](unsigned char c){return std::tolower(c);});
  if(!dns(host))throw std::runtime_error("invalid CNAME");
  return host;
}
inline Val policy(const fs::path& file) {
  if(!fs::exists(file))return Val::object();
  auto v=json(read(file));
  if(!v.isObj())throw std::runtime_error("visibility policy must be an object");
  for(const auto& item:*v.obj)
    if(item.second.asStr()!="tailnet" && item.second.asStr()!="public")
      throw std::runtime_error("invalid visibility policy value");
  return v;
}
inline std::string mode(const Val& p,const std::string& repo) {
  auto v=p.find(repo);
  return v?v->asStr():"tailnet";
}
inline void readable_dir(const fs::path& p) {
  fs::create_directories(p);
  fs::permissions(p,fs::perms::owner_all|fs::perms::group_read|fs::perms::group_exec|
                      fs::perms::others_read|fs::perms::others_exec,fs::perm_options::replace);
}
inline void materialize(const fs::path& dest,const std::map<std::string,std::string>& files) {
  readable_dir(dest);
  for(const auto& item:files) {
    fs::path target=dest/item.first;
    fs::path current=dest;
    for(const auto& part:fs::path(item.first).parent_path()) {
      current/=part;
      readable_dir(current);
    }
    std::ofstream f(target,std::ios::binary|std::ios::trunc);
    if(!f)throw std::runtime_error("site write failed");
    f.write(item.second.data(),static_cast<std::streamsize>(item.second.size()));
    if(!f)throw std::runtime_error("site write failed");
    fs::permissions(target,fs::perms::owner_read|fs::perms::group_read|fs::perms::others_read);
  }
}
inline void link_atomic(const fs::path& directory,const std::string& name,const fs::path& target) {
  readable_dir(directory);
  const fs::path link=directory/name, temp=directory/(name+".new-"+std::to_string(::getpid()));
  fs::remove(temp);
  fs::create_directory_symlink(target,temp);
  fs::rename(temp,link);
}
struct Site {std::string id,slug,sha,branch,custom,access;fs::path content;};
inline std::vector<Val> repo_list(const std::string& token) {
  std::vector<Val> out;
  std::set<int64_t> ids;
  for(int page=1;page<=200;++page){
    const auto r=request("repos/search?limit=50&page="+std::to_string(page),token);
    if(r.status!=200)throw std::runtime_error("Gitea repo list 404");
    auto v=json(r.body);
    auto data=v.find("data");
    if(!data || !data->isArr())throw std::runtime_error("malformed Gitea repo list");
    for(const auto& row:data->asArr()) {
      const auto id=row.find("id");
      if(!id)throw std::runtime_error("missing repo id");
      int64_t num=static_cast<int64_t>(id->asNum());
      if(ids.insert(num).second)out.push_back(row);
    }
    if(data->size()<50)return out;
  }
  throw std::runtime_error("too many repositories: no partial scan");
}
inline std::pair<std::string,std::string> branch_sha(const std::string& token,const std::string& owner,
                                                     const std::string& name,const std::string& fallback) {
  std::string prefix="repos/"+encode(owner)+"/"+encode(name)+"/branches/";
  auto r=request(prefix+encode("gh-pages"),token);
  std::string branch="gh-pages";
  if(r.status==404) {branch=fallback;r=request(prefix+encode(branch),token);}
  if(r.status!=200)throw std::runtime_error("missing default branch");
  auto v=json(r.body);
  auto commit=v.find("commit");
  if(!commit)throw std::runtime_error("Gitea branch has no commit");
  auto sha=field(*commit,"id");
  if(sha.size()!=40 || sha.find_first_not_of("0123456789abcdefABCDEF")!=std::string::npos)
    throw std::runtime_error("invalid Gitea commit hash");
  return {branch,sha};
}
inline void reconcile(const fs::path& root,const std::string& token) {
  const Val access=policy(env("MAG_PAGES_VISIBILITY","/etc/mag-pages/visibility.json"));
  // Host public bind + explicit per-repo policy are BOTH required.
  const bool public_enabled=!env("MAG_PAGES_PUBLIC_BIND","").empty();
  const auto repositories=repo_list(token);
  const fs::path manifest=root/"sites.json";
  if(repositories.empty() && fs::exists(manifest))
    throw std::runtime_error("empty repo scan: refuse to unpublish existing Pages");
  Val prior=Val::object();
  if(fs::exists(manifest))prior=json(read(manifest));
  std::vector<Site> sites;
  std::set<std::string> slugs,domains;
  Val state=Val::object();
  std::string aliases;
  const std::string zone=env("MAG_PAGES_DOMAIN","pages.stardenburg.de");
  if(!dns(zone))throw std::runtime_error("bad Pages domain");
  for(const auto& repo:repositories) {
    const std::string full=field(repo,"full_name");
    auto slash=full.find('/');
    if(slash==std::string::npos)throw std::runtime_error("invalid Gitea repo");
    const auto owner=full.substr(0,slash),name=full.substr(slash+1);
    const auto page=slug(full);
    if(!slugs.insert(page).second)throw std::runtime_error("Pages slug collision");
    const auto branch=branch_sha(token,owner,name,field(repo,"default_branch"));
    const bool docs=branch.first!="gh-pages";
    const auto prev=prior.find(full);
    std::string cname;
    const fs::path release=root/"releases"/page/branch.second;
    if(prev && field(*prev,"sha")==branch.second && field(*prev,"branch")==branch.first
       && fs::is_directory(release)) {
      cname=field(*prev,"custom");
    }else{
      auto r=request("repos/"+encode(owner)+"/"+encode(name)+"/archive/"+encode(branch.first)+".tar.gz",token);
      if(r.status!=200)throw std::runtime_error("Gitea archive not found");
      auto files=unpack(r.body,docs);
      if(files.empty())continue; // no docs: do not publish
      cname=custom(files);
      if(files.empty())continue;
      if(!fs::exists(release)){
        readable_dir(release.parent_path());
        auto stage=release.parent_path()/(".stage-"+std::to_string(::getpid()));
        fs::remove_all(stage);
        materialize(stage,files);
        fs::rename(stage,release);
      }
    }
    if(!cname.empty()) {
      if(cname==zone || (cname.size()>zone.size() &&
                         cname.compare(cname.size()-zone.size()-1,zone.size()+1,"."+zone)==0) ||
         !domains.insert(cname).second)
        throw std::runtime_error("unsafe/duplicate CNAME");
    }
    const auto requested=mode(access,full);
    Site s{full,page,branch.second,branch.first,cname,
           public_enabled && requested=="public" ? "public" : "tailnet",release};
    Val v=Val::object();
    v["sha"]=s.sha;v["branch"]=s.branch;v["custom"]=s.custom;v["slug"]=s.slug;v["access"]=s.access;
    state[full]=v;
    if(!cname.empty() && s.access=="public"){
      aliases += cname+"\t"+page+"."+zone+"\t"+page+"\n";
    }
    sites.push_back(std::move(s));
  }
  // All source archives, policy and conflicts checked BEFORE changing served sites.
  fs::create_directories(root/"public");
  fs::create_directories(root/"tailnet");
  fs::create_directories(root/"custom");
  std::set<std::string> active,visible,custom_active;
  for(const auto& site:sites){
    active.insert(site.slug);
    link_atomic(root/"tailnet",site.slug,site.content);
    if(site.access=="public"){
      visible.insert(site.slug);
      link_atomic(root/"public",site.slug,site.content);
      if(!site.custom.empty()) {
        custom_active.insert(site.custom);
        link_atomic(root/"custom",site.custom,site.content);
      }
    }
  }
  // Remove stale routes; only known symlink names are touched. Nothing is
  // recursively removed, so immutable published content remains for rollback.
  for(const auto& which:{std::string("public"),std::string("tailnet"),std::string("custom")}){
    auto dir=root/which;
    for(const auto& e:fs::directory_iterator(dir)){
      if(!e.is_symlink())continue;
      auto name=e.path().filename().string();
      const auto& keep = which=="public"?visible:(which=="tailnet"?active:custom_active);
      if(keep.count(name)==0)fs::remove(e.path());
    }
  }
  write_atomic(root/"custom-domains.tsv",aliases);
  write_atomic(manifest,mj::dump(state)+"\n");
}
inline void ticker(const std::atomic<bool>& stopping) {
  const auto root=fs::path(env("MAG_PAGES_ROOT","/var/lib/mag-pages"));
  const auto tokenfile=fs::path(env("MAG_PAGES_TOKEN_FILE","/etc/mag-pages/gitea-token"));
  if(curl_global_init(CURL_GLOBAL_DEFAULT)!=CURLE_OK){
    std::cerr<<"MAG Pages: curl init failed\n";return;
  }
  std::cerr<<"MAG Pages: native C++ polling enabled (nginx webroots)\n";
  while(!stopping.load()){
    try{
      const auto token=trim(read(tokenfile));
      if(token.empty() || token.find_first_of("\r\n")!=std::string::npos)
        throw std::runtime_error("missing/invalid Gitea service token");
      reconcile(root,token);
    }catch(const std::exception& e){
      std::cerr<<"MAG Pages sync failed, last webroots kept: "<<e.what()<<"\n";
    }
    for(int t=0;t<60 && !stopping.load();++t)
      std::this_thread::sleep_for(std::chrono::seconds(1));
  }
  curl_global_cleanup();
}
} // namespace magpages
