// In-process DeepSeek tools. Gitea writes happen ONLY with the bot VM's
// own pre-provisioned Gitea credentials. No host read-only PAT is exposed.
#pragma once
static const char *PAGES_TOOL_DEFS[] = {
 R"PT({"type":"function","function":{"name":"pages_create","description":"Publish your own Gitea repository as a static HTTPS Page. Creates a private repository if absent, writes gh-pages/index.html and pushes it. Never use a different owner's repo. Publication becomes available on the next MAG Pages sync. Example {\"repo\":\"demo\",\"content\":\"<!doctype html><html><body>Hallo</body></html>\"}.","parameters":{"type":"object","properties":{"repo":{"type":"string"},"content":{"type":"string"}},"required":["repo","content"],"additionalProperties":false}}})PT",
 R"PT({"type":"function","function":{"name":"pages_update","description":"Write or replace one static file in YOUR gh-pages branch, commit and push. Use path=index.html for the main page; never edit other owners. Example {\"repo\":\"demo\",\"path\":\"index.html\",\"content\":\"<h1>Neu</h1>\"}.","parameters":{"type":"object","properties":{"repo":{"type":"string"},"path":{"type":"string"},"content":{"type":"string"}},"required":["repo","path","content"],"additionalProperties":false}}})PT",
 R"PT({"type":"function","function":{"name":"pages_list","description":"List only repositories owned by this bot's Gitea identity; never returns credentials. Example {}.","parameters":{"type":"object","properties":{},"additionalProperties":false}}})PT",
 R"PT({"type":"function","function":{"name":"pages_delete","description":"Unpublish a Page in YOUR Gitea repository by removing gh-pages and default-branch docs. The native publisher removes content/releases and root lifecycle removes CNAME TLS cert, nginx entries and visibility on sync. Leaves source repo itself intact. Example {\"repo\":\"demo\"}.","parameters":{"type":"object","properties":{"repo":{"type":"string"}},"required":["repo"],"additionalProperties":false}}})PT",
 R"PT({"type":"function","function":{"name":"pages_set_visibility","description":"Change YOUR Page to tailnet (private, default) or public (requires operator-enabled public bind). Uses a narrow server-side policy helper, not your VM shell or a secret. Example {\"repo\":\"demo\",\"visibility\":\"tailnet\"}.","parameters":{"type":"object","properties":{"repo":{"type":"string"},"visibility":{"type":"string","enum":["tailnet","public"]}},"required":["repo","visibility"],"additionalProperties":false}}})PT",
 R"PT({"type":"function","function":{"name":"pages_set_domain","description":"Commit a CNAME file on YOUR Page, e.g. www.example.org. For Internet HTTPS the Page must be public and the DNS CNAME must point to repo--owner.pages.stardenburg.de. Cert issuance and renewal happen automatically on MAG. Example {\"repo\":\"demo\",\"domain\":\"www.example.org\"}.","parameters":{"type":"object","properties":{"repo":{"type":"string"},"domain":{"type":"string"}},"required":["repo","domain"],"additionalProperties":false}}})PT"
};
static bool is_pages_tool(const std::string &name) {
  for(const char *spec:PAGES_TOOL_DEFS) {
    mj::Val v; if(!mj::parse(spec,v))continue;
    auto fn=v.find("function");
    if(fn && fn->find("name") && fn->find("name")->asStr()==name)return true;
  }
  return false;
}
static bool pages_safe_repo(const std::string& s) {
  if(s.empty()||s.size()>80||s[0]=='.'||s.find("..")!=std::string::npos)return false;
  for(unsigned char c:s) if(!std::isalnum(c)&&c!='-'&&c!='_'&&c!='.')return false;
  return true;
}
static bool pages_safe_vm(const std::string& s) {
  if(s.empty()||s.size()>32||s.front()<'a'||s.front()>'z'||s.back()=='-')return false;
  for(unsigned char c:s)if(!((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-'))return false;
  return true;
}
static bool pages_safe_path(const std::string& s) {
  if(s.empty()||s.size()>170||s[0]=='.'||s.find("..")!=std::string::npos ||
     s.find("/.")!=std::string::npos)return false;
  for(unsigned char c:s)
    if(!std::isalnum(c)&&c!='-'&&c!='_'&&c!='.'&&c!='/')return false;
  return true;
}
static std::string pages_dispatch_tool(int bot_idx,const std::string& name,
                                       const Val& args);
