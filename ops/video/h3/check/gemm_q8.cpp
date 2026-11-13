// Checks gfx906's W8A8 GEMM for Q8_0 weights against the exact product of the
// same int8 values on the CPU: sizes that do not fill the 128-tiles, k not a
// multiple of the 128-step, an output row stride wider than m. Correctness
// only (no timing). MAG stack.
#include <hip/hip_runtime.h>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
#include "gemm_q8_gfx906.cuh"

using namespace gfx906_q8;
#define OK(x) do { if ((x) != hipSuccess) { std::printf("HIP error at %d\n", __LINE__); return 2; } } while (0)

static int run(int m, int n, int k, int stride, double& rel, double& rel_plain) {
    std::mt19937 rng(m * 7 + n + k);
    std::uniform_int_distribution<int> q(-127, 127);
    std::normal_distribution<float> normal(0.f, 1.f);
    const int kb = k / QK;
    std::vector<uint8_t> w(size_t(m) * kb * BLOCK_BYTES);
    for (int r = 0; r < m; ++r)
        for (int b = 0; b < kb; ++b) {
            uint8_t* blk = &w[(size_t(r) * kb + b) * BLOCK_BYTES];
            const _Float16 d = (_Float16) (0.001f + 0.01f * std::fabs(normal(rng)));
            std::memcpy(blk, &d, 2);
            for (int j = 0; j < QK; ++j) blk[2 + j] = uint8_t(int8_t(q(rng)));
        }
    std::vector<float> x(size_t(n) * k);
    for (auto& v : x) v = normal(rng) * (1.f + 4.f * (rng() % 50 == 0));   // a few outliers
    uint8_t* dw; float* dx; int8_t *dwq, *dxq; float *dws, *dxs, *dy;
    OK(hipMalloc(&dw, w.size())); OK(hipMalloc(&dx, x.size() * 4)); OK(hipMalloc(&dwq, size_t(m) * k)); OK(hipMalloc(&dxq, size_t(n) * k));
    OK(hipMalloc(&dws, size_t(m) * kb * 4)); OK(hipMalloc(&dxs, size_t(n) * kb * 4)); OK(hipMalloc(&dy, size_t(n) * stride * 4));
    OK(hipMemcpy(dw, w.data(), w.size(), hipMemcpyHostToDevice)); OK(hipMemcpy(dx, x.data(), x.size() * 4, hipMemcpyHostToDevice));
    OK(hipMemset(dy, 0, size_t(n) * stride * 4));
    repack<<<unsigned((int64_t(m) * kb + 255) / 256), 256>>>(dw, int64_t(kb) * BLOCK_BYTES, dwq, dws, int64_t(m) * kb, k);
    quantize_x<<<unsigned((int64_t(n) * kb + 255) / 256), 256>>>(dx, dxq, dxs, int64_t(n) * kb, k, k);
    gemm<<<dim3((n + BN - 1) / BN, (m + BM - 1) / BM), 256>>>(dwq, dws, dxq, dxs, dy, stride, m, n, k);
    OK(hipGetLastError()); OK(hipDeviceSynchronize());
    std::vector<int8_t> xq(size_t(n) * k); std::vector<float> xs(size_t(n) * kb), y(size_t(n) * stride);
    OK(hipMemcpy(xq.data(), dxq, xq.size(), hipMemcpyDeviceToHost));
    OK(hipMemcpy(xs.data(), dxs, xs.size() * 4, hipMemcpyDeviceToHost));
    OK(hipMemcpy(y.data(), dy, y.size() * 4, hipMemcpyDeviceToHost));
    double err = 0, norm = 0, perr = 0;
    for (int t = 0; t < n; ++t)
        for (int r = 0; r < m; ++r) {
            double ref = 0, plain = 0;
            for (int b = 0; b < kb; ++b) {
                const uint8_t* blk = &w[(size_t(r) * kb + b) * BLOCK_BYTES];
                _Float16 d; std::memcpy(&d, blk, 2);
                long dot = 0;
                for (int j = 0; j < QK; ++j) {
                    dot += long(int8_t(blk[2 + j])) * xq[size_t(t) * k + b * QK + j];
                    plain += double(d) * int8_t(blk[2 + j]) * x[size_t(t) * k + b * QK + j];
                }
                ref += double(d) * xs[size_t(t) * kb + b] * dot;
            }
            const double got = y[size_t(t) * stride + r];
            err += (got - ref) * (got - ref); norm += ref * ref; perr += (got - plain) * (got - plain);
        }
    rel = std::sqrt(err / norm); rel_plain = std::sqrt(perr / norm);
    hipFree(dw); hipFree(dx); hipFree(dwq); hipFree(dxq); hipFree(dws); hipFree(dxs); hipFree(dy);
    return 0;
}

int main() {
    struct { int m, n, k, stride; } cases[] = {{200, 150, 32 * 11, 200}, {130, 260, 32 * 4, 136}, {384, 384, 32 * 16, 384}, {64, 129, 32 * 5, 70}};
    bool ok = true;
    for (auto& c : cases) {
        double rel, plain;
        if (run(c.m, c.n, c.k, c.stride, rel, plain)) return 2;
        std::printf("m %d n %d k %d: exact on the int8 values %.2e, against fp32 activations %.4f\n", c.m, c.n, c.k, rel, plain);
        ok = ok && rel < 1e-5 && plain < 0.02;
    }
    std::printf("%s\n", ok ? "OK" : "WRONG");
    return ok ? 0 : 1;
}
