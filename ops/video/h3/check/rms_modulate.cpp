// Checks the fused RMSNorm + weight + AdaLN modulation against the unfused
// math in double on the CPU: rows of 300 (not a multiple of the block), a
// strided x, scale broadcast per batch, shift for all. MAG stack.
#include <hip/hip_runtime.h>
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>
#include "rms_modulate_kernels.h"

using namespace sd_rms_modulate;
#define OK(x) do { if ((x) != hipSuccess) { std::printf("HIP error at %d\n", __LINE__); return 2; } } while (0)

int main() {
    const int64_t n0 = 300, n1 = 37, n2 = 1, n3 = 2, pitch = 320;   // x rows are 320 floats apart
    std::mt19937 rng(3);
    std::normal_distribution<float> normal(0.f, 1.f);
    std::vector<float> x(size_t(pitch) * n1 * n2 * n3), w(n0), scale(size_t(n0) * n3), shift(n0);
    for (auto& v : x) v = 3.f * normal(rng);
    for (auto& v : w) v = 1.f + 0.2f * normal(rng);
    for (auto& v : scale) v = 0.3f * normal(rng);
    for (auto& v : shift) v = 0.5f * normal(rng);
    Parameters p = {};
    const int64_t ne[4] = {n0, n1, n2, n3}, xs[4] = {1, pitch, pitch * n1, pitch * n1 * n2};
    const int64_t sne[4] = {n0, 1, 1, n3}, sst[4] = {1, n0, n0, n0};
    const int64_t hne[4] = {n0, 1, 1, 1}, hst[4] = {1, n0, n0, n0};
    for (int d = 0; d < 4; ++d) {
        p.ne[d] = ne[d]; p.x_stride[d] = xs[d];
        p.scale_ne[d] = sne[d]; p.scale_stride[d] = sst[d];
        p.shift_ne[d] = hne[d]; p.shift_stride[d] = hst[d];
    }
    p.rows = n1 * n2 * n3;
    p.eps = 1e-6f;
    float *dx, *dw, *ds, *dh, *dout;
    OK(hipMalloc(&dx, x.size() * 4)); OK(hipMalloc(&dw, w.size() * 4)); OK(hipMalloc(&ds, scale.size() * 4));
    OK(hipMalloc(&dh, shift.size() * 4)); OK(hipMalloc(&dout, size_t(p.rows) * n0 * 4));
    OK(hipMemcpy(dx, x.data(), x.size() * 4, hipMemcpyHostToDevice));
    OK(hipMemcpy(dw, w.data(), w.size() * 4, hipMemcpyHostToDevice));
    OK(hipMemcpy(ds, scale.data(), scale.size() * 4, hipMemcpyHostToDevice));
    OK(hipMemcpy(dh, shift.data(), shift.size() * 4, hipMemcpyHostToDevice));
    rms_mul_modulate<<<unsigned(p.rows), 256>>>(dx, dw, ds, dh, dout, p);
    OK(hipGetLastError());
    OK(hipDeviceSynchronize());
    std::vector<float> out(size_t(p.rows) * n0);
    OK(hipMemcpy(out.data(), dout, out.size() * 4, hipMemcpyDeviceToHost));
    double worst = 0.0;
    for (int64_t i3 = 0; i3 < n3; ++i3)
        for (int64_t i1 = 0; i1 < n1; ++i1) {
            const float* row = &x[size_t(i3 * xs[3] + i1 * xs[1])];
            double sum = 0.0;
            for (int64_t c = 0; c < n0; ++c) sum += double(row[c]) * row[c];
            const double r = 1.0 / std::sqrt(sum / n0 + 1e-6);
            for (int64_t c = 0; c < n0; ++c) {
                const double y = row[c] * r * w[c];
                const double ref = y * (1.0 + scale[size_t(i3) * n0 + c]) + shift[c];
                const double got = out[size_t((i3 * n1 + i1) * n0 + c)];
                worst = std::max(worst, std::fabs(got - ref) / (std::fabs(ref) + 1e-3));
            }
        }
    std::printf("worst relative error %.2e -> %s\n", worst, worst < 1e-4 ? "OK" : "WRONG");
    return worst < 1e-4 ? 0 : 1;
}
