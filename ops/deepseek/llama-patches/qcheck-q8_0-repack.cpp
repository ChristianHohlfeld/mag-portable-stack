// Prüft den AVX2-Q8_0-Repack (mag-Patch): Ergebnis mit Repack-Puffer == Ergebnis ohne (Referenz-vec_dot),
// für GEMV (n=1), kleine n (Rest-Zeilen), GEMM (n=4k und n=4k+r), dazu Geschwindigkeit. Exit 1 bei Abweichung.
#include "ggml.h"
#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>

struct Res { std::vector<float> y; double gflops = 0; bool repacked = false; };

static Res run(ggml_type type, int M, int K, int N, bool repack, int threads, int reps, unsigned seed) {
    Res r;
    ggml_backend_t be = ggml_backend_cpu_init();
    ggml_backend_cpu_set_n_threads(be, threads);
    ggml_backend_dev_t dev = ggml_backend_get_device(be);
    ggml_backend_reg_t reg = ggml_backend_dev_backend_reg(dev);
    ggml_backend_buffer_type_t wbuft = ggml_backend_cpu_buffer_type();
    if (repack) {
        auto get_extra = (ggml_backend_dev_get_extra_bufts_t) ggml_backend_reg_get_proc_address(reg, "ggml_backend_dev_get_extra_bufts");
        for (ggml_backend_buffer_type_t *ex = get_extra ? get_extra(dev) : nullptr; ex && *ex; ++ex)
            if (strstr(ggml_backend_buft_name(*ex), "REPACK")) { wbuft = *ex; r.repacked = true; break; }
    }
    ggml_init_params wp = {ggml_tensor_overhead() * 4, nullptr, true};
    ggml_context *wctx = ggml_init(wp);
    ggml_tensor *W = ggml_new_tensor_2d(wctx, type, K, M);
    ggml_backend_buffer_t wbuf = ggml_backend_alloc_ctx_tensors_from_buft(wctx, wbuft);
    std::mt19937 rng(seed);
    std::normal_distribution<float> nd(0.f, 1.f);
    std::vector<float> f((size_t) M * K); for (auto &v : f) v = nd(rng);
    std::vector<uint8_t> q(ggml_row_size(type, K) * M);
    ggml_quantize_chunk(type, f.data(), q.data(), 0, M, K, nullptr);
    ggml_backend_tensor_set(W, q.data(), 0, q.size());

    ggml_init_params cp = {ggml_tensor_overhead() * 8 + ggml_graph_overhead(), nullptr, true};
    ggml_context *ctx = ggml_init(cp);
    ggml_tensor *X = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, K, N);
    ggml_tensor *Y = ggml_mul_mat(ctx, W, X);
    ggml_cgraph *gf = ggml_new_graph(ctx);
    ggml_build_forward_expand(gf, Y);
    ggml_backend_buffer_t abuf = ggml_backend_alloc_ctx_tensors(ctx, be);
    std::vector<float> xv((size_t) K * N); for (auto &v : xv) v = nd(rng);
    ggml_backend_tensor_set(X, xv.data(), 0, xv.size() * sizeof(float));
    ggml_backend_graph_compute(be, gf);
    r.y.resize((size_t) M * N);
    ggml_backend_tensor_get(Y, r.y.data(), 0, r.y.size() * sizeof(float));
    if (reps > 0) {
        auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < reps; i++) ggml_backend_graph_compute(be, gf);
        double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() / reps;
        r.gflops = 2.0 * M * K * N / s / 1e9;
    }
    ggml_backend_buffer_free(abuf); ggml_free(ctx);
    ggml_backend_buffer_free(wbuf); ggml_free(wctx); ggml_backend_free(be);
    return r;
}

int main(int argc, char **argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    int threads = argc > 1 ? atoi(argv[1]) : 2;
    int bad = 0;
    struct Case { int M, K, N, reps; } cases[] = {
        {64, 256, 1, 0}, {64, 256, 3, 0}, {64, 256, 4, 0}, {64, 256, 7, 0}, {128, 512, 16, 0}, {256, 4096, 33, 0},
        {4096, 4096, 1, 30}, {4096, 4096, 512, 2}, {4096, 14336, 1, 20}, {14336, 4096, 1, 20}, {4096, 14336, 64, 3},
    };
    printf("%-22s %10s %12s %12s %8s\n", "M x K x N", "max rel", "GF normal", "GF repack", "repack?");
    for (auto &c : cases) {
        Res a = run(GGML_TYPE_Q8_0, c.M, c.K, c.N, false, threads, c.reps, 7);
        Res b = run(GGML_TYPE_Q8_0, c.M, c.K, c.N, true, threads, c.reps, 7);
        double mx = 0, ref = 0;
        for (size_t i = 0; i < a.y.size(); i++) { mx = std::max(mx, (double) fabsf(a.y[i] - b.y[i])); ref = std::max(ref, (double) fabsf(a.y[i])); }
        double rel = mx / (ref > 0 ? ref : 1);
        char nm[32]; snprintf(nm, sizeof nm, "%dx%dx%d", c.M, c.K, c.N);
        printf("%-22s %10.2e %12.1f %12.1f %8s\n", nm, rel, a.gflops, b.gflops, b.repacked ? "ja" : "NEIN");
        if (!(rel < 1e-5) || !b.repacked) bad = 1;
    }
    printf(bad ? "FEHLER: Abweichung oder kein Repack\n" : "ok: Repack-Ergebnis identisch mit Referenz\n");
    return bad;
}
