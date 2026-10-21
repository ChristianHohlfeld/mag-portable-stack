/**
 * MAG Deterministic Inference & Verification Engine
 * Production C++20 Implementation (Zero external dependencies, POSIX/Linux NUMA-aware)
 *
 * Compile:
 *   g++ -std=c++20 -O3 -Wall -Wextra -pthread mag_deterministic_engine.cpp -o mag_det
 */

#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cerrno>
#include <memory>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#if defined(__linux__) || defined(__unix__) || defined(__APPLE__)
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#if defined(__linux__)
#include <pthread.h>
#include <sched.h>
#endif

namespace mag::determinism {

/**
 * Standalone RFC 6234 SHA-256 implementation.
 * Computes immutable cryptographic fingerprints over model config, chat template,
 * NUMA affinity mask, and greedy decoding parameters.
 */
class Sha256 {
public:
    static std::string hash(const std::string& input) {
        Sha256 ctx;
        ctx.update(reinterpret_cast<const uint8_t*>(input.data()), input.size());
        return ctx.finalHex();
    }

private:
    uint32_t state_[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    };
    uint64_t bitlen_ = 0;
    uint8_t data_[64]{};
    size_t datalen_ = 0;

    static constexpr std::array<uint32_t, 64> K = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    };

    static inline uint32_t rotr(uint32_t x, uint32_t n) noexcept {
        return (x >> n) | (x << (32 - n));
    }

    void transform() noexcept {
        uint32_t m[64];
        for (size_t i = 0, j = 0; i < 16; ++i, j += 4) {
            m[i] = (static_cast<uint32_t>(data_[j]) << 24) |
                   (static_cast<uint32_t>(data_[j + 1]) << 16) |
                   (static_cast<uint32_t>(data_[j + 2]) << 8) |
                   (static_cast<uint32_t>(data_[j + 3]));
        }
        for (size_t i = 16; i < 64; ++i) {
            uint32_t s0 = rotr(m[i - 15], 7) ^ rotr(m[i - 15], 18) ^ (m[i - 15] >> 3);
            uint32_t s1 = rotr(m[i - 2], 17) ^ rotr(m[i - 2], 19) ^ (m[i - 2] >> 10);
            m[i] = m[i - 16] + s0 + m[i - 7] + s1;
        }

        uint32_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
        uint32_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];

        for (size_t i = 0; i < 64; ++i) {
            uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            uint32_t ch = (e & f) ^ ((~e) & g);
            uint32_t temp1 = h + S1 + ch + K[i] + m[i];
            uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t temp2 = S0 + maj;

            h = g;
            g = f;
            f = e;
            e = d + temp1;
            d = c;
            c = b;
            b = a;
            a = temp1 + temp2;
        }

        state_[0] += a; state_[1] += b; state_[2] += c; state_[3] += d;
        state_[4] += e; state_[5] += f; state_[6] += g; state_[7] += h;
    }

    void update(const uint8_t* data, size_t len) noexcept {
        for (size_t i = 0; i < len; ++i) {
            data_[datalen_++] = data[i];
            if (datalen_ == 64) {
                transform();
                bitlen_ += 512;
                datalen_ = 0;
            }
        }
    }

    std::string finalHex() {
        size_t i = datalen_;
        if (datalen_ < 56) {
            data_[i++] = 0x80;
            while (i < 56) data_[i++] = 0x00;
        } else {
            data_[i++] = 0x80;
            while (i < 64) data_[i++] = 0x00;
            transform();
            std::memset(data_, 0, 56);
        }

        bitlen_ += datalen_ * 8;
        data_[63] = static_cast<uint8_t>(bitlen_);
        data_[62] = static_cast<uint8_t>(bitlen_ >> 8);
        data_[61] = static_cast<uint8_t>(bitlen_ >> 16);
        data_[60] = static_cast<uint8_t>(bitlen_ >> 24);
        data_[59] = static_cast<uint8_t>(bitlen_ >> 32);
        data_[58] = static_cast<uint8_t>(bitlen_ >> 40);
        data_[57] = static_cast<uint8_t>(bitlen_ >> 48);
        data_[56] = static_cast<uint8_t>(bitlen_ >> 56);
        transform();

        std::ostringstream oss;
        oss << std::hex << std::setfill('0');
        for (uint32_t s : state_) {
            oss << std::setw(8) << s;
        }
        return oss.str();
    }
};

/**
 * RAII Guard that pins only the CLI HTTP client thread to specified logical CPUs
 * (e.g. a single NUMA island) during deterministic verification and inference,
 * It does NOT move or pin the inference server (configure that service separately).
 */
class ScopedNumaAffinity {
public:
    explicit ScopedNumaAffinity(const std::vector<int>& allowed_cores) {
#if defined(__linux__)
        if (allowed_cores.empty()) return;
        if (sched_getaffinity(0, sizeof(cpu_set_t), &original_mask_) == 0) {
            restorable_ = true;
        }
        cpu_set_t mask;
        CPU_ZERO(&mask);
        for (int core : allowed_cores) {
            if (core >= 0 && core < CPU_SETSIZE) {
                CPU_SET(core, &mask);
            }
        }
        if (sched_setaffinity(0, sizeof(cpu_set_t), &mask) != 0) {
            const std::string why = std::strerror(errno);
            restorable_ = false;
            throw std::runtime_error("NUMA client affinity failed: " + why);
        }
#else
        (void)allowed_cores;
#endif
    }

    ~ScopedNumaAffinity() {
#if defined(__linux__)
        if (restorable_) {
            sched_setaffinity(0, sizeof(cpu_set_t), &original_mask_);
        }
#endif
    }

    ScopedNumaAffinity(const ScopedNumaAffinity&) = delete;
    ScopedNumaAffinity& operator=(const ScopedNumaAffinity&) = delete;

private:
#if defined(__linux__)
    cpu_set_t original_mask_{};
    bool restorable_ = false;
#endif
};

/**
 * Fixed Model & Runtime Configuration.
 * Any modification to these parameters alters the SHA-256 fingerprint and
 * immediately invalidates a previous 20/20 verification status.
 */
struct ModelDeterministicConfig {
    std::string model_id = "qwen2.5-72b-instruct";
    std::string weights_sha256_or_path = "UNATTESTED"; // optional --weights-sha256
    std::string chat_template_id = "chatml-default-v1";
    std::string backend_host = "127.0.0.1";
    uint16_t backend_port = 8080;
    std::string endpoint_path = "/v1/chat/completions";
    std::string backend_build = "unspecified";
    int timeout_sec = 600;

    // Fixed deterministic decoding parameters
    float temperature = 0.0f;
    int32_t top_k = 1;
    float top_p = 1.0f;
    uint32_t seed = 42;
    bool cache_prompt = false;
    bool thinking_enabled = false;
    int32_t n_threads = 16;
    std::vector<int> numa_cores = {0, 1, 2, 3, 4, 5, 6, 7};

    std::string computeFingerprint() const {
        std::ostringstream oss;
        oss << "model_id=" << model_id
            << "|weights=" << weights_sha256_or_path
            << "|template=" << chat_template_id
            << "|backend-build=" << backend_build
            << "|host=" << backend_host
            << "|port=" << backend_port
            << "|endpoint=" << endpoint_path
            << "|timeout=" << timeout_sec
            << "|temp=" << std::fixed << std::setprecision(6) << temperature
            << "|top_k=" << top_k
            << "|top_p=" << std::fixed << std::setprecision(6) << top_p
            << "|seed=" << seed
            << "|cache_prompt=" << (cache_prompt ? "1" : "0")
            << "|thinking=" << (thinking_enabled ? "1" : "0")
            << "|threads=" << n_threads
            << "|numa=";
        for (size_t i = 0; i < numa_cores.size(); ++i) {
            if (i > 0) oss << ",";
            oss << numa_cores[i];
        }
        return Sha256::hash(oss.str());
    }
};

/**
 * Full stateful MAG room context (used in normal mode, stripped in deterministic mode).
 */
struct ChatMessage {
    std::string role;
    std::string content;
};

struct MagStatefulRoomContext {
    std::string system_instruction;
    std::vector<std::string> memories;
    std::vector<ChatMessage> chat_history;
    std::string current_timestamp_iso;
    std::vector<std::string> tool_definitions_json;
    bool deterministic_mode_enabled = false;
};

struct RawInferenceResult {
    bool success = false;
    int http_status = 0;
    std::string error_message;
    std::string content;
    std::vector<int32_t> token_ids;
    std::string token_stream_sha256;
    double latency_ms = 0.0;
};

struct VerificationReport {
    bool verified = false;
    int completed_runs = 0;
    int total_target_runs = 20;
    std::string config_fingerprint;
    std::string baseline_token_hash;
    std::vector<int32_t> baseline_tokens;
    std::string baseline_text;
    std::string failure_reason;
    int diverging_run_index = -1;      // 1-based run number where divergence occurred
    size_t diverging_token_pos = 0;    // Token index where divergence started
    int32_t expected_token_id = -1;
    int32_t actual_token_id = -1;
    double total_duration_ms = 0.0;
    std::string timestamp_utc;
};

/**
 * JSON Utility for RFC 8259 string escaping and lightweight token/content extraction.
 */
class JsonUtils {
public:
    static std::string escape(const std::string& s) {
        std::ostringstream o;
        for (unsigned char c : s) {
            switch (c) {
                case '"':  o << "\\\""; break;
                case '\\': o << "\\\\"; break;
                case '\b': o << "\\b";  break;
                case '\f': o << "\\f";  break;
                case '\n': o << "\\n";  break;
                case '\r': o << "\\r";  break;
                case '\t': o << "\\t";  break;
                default:
                    if (c < 0x20) {
                        o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
                    } else {
                        o << c;
                    }
            }
        }
        return o.str();
    }

    /**
     * Builds the strictly isolated 1-message JSON payload for deterministic mode.
     * Deliberately ignores chat history, memories, system prompts, timestamps, and tools.
     */
    static std::string buildStatelessPayload(
        const ModelDeterministicConfig& cfg,
        const std::string& isolated_user_input,
        int32_t max_tokens)
    {
        std::ostringstream json;
        json << "{\n"
             << "  \"model\": \"" << escape(cfg.model_id) << "\",\n"
             << "  \"messages\": [\n"
             << "    {\"role\": \"user\", \"content\": \"" << escape(isolated_user_input) << "\"}\n"
             << "  ],\n"
             << "  \"temperature\": 0,\n"
             << "  \"top_k\": 1,\n"
             << "  \"top_p\": 1,\n"
             << "  \"seed\": " << cfg.seed << ",\n"
             << "  \"max_tokens\": " << max_tokens << ",\n"
             << "  \"n_predict\": " << max_tokens << ",\n"
             << "  \"cache_prompt\": false,\n"
             << "  \"stream\": false,\n"
             << "  \"logprobs\": true,\n"
             << "  \"return_tokens\": true,\n"
             << "  \"return_token_ids\": true,\n"
             << "  \"chat_template_kwargs\": {\"enable_thinking\": "
             << (cfg.thinking_enabled ? "true" : "false") << "}\n"
             << "}";
        return json.str();
    }

    /**
     * Builds a normal stateful request when deterministic mode is OFF,
     * or delegates to buildStatelessPayload when deterministic mode is ON.
     */
    static std::string buildRequestForRoom(
        const ModelDeterministicConfig& cfg,
        const MagStatefulRoomContext& room,
        const std::string& current_user_input,
        int32_t max_tokens = 512)
    {
        if (room.deterministic_mode_enabled) {
            // HARD ISOLATION: Only the raw user text is transmitted.
            return buildStatelessPayload(cfg, current_user_input, max_tokens);
        }

        // Normal mode: includes system prompt, memories, timestamp, and chat history
        std::ostringstream json;
        json << "{\n"
             << "  \"model\": \"" << escape(cfg.model_id) << "\",\n"
             << "  \"messages\": [\n";

        bool first = true;
        if (!room.system_instruction.empty() || !room.memories.empty()) {
            std::ostringstream sys;
            sys << room.system_instruction;
            if (!room.current_timestamp_iso.empty()) {
                sys << "\n[Timestamp: " << room.current_timestamp_iso << "]";
            }
            for (const auto& mem : room.memories) {
                sys << "\n[Memory: " << mem << "]";
            }
            json << "    {\"role\": \"system\", \"content\": \"" << escape(sys.str()) << "\"}";
            first = false;
        }

        for (const auto& msg : room.chat_history) {
            if (!first) json << ",\n";
            json << "    {\"role\": \"" << escape(msg.role) << "\", \"content\": \"" << escape(msg.content) << "\"}";
            first = false;
        }

        if (!first) json << ",\n";
        json << "    {\"role\": \"user\", \"content\": \"" << escape(current_user_input) << "\"}\n"
             << "  ],\n"
             << "  \"temperature\": 0.7,\n"
             << "  \"cache_prompt\": true,\n"
             << "  \"max_tokens\": " << max_tokens << "\n"
             << "}";
        return json.str();
    }

    /**
     * Reads only real generated integer IDs under "tokens" or "token_ids".
     * Never guesses token IDs from bytes or logprobs.
     */
    static void parseInferenceResponse(const std::string& raw_json, RawInferenceResult& out) {
        out.content = extractStringField(raw_json, "content");

        // 1. Try explicit integer array "tokens" or "token_ids"
        out.token_ids = extractIntArrayField(raw_json, "\"tokens\"");
        if (out.token_ids.empty()) {
            out.token_ids = extractIntArrayField(raw_json, "\"token_ids\"");
        }

        // Only actual backend-supplied integer token IDs are admissible.
        // Byte hashes or top-logprobs entries must never masquerade as tokens.
        // Request return_tokens (llama.cpp) / return_token_ids (vLLM).
        // Compute cryptographic hash over the exact token ID sequence + content
        std::ostringstream tok_stream;
        for (int32_t tid : out.token_ids) {
            tok_stream << tid << ",";
        }
        tok_stream << "|" << out.content;
        out.token_stream_sha256 = Sha256::hash(tok_stream.str());
    }

private:
    static int32_t fnv1a32(const std::string& s) noexcept {
        uint32_t h = 2166136261u;
        for (unsigned char c : s) {
            h ^= c;
            h *= 16777619u;
        }
        return static_cast<int32_t>(h & 0x7fffffff);
    }

    static std::string extractStringField(const std::string& json, const std::string& field_name) {
        std::string pattern = "\"" + field_name + "\"";
        // Find the last occurrence of "content" (or first after "message")
        size_t msg_pos = json.find("\"message\"");
        size_t pos = json.find(pattern, msg_pos != std::string::npos ? msg_pos : 0);
        if (pos == std::string::npos) {
            pos = json.find(pattern);
        }
        if (pos == std::string::npos) return "";

        pos = json.find(':', pos + pattern.size());
        if (pos == std::string::npos) return "";
        pos++;
        while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t' || json[pos] == '\n' || json[pos] == '\r')) {
            pos++;
        }
        if (pos >= json.size() || json[pos] != '"') return "";
        pos++;

        std::string result;
        while (pos < json.size()) {
            char c = json[pos++];
            if (c == '\\' && pos < json.size()) {
                char esc = json[pos++];
                switch (esc) {
                    case '"':  result.push_back('"'); break;
                    case '\\': result.push_back('\\'); break;
                    case '/':  result.push_back('/'); break;
                    case 'b':  result.push_back('\b'); break;
                    case 'f':  result.push_back('\f'); break;
                    case 'n':  result.push_back('\n'); break;
                    case 'r':  result.push_back('\r'); break;
                    case 't':  result.push_back('\t'); break;
                    default:   result.push_back(esc); break;
                }
            } else if (c == '"') {
                break;
            } else {
                result.push_back(c);
            }
        }
        return result;
    }

    static std::vector<int32_t> extractIntArrayField(const std::string& json, const std::string& key_quoted) {
        std::vector<int32_t> res;
        size_t pos = json.find(key_quoted);
        if (pos == std::string::npos) return res;
        pos = json.find('[', pos + key_quoted.size());
        if (pos == std::string::npos) return res;
        size_t end = json.find(']', pos);
        if (end == std::string::npos) return res;

        std::string slice = json.substr(pos + 1, end - pos - 1);
        std::stringstream ss(slice);
        std::string item;
        while (std::getline(ss, item, ',')) {
            try {
                size_t idx = 0;
                int32_t val = std::stoi(item, &idx);
                if (idx > 0) res.push_back(val);
            } catch (...) {
                // Ignore non-integer array elements
            }
        }
        return res;
    }

    static void extractRepeatedIntKey(const std::string& json, const std::string& key_quoted, std::vector<int32_t>& out) {
        size_t pos = 0;
        while ((pos = json.find(key_quoted, pos)) != std::string::npos) {
            pos = json.find(':', pos + key_quoted.size());
            if (pos == std::string::npos) break;
            pos++;
            while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t')) pos++;
            if (pos < json.size() && (std::isdigit(static_cast<unsigned char>(json[pos])) || json[pos] == '-')) {
                try {
                    size_t idx = 0;
                    int32_t val = std::stoi(json.substr(pos, 16), &idx);
                    if (idx > 0) out.push_back(val);
                } catch (...) {}
            }
        }
    }

    static void extractRepeatedStringKey(const std::string& json, const std::string& key_quoted, std::vector<std::string>& out) {
        size_t pos = 0;
        while ((pos = json.find(key_quoted, pos)) != std::string::npos) {
            pos = json.find(':', pos + key_quoted.size());
            if (pos == std::string::npos) break;
            pos++;
            while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t' || json[pos] == '\n')) pos++;
            if (pos < json.size() && json[pos] == '"') {
                pos++;
                std::string token;
                while (pos < json.size()) {
                    char c = json[pos++];
                    if (c == '\\' && pos < json.size()) {
                        token.push_back(json[pos++]);
                    } else if (c == '"') {
                        break;
                    } else {
                        token.push_back(c);
                    }
                }
                out.push_back(token);
            }
        }
    }
};

/**
 * Zero-dependency POSIX HTTP/1.1 client for communicating with the local
 * C++ inference server (e.g., llama-server on 127.0.0.1:8080).
 */
class HttpInferenceTransport {
public:
    virtual ~HttpInferenceTransport() = default;

    virtual RawInferenceResult postJson(
        const std::string& host,
        uint16_t port,
        const std::string& path,
        const std::string& json_body,
        int timeout_sec = 60)
    {
        RawInferenceResult result;
        auto t0 = std::chrono::steady_clock::now();

#if defined(__linux__) || defined(__unix__) || defined(__APPLE__)
        int sock = ::socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) {
            result.error_message = "socket() creation failed";
            return result;
        }

        struct timeval tv{};
        tv.tv_sec = timeout_sec;
        tv.tv_usec = 0;
        ::setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        ::setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

        struct sockaddr_in server_addr{};
        server_addr.sin_family = AF_INET;
        server_addr.sin_port = htons(port);

        if (::inet_pton(AF_INET, host.c_str(), &server_addr.sin_addr) <= 0) {
            struct hostent* he = ::gethostbyname(host.c_str());
            if (!he || !he->h_addr_list[0]) {
                ::close(sock);
                result.error_message = "Unable to resolve host: " + host;
                return result;
            }
            std::memcpy(&server_addr.sin_addr, he->h_addr_list[0], he->h_length);
        }

        if (::connect(sock, reinterpret_cast<struct sockaddr*>(&server_addr), sizeof(server_addr)) < 0) {
            ::close(sock);
            result.error_message = "Connection refused on " + host + ":" + std::to_string(port);
            return result;
        }

        std::ostringstream req;
        req << "POST " << path << " HTTP/1.1\r\n"
            << "Host: " << host << ":" << port << "\r\n"
            << "Content-Type: application/json\r\n"
            << "Accept: application/json\r\n"
            << "Connection: close\r\n"
            << "Content-Length: " << json_body.size() << "\r\n\r\n"
            << json_body;

        std::string wire = req.str();
        size_t total_sent = 0;
        while (total_sent < wire.size()) {
            ssize_t n = ::send(sock, wire.data() + total_sent, wire.size() - total_sent, 0);
            if (n <= 0) {
                ::close(sock);
                result.error_message = "Socket write failed";
                return result;
            }
            total_sent += static_cast<size_t>(n);
        }

        std::string raw_response;
        char buffer[4096];
        ssize_t n_read = 0;
        while ((n_read = ::recv(sock, buffer, sizeof(buffer), 0)) > 0) {
            if (raw_response.size() + static_cast<size_t>(n_read) > 8 * 1024 * 1024) {
                ::close(sock);
                result.error_message = "HTTP response exceeds 8 MiB";
                return result;
            }
            raw_response.append(buffer, static_cast<size_t>(n_read));
        }
        if (n_read < 0) {
            ::close(sock);
            result.error_message = "HTTP receive failed/timeout: " + std::string(std::strerror(errno));
            return result;
        }
        ::close(sock);

        auto t1 = std::chrono::steady_clock::now();
        result.latency_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

        size_t header_end = raw_response.find("\r\n\r\n");
        if (header_end == std::string::npos) {
            result.error_message = "Malformed HTTP response from inference backend";
            return result;
        }

        std::string headers = raw_response.substr(0, header_end);
        std::string body = raw_response.substr(header_end + 4);
        if (headers.find("Transfer-Encoding: chunked") != std::string::npos ||
            headers.find("transfer-encoding: chunked") != std::string::npos) {
            std::string unchunked;
            size_t pos = 0;
            bool ended = false;
            while (pos < body.size()) {
                const size_t nl = body.find("\r\n", pos);
                if (nl == std::string::npos) break;
                const std::string len = body.substr(pos, nl - pos);
                size_t used = 0, size = 0;
                try { size = std::stoull(len, &used, 16); } catch (...) { break; }
                if (used == 0 || (used < len.size() && len[used] != ';')) break;
                pos = nl + 2;
                if (size == 0) { ended = true; break; }
                if (size > body.size() - pos || size > 8 * 1024 * 1024 - unchunked.size()) break;
                unchunked.append(body, pos, size);
                pos += size;
                if (body.compare(pos, 2, "\r\n") != 0) break;
                pos += 2;
            }
            if (!ended) {
                result.error_message = "Invalid chunked HTTP response";
                return result;
            }
            body = std::move(unchunked);
        }

        // Parse HTTP status code
        std::istringstream hss(headers);
        std::string http_ver;
        hss >> http_ver >> result.http_status;

        if (result.http_status < 200 || result.http_status >= 300) {
            result.error_message = "HTTP " + std::to_string(result.http_status) + ": " + body.substr(0, 256);
            return result;
        }

        JsonUtils::parseInferenceResponse(body, result);
        if (result.token_ids.empty()) {
            result.error_message = "Keine echten Token-IDs geliefert (tokens/token_ids). Token-Verifikation nicht moeglich; Backend-API pruefen.";
            return result;
        }
        if (result.content.empty()) {
            result.error_message = "Kein Assistant-Text in Backend-Antwort. Token-only Ergebnis wird nicht zertifiziert.";
            return result;
        }

        result.success = true;
        return result;
#else
        (void)host; (void)port; (void)path; (void)json_body; (void)timeout_sec;
        result.error_message = "POSIX sockets unsupported on this platform";
        return result;
#endif
    }
};

/**
 * MAG Deterministic Engine Controller.
 * Manages:
 *  - Local mutex (does not control remote inference-server continuous batching)
 *  - Client-thread CPU affinity only; server process not controlled
 *  - 20x fresh HTTP inference (same running backend) Fail-Fast Verification ("Prüfen")
 *  - Fingerprint validation before allowing verified deterministic claims
 */
class MagDeterministicEngine {
public:
    explicit MagDeterministicEngine(
        std::shared_ptr<HttpInferenceTransport> transport = std::make_shared<HttpInferenceTransport>(),
        std::string state_filepath = "mag_determinism_state.json")
        : transport_(std::move(transport)), state_filepath_(std::move(state_filepath)) {}

    /**
     * Runs the 20x fresh HTTP inference (same running backend) Hardware Verification ("Prüfen" button handler).
     * Completely isolated from any MAG room, chat history, or memory.
     * Uses Fail-Fast early exit on the very first diverging token ID.
     */
    VerificationReport verifyModel20x(
        const ModelDeterministicConfig& cfg,
        int32_t max_verify_tokens = 32,
        const std::string& verification_prompt = "Erkläre in exakt zwei präzisen Sätzen, warum deterministische Zustandsautomaten reproduzierbar sind.")
    {
        std::lock_guard<std::mutex> lock(engine_mutex_);
        ScopedNumaAffinity numa_guard(cfg.numa_cores);

        VerificationReport report;
        report.total_target_runs = 20;
        report.config_fingerprint = cfg.computeFingerprint();
        report.timestamp_utc = currentUtcTimestamp();

        const std::string payload = JsonUtils::buildStatelessPayload(cfg, verification_prompt, max_verify_tokens);
        auto start_all = std::chrono::steady_clock::now();

        for (int run = 0; run < report.total_target_runs; ++run) {
            RawInferenceResult res = transport_->postJson(
                cfg.backend_host, cfg.backend_port, cfg.endpoint_path, payload, cfg.timeout_sec);

            if (!res.success || res.token_ids.empty()) {
                report.verified = false;
                report.completed_runs = run;
                report.failure_reason = "Inferenzfehler in Lauf " + std::to_string(run + 1) + ": " + res.error_message;
                invalidateModelVerification(cfg.model_id);
                return report;
            }

            if (run == 0) {
                report.baseline_tokens = res.token_ids;
                report.baseline_token_hash = res.token_stream_sha256;
                report.baseline_text = res.content;
                report.completed_runs = 1;
            } else {
                // Compare token-by-token against Run 1 (Baseline)
                const size_t min_len = std::min(report.baseline_tokens.size(), res.token_ids.size());
                for (size_t pos = 0; pos < min_len; ++pos) {
                    if (res.token_ids[pos] != report.baseline_tokens[pos]) {
                        // FAIL-FAST: Immediate abort on the first diverging token ID
                        report.verified = false;
                        report.completed_runs = run + 1;
                        report.diverging_run_index = run + 1;
                        report.diverging_token_pos = pos;
                        report.expected_token_id = report.baseline_tokens[pos];
                        report.actual_token_id = res.token_ids[pos];
                        report.failure_reason =
                            "Token-Abweichung in Lauf " + std::to_string(run + 1) +
                            " an Token-Position " + std::to_string(pos) +
                            " (Erwartet Token-ID " + std::to_string(report.expected_token_id) +
                            ", Erhalten Token-ID " + std::to_string(report.actual_token_id) + ")";
                        auto end_fail = std::chrono::steady_clock::now();
                        report.total_duration_ms = std::chrono::duration<double, std::milli>(end_fail - start_all).count();
                        invalidateModelVerification(cfg.model_id);
                        return report;
                    }
                }

                if (res.token_ids.size() != report.baseline_tokens.size()) {
                    report.verified = false;
                    report.completed_runs = run + 1;
                    report.diverging_run_index = run + 1;
                    report.diverging_token_pos = min_len;
                    report.failure_reason =
                        "Längen-Abweichung der Tokenfolge in Lauf " + std::to_string(run + 1) +
                        " (Baseline: " + std::to_string(report.baseline_tokens.size()) +
                        " Tokens vs. Lauf: " + std::to_string(res.token_ids.size()) + " Tokens)";
                    auto end_fail = std::chrono::steady_clock::now();
                    report.total_duration_ms = std::chrono::duration<double, std::milli>(end_fail - start_all).count();
                    invalidateModelVerification(cfg.model_id);
                    return report;
                }

                if (res.token_stream_sha256 != report.baseline_token_hash) {
                    report.verified = false;
                    report.completed_runs = run + 1;
                    report.diverging_run_index = run + 1;
                    report.failure_reason = "Gleiche Token-IDs, aber abweichender Antworttext in Lauf " + std::to_string(run + 1);
                    invalidateModelVerification(cfg.model_id);
                    return report;
                }
                report.completed_runs = run + 1;
            }
        }

        auto end_all = std::chrono::steady_clock::now();
        report.total_duration_ms = std::chrono::duration<double, std::milli>(end_all - start_all).count();
        report.verified = true;

        verified_registry_[cfg.model_id] = report;
        persistStateToDisk();
        return report;
    }

    /**
     * Executes a single inference call.
     * If `room.deterministic_mode_enabled` is true:
     *  - Verifies that the current `cfg.computeFingerprint()` matches the 20/20 verified fingerprint.
     *  - Strips all room state, history, memories, and tools.
     *  - Pins NUMA cores and acquires exclusive lock for Batch=1 deterministic execution.
     */
    RawInferenceResult executeRoomInference(
        const ModelDeterministicConfig& cfg,
        const MagStatefulRoomContext& room,
        const std::string& user_input,
        int32_t max_tokens = 256,
        bool require_verified_badge = true)
    {
        std::lock_guard<std::mutex> lock(engine_mutex_);

        if (room.deterministic_mode_enabled && require_verified_badge) {
            if (!isModelVerifiedForConfig(cfg)) {
                RawInferenceResult blocked;
                blocked.success = false;
                blocked.error_message =
                    "Deterministischer Modus blockiert: Modell '" + cfg.model_id +
                    "' besitzt keinen gültigen 20/20-Verifikationsnachweis für Fingerprint " +
                    cfg.computeFingerprint().substr(0, 16) + "...";
                return blocked;
            }
        }

        ScopedNumaAffinity numa_guard(room.deterministic_mode_enabled ? cfg.numa_cores : std::vector<int>{});
        const std::string payload = JsonUtils::buildRequestForRoom(cfg, room, user_input, max_tokens);
        return transport_->postJson(cfg.backend_host, cfg.backend_port, cfg.endpoint_path, payload, cfg.timeout_sec);
    }

    /**
     * Checks whether the model is currently verified and its runtime configuration
     * fingerprint is unchanged since the 20/20 hardware test.
     */
    bool isModelVerifiedForConfig(const ModelDeterministicConfig& cfg) const {
        auto it = verified_registry_.find(cfg.model_id);
        if (it == verified_registry_.end() || !it->second.verified) {
            return false;
        }
        return it->second.config_fingerprint == cfg.computeFingerprint();
    }

private:
    std::shared_ptr<HttpInferenceTransport> transport_;
    std::string state_filepath_;
    mutable std::mutex engine_mutex_;
    std::map<std::string, VerificationReport> verified_registry_;

    void invalidateModelVerification(const std::string& model_id) {
        verified_registry_.erase(model_id);
        persistStateToDisk();
    }

    static std::string currentUtcTimestamp() {
        std::time_t now = std::time(nullptr);
        std::tm tm_utc{};
#if defined(_WIN32)
        gmtime_s(&tm_utc, &now);
#else
        gmtime_r(&now, &tm_utc);
#endif
        char buf[32];
        std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm_utc);
        return std::string(buf);
    }

    void persistStateToDisk() const {
        std::ofstream ofs(state_filepath_, std::ios::trunc);
        if (!ofs) return;
        ofs << "{\n  \"verified_models\": {\n";
        bool first = true;
        for (const auto& [model_id, rep] : verified_registry_) {
            if (!first) ofs << ",\n";
            ofs << "    \"" << JsonUtils::escape(model_id) << "\": {\n"
                << "      \"verified\": " << (rep.verified ? "true" : "false") << ",\n"
                << "      \"completed_runs\": " << rep.completed_runs << ",\n"
                << "      \"config_fingerprint\": \"" << rep.config_fingerprint << "\",\n"
                << "      \"baseline_token_hash\": \"" << rep.baseline_token_hash << "\",\n"
                << "      \"timestamp_utc\": \"" << rep.timestamp_utc << "\"\n"
                << "    }";
            first = false;
        }
        ofs << "\n  }\n}\n";
    }
};

/**
 * Local IEEE-754 Matrix-Logit Transport for self-testing the 20/20 verifier and
 * Fail-Fast divergence detector when no external llama-server is running on port 8080.
 */
class LocalHardwareMatrixTransport : public HttpInferenceTransport {
public:
    explicit LocalHardwareMatrixTransport(bool inject_non_associative_thread_noise = false)
        : inject_noise_(inject_non_associative_thread_noise) {}

    RawInferenceResult postJson(
        const std::string& /*host*/,
        uint16_t /*port*/,
        const std::string& /*path*/,
        const std::string& json_body,
        int /*timeout_sec*/) override
    {
        auto t0 = std::chrono::steady_clock::now();
        RawInferenceResult res;
        res.http_status = 200;

        // Verify that the incoming payload strictly complies with stateless rules
        if (json_body.find("\"cache_prompt\": false") == std::string::npos ||
            json_body.find("\"temperature\": 0") == std::string::npos ||
            json_body.find("\"top_k\": 1") == std::string::npos) {
            res.error_message = "Payload verletzt deterministische Parameter-Vorgaben";
            return res;
        }

        const uint64_t call_idx = ++call_counter_;
        std::vector<int32_t> generated_tokens;
        generated_tokens.reserve(32);

        // Perform real FP32 dot-product reductions for 32 autoregressive steps
        uint32_t prev_token = 128000; // BOS token
        for (int step = 0; step < 32; ++step) {
            float candidate_a_logit = computeDotProductLogit(prev_token, step, /*candidate=*/0, false);
            bool perturb = inject_noise_ && (call_idx == 4) && (step == 5);
            float candidate_b_logit = computeDotProductLogit(prev_token, step, /*candidate=*/1, perturb);

            int32_t chosen_token = (candidate_a_logit >= candidate_b_logit)
                ? (10000 + step * 17 + static_cast<int32_t>(prev_token % 97))
                : (20000 + step * 31 + static_cast<int32_t>(prev_token % 89));

            generated_tokens.push_back(chosen_token);
            prev_token = static_cast<uint32_t>(chosen_token);
        }

        std::ostringstream body;
        body << "{\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"Stateless-Output-Hash:"
             << generated_tokens.back() << "\"}}],\"tokens\":[";
        for (size_t i = 0; i < generated_tokens.size(); ++i) {
            if (i > 0) body << ",";
            body << generated_tokens[i];
        }
        body << "]}";

        JsonUtils::parseInferenceResponse(body.str(), res);
        auto t1 = std::chrono::steady_clock::now();
        res.latency_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
        res.success = true;
        return res;
    }

private:
    bool inject_noise_ = false;
    std::atomic<uint64_t> call_counter_{0};

    static float computeDotProductLogit(uint32_t token_in, int step, int candidate, bool reverse_reduction_order) noexcept {
        constexpr size_t DIM = 4096;
        std::array<float, DIM> activations{};
        std::array<float, DIM> weights{};

        for (size_t i = 0; i < DIM; ++i) {
            uint32_t h1 = (token_in * 2654435761u) ^ static_cast<uint32_t>(i * 7919 + step * 101);
            uint32_t h2 = (static_cast<uint32_t>(candidate + 1) * 2246822519u) ^ static_cast<uint32_t>(i * 6151);
            activations[i] = static_cast<float>(h1 & 0xFFFF) * 1e-5f;
            weights[i]     = static_cast<float>(h2 & 0xFFFF) * 1e-5f;
        }

        float acc = 0.0f;
        if (!reverse_reduction_order) {
            for (size_t i = 0; i < DIM; ++i) {
                acc += activations[i] * weights[i];
            }
        } else {
            // Simulates non-associative floating point drift + close logit boundary flip
            for (size_t i = DIM; i > 0; --i) {
                acc += activations[i - 1] * weights[i - 1];
            }
            acc += 1.0f;
        }
        return acc;
    }
};

} // namespace mag::determinism

void printUsage(const char* prog) {
    std::cout << "Verwendung:\n"
              << "  " << prog << " --selftest\n"
              << "      Prüft den synthetischen Architektur-Selbsttest (Stateless-Isolation, 20/20 Pass, Fail-Fast Abbruch, Fingerprint-Lock) aus.\n\n"
              << "  " << prog << " --verify --host <IP> --port <PORT> --model <NAME> [--numa 0,1,2,3]\n"
              << "      Führt 20 echte neue HTTP-Anfragen (keine Prozess-Kaltstarts) gegen einen laufenden llama-server / vLLM Endpunkt aus.\n\n"
              << "  " << prog << " --infer \"<TEXT>\" --host <IP> --port <PORT> --model <NAME>\n"
              << "      Verifiziert das Modell und führt anschließend einen isolierten Stateless-Aufruf aus.\n";
}

static std::vector<int> parseCores(const std::string& csv) {
    std::vector<int> cores;
    std::stringstream ss(csv);
    std::string item;
    while (std::getline(ss, item, ',')) {
        try {
            cores.push_back(std::stoi(item));
        } catch (...) {}
    }
    return cores;
}

int main(int argc, char* argv[]) {
    using namespace mag::determinism;

    std::string mode = "--selftest";
    ModelDeterministicConfig cfg;
    std::string user_prompt = "hi";

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--selftest" || arg == "--verify") {
            mode = arg;
        } else if (arg == "--infer" && i + 1 < argc) {
            mode = "--infer";
            user_prompt = argv[++i];
        } else if (arg == "--host" && i + 1 < argc) {
            cfg.backend_host = argv[++i];
        } else if (arg == "--port" && i + 1 < argc) {
            cfg.backend_port = static_cast<uint16_t>(std::stoi(argv[++i]));
        } else if (arg == "--model" && i + 1 < argc) {
            cfg.model_id = argv[++i];
        } else if (arg == "--numa" && i + 1 < argc) {
            const std::string value = argv[++i];
            cfg.numa_cores = value == "none" ? std::vector<int>{} : parseCores(value);
        } else if (arg == "--endpoint" && i + 1 < argc) {
            cfg.endpoint_path = argv[++i];
        } else if (arg == "--timeout" && i + 1 < argc) {
            cfg.timeout_sec = std::stoi(argv[++i]);
        } else if (arg == "--weights-sha256" && i + 1 < argc) {
            cfg.weights_sha256_or_path = argv[++i];
        } else if (arg == "--backend-build" && i + 1 < argc) {
            cfg.backend_build = argv[++i];
        } else if (arg == "--template-id" && i + 1 < argc) {
            cfg.chat_template_id = argv[++i];
        } else if (arg == "--help" || arg == "-h") {
            printUsage(argv[0]);
            return 0;
        } else {
            std::cerr << "Unbekannte/unvollstaendige Option: " << arg << "\n";
            return 2;
        }
    }

    if (cfg.timeout_sec < 1 || cfg.backend_port == 0 || cfg.model_id.empty() ||
        cfg.endpoint_path.empty() || cfg.endpoint_path[0] != '/') {
        std::cerr << "[MAG-FAIL] Ungueltige Endpunkt-/Timeout-Konfiguration\n";
        return 2;
    }
    try {
    if (mode == "--verify" || mode == "--infer") {
        if (cfg.weights_sha256_or_path == "UNATTESTED") {
            std::cerr << "[MAG-WARN] Gewichte nicht kryptografisch attestiert; optional --weights-sha256 angeben.\n";
        }
        if (!cfg.numa_cores.empty()) {
            std::cerr << "[MAG-HINWEIS] --numa setzt nur CLI-Affinity, nicht die CPU-Affinity des llama-server.\n";
        }
        MagDeterministicEngine engine(std::make_shared<HttpInferenceTransport>());
        std::cout << "[MAG] Starte 20x frische HTTP-Inferenz-Verifikation (kein Server-Kaltstart) gegen "
                  << cfg.backend_host << ":" << cfg.backend_port
                  << " (Modell: " << cfg.model_id << ")...\n"
                  << "[MAG] Konfigurations-Fingerprint: " << cfg.computeFingerprint() << "\n";

        // Verify the actual --infer input, not an unrelated default prompt.
        const std::string verify_prompt = mode == "--infer"
            ? user_prompt
            : "Erkläre in exakt zwei präzisen Sätzen, warum deterministische Zustandsautomaten reproduzierbar sind.";
        VerificationReport rep = engine.verifyModel20x(cfg, 32, verify_prompt);
        if (!rep.verified) {
            std::cerr << "[MAG-FAIL] Verifikation fehlgeschlagen (" << rep.completed_runs << "/20): "
                      << rep.failure_reason << "\n";
            return 1;
        }

        std::cout << "[MAG-OK] 20/20 Token-IDs und Text identisch (beobachtete HTTP-Inferenz, kein Kaltstart)!\n"
                  << "         Token-Stream-Hash: " << rep.baseline_token_hash << "\n"
                  << "         Gesamtdauer:       " << std::fixed << std::setprecision(2) << rep.total_duration_ms << " ms\n";

        if (mode == "--infer") {
            MagStatefulRoomContext room;
            room.deterministic_mode_enabled = true;
            room.system_instruction = "IGNORED_SYSTEM_PROMPT";
            room.memories = {"IGNORED_MEMORY_1", "IGNORED_MEMORY_2"};
            room.chat_history = {{"user", "vorherige nachricht"}, {"assistant", "vorherige antwort"}};

            RawInferenceResult res = engine.executeRoomInference(cfg, room, user_prompt, 32);
            if (!res.success) {
                std::cerr << "[MAG-FAIL] Inferenzfehler: " << res.error_message << "\n";
                return 1;
            }
            if (res.token_stream_sha256 != rep.baseline_token_hash ||
                res.token_ids != rep.baseline_tokens) {
                std::cerr << "[MAG-FAIL] Isolierter 21. Inferenzlauf weicht von den 20 Verifikationslaeufen ab\n";
                return 1;
            }
            std::cout << "[MAG-OK] 21. Inferenzlauf ebenfalls token- und textidentisch.\n"
                      << "\n--- STATELESS MODELL-ANTWORT ---\n"
                      << res.content << "\n"
                      << "--------------------------------\n"
                      << "Generierte Tokens (" << res.token_ids.size() << ") | Hash: " << res.token_stream_sha256 << "\n";
        }
        return 0;
    }

    // Container CPUsets may exclude CPUs 0..7; the selftest uses a valid
    // logical CPU without pretending to pin the inference-server process.
#if defined(__linux__)
    cpu_set_t allowed{};
    if (sched_getaffinity(0, sizeof(cpu_set_t), &allowed) == 0) {
        cfg.numa_cores.clear();
        for (int c = 0; c < CPU_SETSIZE; ++c)
            if (CPU_ISSET(c, &allowed)) { cfg.numa_cores.push_back(c); break; }
    }
#endif
    // Default: Run complete local verification and architecture self-test
    std::cout << "====================================================================\n"
              << " MAG DETERMINISTIC INFERENCE ENGINE - C++20 VERIFICATION SUITE\n"
              << "====================================================================\n\n";

    // 1. Test Stateless Isolation
    MagStatefulRoomContext dirty_room;
    dirty_room.deterministic_mode_enabled = true;
    dirty_room.system_instruction = "Du bist ein hilfreicher Assistent.";
    dirty_room.current_timestamp_iso = "2026-10-21T14:44:00Z";
    dirty_room.memories = {"Nutzer mag C++", "Server hat 2 NUMA Islands"};
    dirty_room.chat_history = {
        {"user", "Was haben wir gestern besprochen?"},
        {"assistant", "Wir haben über NUMA-Topologien gesprochen."}
    };

    std::string stateless_json = JsonUtils::buildRequestForRoom(cfg, dirty_room, "hi", 32);
    std::cout << "[Test 1] Stateless-Isolation (Verlauf, Memory, System-Prompt entfernt):\n"
              << stateless_json << "\n\n";

    // 2. Test 20/20 Deterministic Pass on Hardware Matrix Runner
    MagDeterministicEngine clean_engine(std::make_shared<LocalHardwareMatrixTransport>(/*inject_noise=*/false));
    VerificationReport pass_rep = clean_engine.verifyModel20x(cfg, 32);

    std::cout << "[Test 2] 20x Anfrage-Prüfung (Feste Reduktion & Client-CPU-Affinity):\n"
              << "         Status:      " << (pass_rep.verified ? "VERIFIZIERT (20/20 identisch)" : "FEHLGESCHLAGEN") << "\n"
              << "         Fingerprint: " << pass_rep.config_fingerprint << "\n"
              << "         Token-Hash:  " << pass_rep.baseline_token_hash << "\n"
              << "         Erste 6 IDs: ";
    for (size_t i = 0; i < std::min<size_t>(6, pass_rep.baseline_tokens.size()); ++i) {
        std::cout << pass_rep.baseline_tokens[i] << " ";
    }
    std::cout << "\n         Dauer (20x): " << std::fixed << std::setprecision(2) << pass_rep.total_duration_ms << " ms\n\n";

    // 3. Test Fail-Fast Early Exit on Token Divergence
    MagDeterministicEngine noisy_engine(std::make_shared<LocalHardwareMatrixTransport>(/*inject_noise=*/true));
    VerificationReport fail_rep = noisy_engine.verifyModel20x(cfg, 32);

    std::cout << "[Test 3] Fail-Fast Abbruch-Test bei Token-Abweichung in Lauf 4:\n"
              << "         Status:      " << (fail_rep.verified ? "VERIFIZIERT" : "NICHT VERIFIZIERT (Fail-Fast ausgelöst)") << "\n"
              << "         Läufe:       " << fail_rep.completed_runs << "/20 (Läufe 5..20 übersprungen)\n"
              << "         Grund:       " << fail_rep.failure_reason << "\n\n";

    // 4. Test Fingerprint Invalidation on Parameter Drift
    ModelDeterministicConfig mutated_cfg = cfg;
    mutated_cfg.thinking_enabled = true; // Flip one parameter
    RawInferenceResult blocked_res = clean_engine.executeRoomInference(mutated_cfg, dirty_room, "hi", 32);

    std::cout << "[Test 4] Automatische Sperre bei Parameter-/Fingerprint-Änderung:\n"
              << "         Ergebnis:    " << blocked_res.error_message << "\n"
              << "====================================================================\n";

    const bool isolated = stateless_json.find("IGNORED") == std::string::npos &&
        stateless_json.find("Erinnerungen") == std::string::npos &&
        stateless_json.find("gestern") == std::string::npos &&
        stateless_json.find("System") == std::string::npos &&
        stateless_json.find("\"role\": \"user\"") != std::string::npos &&
        stateless_json.find("\"role\": \"system\"") == std::string::npos;
    const bool sha_ok = Sha256::hash("abc") ==
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    const bool passed = sha_ok && pass_rep.verified && pass_rep.completed_runs == 20 &&
        !fail_rep.verified && fail_rep.completed_runs == 4 &&
        fail_rep.diverging_run_index == 4 &&
        !blocked_res.success && !blocked_res.error_message.empty() && isolated;
    std::cout << (passed ? "[MAG-SELFTEST] PASS (synthetisch, kein echter LLM-Nachweis)\n" :
                             "[MAG-SELFTEST] FAIL\n");
    return passed ? 0 : 1;
    } catch (const std::exception& e) {
        std::cerr << "[MAG-FAIL] " << e.what() << "\n";
        return 2;
    }
}