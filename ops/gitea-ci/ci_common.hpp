#pragma once
#include "../../mag-room/native/common.hpp"
#include "../../mag-room/native/json.hpp"
#include <curl/curl.h>
#include <openssl/hmac.h>
#include <openssl/crypto.h>
#include <regex>
#include <fstream>
#include <filesystem>
#include <set>
namespace ci {
using mj::Val;
inline std::string read(const std::string &p) {std::ifstream f(p,std::ios::binary);return {std::istreambuf_iterator<char>(f),{}};}
inline void save(const std::string &p,const std::string &s) {std::ofstream f(p+".tmp",std::ios::binary);f<<s;f.close();if(!f||rename((p+".tmp").c_str(),p.c_str()))throw std::runtime_error("state write failed");}
inline bool sha(const std::string &s){return std::regex_match(s,std::regex("[0-9a-f]{40}"));}
inline std::string str(const Val &v,const std::string &k){auto p=v.find(k);return p?p->asStr():"";}
inline long num(const Val &v,const std::string &k){auto p=v.find(k);return p?(long)p->asNum():0;}
inline Val json(const std::string &s){Val v;if(!mj::parse(s,v))throw std::runtime_error("invalid API response");return v;}
inline std::string secret(const std::string &name){auto p=mag::env("CREDENTIALS_DIRECTORY","");auto s=mag::trim(read(p+"/"+name));if(s.empty()||s.find_first_of("\r\n")!=s.npos)throw std::runtime_error("credential unavailable");return s;}
struct Reply {long code=0;Val value;};
inline size_t receive(char *p,size_t a,size_t b,void *ctx){auto &s=*static_cast<std::string*>(ctx);size_t n=a*b;if(s.size()+n>32*1024*1024)return 0;s.append(p,n);return n;}
inline Reply request(const std::string &url,const std::string &token,const std::string &method="GET",const Val &body=Val(),long timeout=30) {
  auto c=curl_easy_init();if(!c)throw std::runtime_error("HTTP unavailable");std::string out,data=mj::dump(body);curl_slist *h=nullptr;
  h=curl_slist_append(h,"Accept: application/json");h=curl_slist_append(h,"Content-Type: application/json");h=curl_slist_append(h,"User-Agent: mag-single-repo-ci");
  if(!token.empty())h=curl_slist_append(h,("Authorization: token "+token).c_str());
  curl_easy_setopt(c,CURLOPT_URL,url.c_str());curl_easy_setopt(c,CURLOPT_HTTPHEADER,h);curl_easy_setopt(c,CURLOPT_CUSTOMREQUEST,method.c_str());
  curl_easy_setopt(c,CURLOPT_WRITEFUNCTION,receive);curl_easy_setopt(c,CURLOPT_WRITEDATA,&out);curl_easy_setopt(c,CURLOPT_CONNECTTIMEOUT,3L);if(timeout<0){curl_easy_setopt(c,CURLOPT_CONNECTTIMEOUT_MS,-timeout);curl_easy_setopt(c,CURLOPT_TIMEOUT_MS,-timeout);}else curl_easy_setopt(c,CURLOPT_TIMEOUT,timeout);
  curl_easy_setopt(c,CURLOPT_FOLLOWLOCATION,0L);curl_easy_setopt(c,CURLOPT_NOSIGNAL,1L);
  if(method!="GET"){curl_easy_setopt(c,CURLOPT_POSTFIELDS,data.c_str());curl_easy_setopt(c,CURLOPT_POSTFIELDSIZE,(long)data.size());}
  auto rc=curl_easy_perform(c);Reply r;curl_easy_getinfo(c,CURLINFO_RESPONSE_CODE,&r.code);curl_slist_free_all(h);curl_easy_cleanup(c);
  if(rc!=CURLE_OK)throw std::runtime_error("API transport failed");
  if(!out.empty()&&!mj::parse(out,r.value))throw std::runtime_error("API returned non-JSON");
  return r;
}
inline bool signature(const std::string &key,const std::string &body,const std::string &supplied) {
  unsigned char mac[EVP_MAX_MD_SIZE];unsigned n=0;HMAC(EVP_sha256(),key.data(),(int)key.size(),(const unsigned char*)body.data(),body.size(),mac,&n);
  const char *hex="0123456789abcdef";std::string expected="sha256=";for(unsigned i=0;i<n;++i){expected+=hex[mac[i]>>4];expected+=hex[mac[i]&15];}
  return supplied.size()==expected.size()&&CRYPTO_memcmp(expected.data(),supplied.data(),expected.size())==0;
}
inline bool idle_slots(const Val &slots) {
  if(!slots.isArr()||slots.asArr().empty())return false;
  for(const auto &s:slots.asArr()) {
    auto busy=s.find("is_processing"),state=s.find("state");
    if(busy&&busy->type==Val::BOOL){if(busy->asBool())return false;continue;}
    if(state&&state->type==Val::NUM){if(state->asNum()!=0)return false;continue;}
    return false;
  }return true;
}
inline std::string conclusion(const std::string &s){if(s=="success")return "success";if(s=="failure"||s=="cancelled"||s=="timed_out"||s=="action_required"||s=="skipped"||s=="neutral")return "failure";return "pending";}
} // namespace ci
