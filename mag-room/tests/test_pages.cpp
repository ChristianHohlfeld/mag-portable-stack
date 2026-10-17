#include "../native/pages.hpp"
using Val=mj::Val;
#include "../native/pages_tools.hpp"
#include <cassert>
#include <stdexcept>
#define REQUIRE(expression) do { if (!(expression)) throw std::runtime_error("REQUIRE failed: " #expression); } while (0)
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <functional>
#include <archive.h>
#include <archive_entry.h>
#include <sys/stat.h>

static std::string make_tar(const std::vector<std::pair<std::string,int>>& entries) {
  std::string buffer(4*1024*1024,'\0');
  size_t used=0;
  archive* a=archive_write_new();
  archive_write_set_format_pax_restricted(a);
  archive_write_add_filter_gzip(a);
  REQUIRE(archive_write_open_memory(a,buffer.data(),buffer.size(),&used)==ARCHIVE_OK);
  for(const auto& [path,kind]:entries) {
    archive_entry* entry=archive_entry_new();
    archive_entry_set_pathname(entry,path.c_str());
    if(kind==AE_IFLNK) {
      archive_entry_set_filetype(entry,AE_IFLNK);
      archive_entry_set_symlink(entry,"/etc/passwd");
      archive_entry_set_size(entry,0);
    }else{
      archive_entry_set_filetype(entry,AE_IFREG);
      archive_entry_set_perm(entry,0644);
      archive_entry_set_size(entry,5);
    }
    REQUIRE(archive_write_header(a,entry)==ARCHIVE_OK);
    if(kind!=AE_IFLNK) REQUIRE(archive_write_data(a,"hello",5)==5);
    archive_entry_free(entry);
  }
  REQUIRE(archive_write_close(a)==ARCHIVE_OK);
  archive_write_free(a);
  buffer.resize(used);
  return buffer;
}
static bool throws(const std::function<void()>& f) {
  try{f();return false;}catch(const std::exception&){return true;}
}
int main() {
  using namespace magpages;
  REQUIRE(slug("alice/hello")=="hello--alice");
  REQUIRE(slug("alice/my_repo")!=slug("alice/my-repo"));
  REQUIRE(slug("bob/"+std::string(90,'a')).size()<=63);
  REQUIRE(throws([]{slug("alice/../bad");}));
  REQUIRE(dns("www.example.org"));
  for(const auto& host:{"*.example.org","www..example.org","a","www/evil.org","-a.example.org"})
    REQUIRE(!dns(host));
  auto files=unpack(make_tar({{"archive/index.html",AE_IFREG},
    {"archive/docs/index.html",AE_IFREG},{"archive/.env",AE_IFREG},
    {"archive/docs/.secret",AE_IFREG},{"archive/../../etc/passwd",AE_IFREG}}),false);
  REQUIRE(files.at("index.html")=="hello");
  REQUIRE(files.find(".env")==files.end());
  REQUIRE(files.find("../../etc/passwd")==files.end());
  auto docs=unpack(make_tar({{"archive/index.html",AE_IFREG},
    {"archive/docs/index.html",AE_IFREG}}),true);
  REQUIRE(docs.size()==1 && docs.at("index.html")=="hello");
  REQUIRE(throws([]{unpack(make_tar({{"archive/unsafe",AE_IFLNK}}),false);}));
  auto c=files;
  c["CNAME"]="www.example.org\n";
  REQUIRE(custom(c)=="www.example.org");
  REQUIRE(c.count("CNAME")==0);
  c["CNAME"]="evil.org; echo leaked";
  REQUIRE(throws([&]{custom(c);}));
  auto base=std::filesystem::temp_directory_path()/
    ("mag-pages-native-test-"+std::to_string(getpid()));
  std::filesystem::remove_all(base);
  std::filesystem::create_directories(base);
  docs["deep/nested/test.txt"]="nested";
  materialize(base/"release",docs);
  REQUIRE(read(base/"release/index.html")=="hello");
  REQUIRE(read(base/"release/deep/nested/test.txt")=="nested");
  using perms=std::filesystem::perms;
  REQUIRE((std::filesystem::status(base/"release/deep").permissions() & perms::others_exec)!=perms::none);
  REQUIRE((std::filesystem::status(base/"release/deep/nested").permissions() & perms::others_exec)!=perms::none);
  link_atomic(base/"public","hello--alice",base/"release");
  REQUIRE(read(base/"public/hello--alice/index.html")=="hello");
  link_atomic(base/"public","hello--alice",base/"release");
  std::filesystem::remove(base/"public/hello--alice");
  REQUIRE(!std::filesystem::exists(base/"public/hello--alice"));
  Val v=Val::object();
  // Private Gitea API coverage: the same read-only service token must
  // enumerate bot-owned PRIVATE repos. HTTP 403/unlisted is fail-closed.
  const auto mocked=[](const std::string& path,const std::string& token)->Response {
    REQUIRE(path.rfind("repos/search?",0)==0);
    if(token!="read-repository-pat")return {403,"{}"};
    return {200,R"JSON({"data":[
      {"id":121,"full_name":"magbot-agent1/private-site","private":true},
      {"id":122,"full_name":"magbot-agent2/other-site","private":true}
    ]})JSON"};
  };
  const auto index=repo_list("read-repository-pat",mocked);
  REQUIRE(index.size()==2);
  REQUIRE(field(index[0],"full_name")=="magbot-agent1/private-site");
  REQUIRE(index[0].find("private") && index[0].find("private")->asBool());
  REQUIRE(throws([&]{repo_list("wrong-scope",mocked);}));
    REQUIRE(mode(v,"alice/hello")=="tailnet");
  v["alice/hello"]="public";
  REQUIRE(mode(v,"alice/hello")=="public");
  REQUIRE(!publicly_served("",mode(v,"alice/hello")));
  REQUIRE(publicly_served("192.0.2.10",mode(v,"alice/hello")));
  REQUIRE(!publicly_served("192.0.2.10","tailnet"));
  std::filesystem::create_directories(base/"releases"/"demo--alice"/"oldsha");
  std::filesystem::create_directories(base/"releases"/"demo--alice"/"newsha");
  std::filesystem::create_directories(base/"releases"/"obsolete--bob"/"garbage");
  Site site{"alice/demo","demo--alice","newsha","gh-pages","","tailnet",
            base/"releases"/"demo--alice"/"newsha"};
  cleanup_releases(base/"releases",{site});
  REQUIRE(!std::filesystem::exists(base/"releases"/"demo--alice"/"oldsha"));
  REQUIRE(!std::filesystem::exists(base/"releases"/"obsolete--bob"));
  REQUIRE(std::filesystem::exists(base/"releases"/"demo--alice"/"newsha"));
  cleanup_releases(base/"releases",{});
  REQUIRE(std::filesystem::is_empty(base/"releases"));
  REQUIRE(pages_safe_repo("demo") && !pages_safe_repo("../escape"));
  REQUIRE(pages_safe_vm("agent1") && !pages_safe_vm("../agent"));
  REQUIRE(pages_safe_path("assets/index.css") && !pages_safe_path("../../secret"));
  int count=0;
  for(const char* definition:PAGES_TOOL_DEFS) {
    Val v;REQUIRE(mj::parse(definition,v));
    auto fn=v.find("function");
    REQUIRE(fn && fn->find("name") && is_pages_tool(fn->find("name")->asStr()));
    count++;
  }
  REQUIRE(count==6);
  std::filesystem::remove_all(base);
  std::cout<<"MAG Pages C++ archive, policy, hostname and atomic webroot tests passed\n";
  return 0;
}
