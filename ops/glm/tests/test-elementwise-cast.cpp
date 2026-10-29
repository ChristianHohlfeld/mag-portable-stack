// Runtime gate for the GLM5-Next elementwise-weight cast (ops/glm/arch-compat.patch).
// Built against the pinned, patched llama.cpp tree by ops/qwen/build-runtime.sh.
//
// f32 + cast(iq4_nl -> f32) on the CPU backend must equal f32 + to_float() of the
// same quantized rows bit-for-bit, i.e. the cast is exactly a dequantize. (The
// uncast f32 + iq4_nl add aborts in the CPU binary kernel even though the CPU
// backend's supports_op accepts it, so it cannot be probed safely here.)
#include "ggml.h"
#include "ggml-cpu.h"
#include "ggml-backend.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>

static int fail(const char * msg) {
    std::fprintf(stderr, "FAIL %s\n", msg);
    return 1;
}

int main() {
    // Shape of blk.N.indexer_compressor_ape.weight is {n_embd_indexer, kpool};
    // any ne0 that is a multiple of the IQ4_NL block (32) exercises the path.
    const int64_t ne0 = 128, ne1 = 16, n_new = 3;
    const ggml_type qtype = GGML_TYPE_IQ4_NL;

    std::mt19937 rng(42);
    std::normal_distribution<float> dist(0.0f, 0.5f);
    std::vector<float> w(ne0 * ne1), x(ne0 * ne1 * n_new);
    for (auto & v : w) v = dist(rng);
    for (auto & v : x) v = dist(rng);

    const size_t row_q = ggml_row_size(qtype, ne0);
    std::vector<uint8_t> wq(row_q * ne1);
    ggml_quantize_chunk(qtype, w.data(), wq.data(), 0, ne1, ne0, nullptr);

    // reference: dequantize with the type traits, add in plain float
    std::vector<float> wd(ne0 * ne1);
    const auto * traits = ggml_get_type_traits(qtype);
    for (int64_t r = 0; r < ne1; ++r) {
        traits->to_float(wq.data() + r * row_q, wd.data() + r * ne0, ne0);
    }
    std::vector<float> ref(x.size());
    for (int64_t t = 0; t < n_new; ++t)
        for (int64_t i = 0; i < ne0 * ne1; ++i)
            ref[t * ne0 * ne1 + i] = x[t * ne0 * ne1 + i] + wd[i];

    ggml_init_params ip = { 64 * 1024 * 1024, nullptr, false };
    ggml_context * ctx = ggml_init(ip);
    ggml_tensor * tw = ggml_new_tensor_2d(ctx, qtype, ne0, ne1);
    ggml_tensor * tx = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, ne0, ne1, n_new);
    std::memcpy(tw->data, wq.data(), wq.size());
    std::memcpy(tx->data, x.data(), x.size() * sizeof(float));

    ggml_backend_t cpu = ggml_backend_cpu_init();
    if (!cpu) return fail("cpu backend init");

    // the patched graph shape: add(x, cast(w, F32))
    ggml_tensor * out = ggml_add(ctx, tx, ggml_cast(ctx, tw, GGML_TYPE_F32));
    ggml_cgraph * gf = ggml_new_graph(ctx);
    ggml_build_forward_expand(gf, out);
    ggml_backend_cpu_set_n_threads(cpu, 2);
    if (ggml_backend_graph_compute(cpu, gf) != GGML_STATUS_SUCCESS) return fail("graph compute");

    const float * got = (const float *) out->data;
    for (size_t i = 0; i < ref.size(); ++i) {
        if (std::memcmp(&got[i], &ref[i], sizeof(float)) != 0) {
            std::fprintf(stderr, "FAIL mismatch at %zu: %.9g vs %.9g\n", i, got[i], ref[i]);
            return 1;
        }
    }
    // sanity: IQ4_NL is lossy but must stay close to the original weights
    double err = 0, norm = 0;
    for (size_t i = 0; i < w.size(); ++i) { err += (wd[i] - w[i]) * (wd[i] - w[i]); norm += w[i] * w[i]; }
    if (std::sqrt(err / norm) > 0.2) return fail("iq4_nl round trip error unexpectedly large");

    ggml_backend_free(cpu);
    ggml_free(ctx);
    std::printf("PASS f32 + cast(%s->f32) == f32 + dequantize (bit-exact, %zu values)\n",
                ggml_type_name(qtype), ref.size());
    return 0;
}
