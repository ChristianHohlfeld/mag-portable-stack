// Runtime gate for ops/glm/chat-template.jinja, built by ops/qwen/build-runtime.sh
// against the pinned llama.cpp (same chat/PEG parser code as llama-server).
//
// For enable_thinking=false/true, with and without tools, it renders the prompt
// exactly as llama-server does and parses representative GLM model outputs:
//   - thinking off: prompt ends "<think></think>", plain text -> content only
//   - thinking on:  prompt ends "<think>", "...</think>READY" -> reasoning + content
//   - tools:        "<tool_call>echo<arg_key>value</arg_key>..." -> structured call
//   - effort line is Low by default and identical for thinking on/off (prompt cache)
#include "chat.h"

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

static int failures = 0;
#define CHECK(cond, what) do { if (!(cond)) { std::fprintf(stderr, "FAIL %s\n", what); failures++; } \
                               else { std::printf("ok   %s\n", what); } } while (0)

static common_chat_params render(const common_chat_templates * t, bool thinking, bool tools) {
    common_chat_templates_inputs in;
    common_chat_msg user;
    user.role = "user";
    user.content = "Reply READY.";
    in.messages = { user };
    in.enable_thinking = thinking;
    in.reasoning_format = COMMON_REASONING_FORMAT_AUTO;
    in.chat_template_kwargs["enable_thinking"] = thinking ? "true" : "false";
    if (tools) {
        in.tools = { { "echo", "Return the supplied string",
            R"({"type":"object","properties":{"value":{"type":"string"}},"required":["value"]})" } };
    }
    return common_chat_templates_apply(t, in);
}

static common_chat_msg parse(const common_chat_params & p, const std::string & out) {
    common_chat_parser_params pp(p);
    pp.parser = p.parser;
    return common_chat_parse(common_chat_input(out), false, pp);
}

static bool ends_with(const std::string & s, const std::string & suffix) {
    return s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

int main(int argc, char ** argv) {
    if (argc != 2) { std::fprintf(stderr, "usage: %s chat-template.jinja\n", argv[0]); return 2; }
    std::ifstream f(argv[1]);
    std::stringstream ss; ss << f.rdbuf();
    auto tmpls = common_chat_templates_ptr(common_chat_templates_init(nullptr, ss.str()));
    CHECK(common_chat_templates_support_enable_thinking(tmpls.get()), "template supports enable_thinking");

    for (bool tools : { false, true }) {
        const char * tag = tools ? " (tools)" : "";
        auto off = render(tmpls.get(), false, tools);
        auto on  = render(tmpls.get(), true,  tools);
        const std::string p_off = off.prompt.str(), p_on = on.prompt.str();
        std::printf("prompt off%s: ...%s\n", tag, p_off.substr(p_off.size() > 80 ? p_off.size() - 80 : 0).c_str());
        CHECK(ends_with(p_off, "<|assistant|><think></think>"), "thinking off prefills empty think block");
        CHECK(ends_with(p_on,  "<|assistant|><think>"),         "thinking on opens think block");
        CHECK(p_off.rfind("[gMASK]<sop><|system|>Reasoning Effort: Low", 0) == 0, "default effort Low");
        CHECK(p_off.substr(0, p_off.rfind("<|assistant|>")) == p_on.substr(0, p_on.rfind("<|assistant|>")),
              "prefix identical for thinking on/off (prompt cache)");

        auto m = parse(off, "READY");
        CHECK(m.content == "READY" && m.reasoning_content.empty() && m.tool_calls.empty(), "off: plain answer -> content");
        m = parse(on, "short check</think>READY");
        CHECK(m.content == "READY" && m.reasoning_content == "short check", "on: reasoning + content split");
        if (tools) {
            const std::string call = "<tool_call>echo<arg_key>value</arg_key><arg_value>READY</arg_value></tool_call>";
            m = parse(off, call);
            CHECK(m.tool_calls.size() == 1 && m.tool_calls[0].name == "echo" &&
                  m.tool_calls[0].arguments.find("READY") != std::string::npos, "off: tool call parsed");
            m = parse(on, "need the tool</think>" + call);
            CHECK(m.tool_calls.size() == 1 && m.reasoning_content == "need the tool", "on: reasoning + tool call parsed");
        }
    }
    if (failures) { std::fprintf(stderr, "GLM chat template: %d check(s) failed\n", failures); return 1; }
    std::printf("PASS GLM chat template (thinking switch, effort, parser, tools)\n");
    return 0;
}
