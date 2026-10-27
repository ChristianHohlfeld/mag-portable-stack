#include "mag_deadline.hpp"
#include "../native/pho_codec.hpp"
#include "../native/pho_live.hpp"
#include <iostream>
#include <stdexcept>
static void require(bool ok,const char *why){if(!ok)throw std::runtime_error(why);}
int main() {
  mag_test_deadline(60); // mag-test-timeout: 60
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
    "/home/chris/models/Qwen3.8.IQ4_XS.gguf", "PHOU0", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "don't",
    "`const X = 42;`", "```cpp\nif (x != 17) { return 42; }\n```"};
  for(const auto &s:exact) {
    auto encoded=pho::encode("Hallo bitte wichtig "+s+" danke");
    require(encoded.text.find(s)!=std::string::npos,"protected span changed");
    require(std::find(encoded.u.begin(),encoded.u.end(),s)!=encoded.u.end(),"missing U span");
  }
  const std::string long_id=std::string(32000,'a')+"_ID";
  require(pho::encode(long_id).text==long_id,"long ID protection must be bounded");
  auto negative=pho::encode("Nicht nein niemals not never without if must should oder nicht").text;
  for(const auto *word:{"Nicht","nein","niemals","not","never","without","if","must","should","oder"})
    require(negative.find(word)!=std::string::npos,"lost negation/modality/condition");
  require(pho::encode("Bitte Zephyr niemals entfernen").text.find("Zephyr")!=std::string::npos,"proper name changed");
  for(const auto *code:{"`a=1`","```cpp\nx();\n```","GitHub pull request","diff --git a/x b/x","[Kontext]\nZeit: jetzt", "SELECT * FROM foo WHERE id = 1", "git status"})
    require(!pho::eligible(code),"code/tool/GitHub/context eligible");
  require(pho::eligible(prose),"ordinary history excluded");
  // The live path must save tokens according to the active model, never bytes.
  const std::string long_text=prose+prose;
  const auto token_count=[](const std::string &s)->long{
    return s.find("Bundestag")!=std::string::npos?120:60;
  };
  const auto live=pho::live_encode(long_text,token_count);
  require(live.accepted && live.text.find("bund")!=std::string::npos,"eligible live prose compressed");
  require(live.text.find("Bundestag")==std::string::npos,"live prose kept the raw token");
  {
    const auto short_in=pho::live_encode("hi",token_count);
    require(!short_in.accepted && short_in.text=="hi","short live input changed");
  }
  {
    const auto down=pho::live_encode(long_text,[](const std::string &)->long{return -1;});
    require(!down.accepted && down.text==long_text,"tokenizer error must fail closed");
  }
  {
    const auto same=pho::live_encode(long_text,[](const std::string &)->long{return 120;});
    require(!same.accepted && same.text==long_text,"no token saving accepted");
  }
  {
    const auto barely=pho::live_encode(long_text,[](const std::string &s)->long{
      return s.find("Bundestag")!=std::string::npos?100:86;});
    require(!barely.accepted && barely.text==long_text,"14% saving rejected");
  }
  {
    const auto enough=pho::live_encode(long_text,[](const std::string &s)->long{
      return s.find("Bundestag")!=std::string::npos?100:85;});
    require(enough.accepted && enough.before-enough.after==15,"15% saving accepted");
  }
  {
    const auto quoted="\"Zitat\" "+long_text;
    const auto q=pho::live_encode(quoted,token_count);
    require(!q.accepted && q.text==quoted,"quoted live content changed");
  }
  {
    const auto code="```cpp\nint main(){}\n``` "+long_text;
    const auto c=pho::live_encode(code,token_count);
    require(!c.accepted && c.text==code,"code entered live codec");
  }
  {
    const auto lines=long_text+"\n"+long_text;
    const auto n=pho::live_encode(lines,token_count);
    require(!n.accepted && n.text==lines,"multiline live content changed");
  }
  {
    const auto brackets="siehe [1] "+long_text;
    const auto b=pho::live_encode(brackets,token_count);
    require(!b.accepted && b.text==brackets,"bracketed live content changed");
  }
  {
    const std::string few=std::string(40,'a')+" "+std::string(40,'b');
    const auto w=pho::live_encode(few,token_count);
    require(!w.accepted && w.text==few,"under 16 words rejected");
  }
  require(std::string(pho::instruction).find("normaler lesbarer Sprache")!=std::string::npos,"normal output instruction");
  std::cout<<"PhO native codec: stems, U protection, negation, deterministic history, live gate PASS\n";
}
