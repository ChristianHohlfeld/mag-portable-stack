#include "../native/pho_codec.hpp"
#include <iostream>
#include <stdexcept>
static void require(bool ok,const char *why){if(!ok)throw std::runtime_error(why);}
int main() {
  const std::string prose="Heute hat der Bundestag den Entwurf zur Datenspeicherung beschlossen. Kritiker warnen vor einem Eingriff in die Privatsphäre.";
  auto a=pho::encode(prose);
  require(a.text.find("bund")!=std::string::npos,"German channel mapping");
  require(a.text.find("draft")!=std::string::npos,"content stem");
  require(a.text.size()<prose.size(),"lexical reduction");
  require(pho::encode(prose).text==a.text,"deterministic");
  const std::vector<std::string> exact={
    "https://example.test/a?x=12&y=A%20B", "alice+test@example.org",
    "-1.23e-4", "2026-10-26", "10.2.3.4:8192", "98", "001234",
    "user_id-ABC_123", "550e8400-e29b-41d4-a716-446655440000",
    "d1cf6afd9ad4f6f65fdfefbc85623fc6e795b19a", "API_KEY",
    "/home/chris/models/Qwen3.8.IQ4_XS.gguf", "PHOU0",
    "`const X = 42;`", "```cpp\nif (x != 17) { return 42; }\n```"};
  for(const auto &s:exact) {
    auto encoded=pho::encode("Hallo bitte wichtig "+s+" danke");
    require(encoded.text.find(s)!=std::string::npos,"protected span changed");
    require(std::find(encoded.u.begin(),encoded.u.end(),s)!=encoded.u.end(),"missing U span");
  }
  auto negative=pho::encode("Nicht nein niemals not never without if must should oder nicht").text;
  for(const auto *word:{"Nicht","nein","niemals","not","never","without","if","must","should","oder"})
    require(negative.find(word)!=std::string::npos,"lost negation/modality/condition");
  require(pho::encode("Bitte Zephyr niemals entfernen").text.find("Zephyr")!=std::string::npos,"proper name changed");
  for(const auto *code:{"`a=1`","```cpp\nx();\n```","GitHub pull request","diff --git a/x b/x","[Kontext]\nZeit: jetzt"})
    require(!pho::eligible(code),"code/tool/GitHub/context eligible");
  require(pho::eligible(prose),"ordinary history excluded");
  require(std::string(pho::instruction).find("normaler lesbarer Sprache")!=std::string::npos,"normal output instruction");
  std::cout<<"PhO native codec: stems, U protection, negation, deterministic history PASS\n";
}
