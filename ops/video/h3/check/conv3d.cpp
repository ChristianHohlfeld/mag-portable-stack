// Checks gfx906's implicit-GEMM Conv3d against a Conv3d in double on the CPU:
// channel counts that do not fill the 64-tiles, k not a multiple of 64,
// split over k, aligned and unaligned weights, 3x3x3 and 1x1x3 kernels, on
// ggml's padded fp16 input. MAG stack.
#include <hip/hip_runtime.h>
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>
#include "conv3d_dot2_gfx906.cuh"

struct Params {
    int64_t IW, IH, ID, OW, OH, OD, KW, KH, KD, ST_X, ST_Y, ST_Z, PD_X, PD_Y, PD_Z, DL_X, DL_Y, DL_Z, IC, OC, B, TOTAL;
};
#define OK(x) do { if ((x) != hipSuccess) { std::printf("HIP error at %d\n", __LINE__); return -1.0; } } while (0)

static double run(int ic, int oc, int kw, int kh, int kd, int split_k) {
    const int b = 2, iw = 9, ih = 7, id = 5, pad = kw == 3 ? 1 : 0, padd = kd == 3 ? 1 : 0;
    const int pw = iw + 2 * pad, ph = ih + 2 * (kh == 3 ? 1 : 0), pd = id + 2 * padd;
    const int ow = pw - kw + 1, oh = ph - kh + 1, od = pd - kd + 1, positions = ow * oh * od, kt = ic * kw * kh * kd;
    std::mt19937 rng(ic * 131 + oc);
    std::uniform_real_distribution<float> u(-1.f, 1.f);
    std::vector<_Float16> x(size_t(b) * ic * pd * ph * pw), w(size_t(oc) * kt);
    for (auto& v : x) v = (_Float16) u(rng);
    for (auto& v : w) v = (_Float16) (u(rng) / std::sqrt(float(kt)));
    Params P = {pw, ph, pd, ow, oh, od, kw, kh, kd, 1, 1, 1, 0, 0, 0, 1, 1, 1, ic, oc, b, int64_t(b) * oc * positions};
    _Float16 *dx, *dw;
    float* dy;
    OK(hipMalloc(&dx, x.size() * 2)); OK(hipMalloc(&dw, w.size() * 2)); OK(hipMalloc(&dy, size_t(split_k) * P.TOTAL * 4));
    OK(hipMemcpy(dx, x.data(), x.size() * 2, hipMemcpyHostToDevice));
    OK(hipMemcpy(dw, w.data(), w.size() * 2, hipMemcpyHostToDevice));
    const bool aligned = kt % 8 == 0;
    dim3 grid((positions + 63) / 64, (oc + 63) / 64, b * split_k);
    if (kw == 3) conv3d_implicit_gemm_dot2<3, 3, 3><<<grid, 256>>>(dx, dw, dy, P, split_k, aligned);
    else conv3d_implicit_gemm_dot2<1, 1, 3><<<grid, 256>>>(dx, dw, dy, P, split_k, aligned);
    OK(hipGetLastError());
    OK(hipDeviceSynchronize());
    std::vector<float> part(size_t(split_k) * P.TOTAL);
    OK(hipMemcpy(part.data(), dy, part.size() * 4, hipMemcpyDeviceToHost));
    double err = 0.0, norm = 0.0;
    for (int n = 0; n < b; ++n)
        for (int co = 0; co < oc; ++co)
            for (int z = 0; z < od; ++z)
                for (int y = 0; y < oh; ++y)
                    for (int xx = 0; xx < ow; ++xx) {
                        double ref = 0.0;
                        for (int ci = 0; ci < ic; ++ci)
                            for (int kz = 0; kz < kd; ++kz)
                                for (int ky = 0; ky < kh; ++ky)
                                    for (int kx = 0; kx < kw; ++kx)
                                        ref += double(x[(((size_t(n) * ic + ci) * pd + z + kz) * ph + y + ky) * pw + xx + kx]) *
                                               double(w[(((size_t(co) * ic + ci) * kd + kz) * kh + ky) * kw + kx]);
                        const int p = (z * oh + y) * ow + xx;
                        double got = 0.0;   // partial slices [batch, split, channel, position], as ggml's reduction reads them
                        for (int s = 0; s < split_k; ++s) got += part[((size_t(n) * split_k + s) * oc + co) * positions + p];
                        err += (got - ref) * (got - ref);
                        norm += ref * ref;
                    }
    hipFree(dx); hipFree(dw); hipFree(dy);
    return std::sqrt(err / norm);
}

int main() {
    struct Case { int ic, oc, kw, kh, kd, split; } cases[] = {{40, 70, 3, 3, 3, 1}, {40, 70, 3, 3, 3, 3}, {5, 9, 3, 3, 3, 2}, {48, 130, 1, 1, 3, 1}};
    double worst = 0.0;
    for (const auto& c : cases) {
        const double e = run(c.ic, c.oc, c.kw, c.kh, c.kd, c.split);
        std::printf("ic %d oc %d kernel %dx%dx%d split %d: relative error %.2e\n", c.ic, c.oc, c.kw, c.kh, c.kd, c.split, e);
        if (e < 0) return 2;
        worst = std::max(worst, e);
    }
    // fp16 inputs, fp32 sums: a correct kernel is at the level of fp32 rounding.
    std::printf("worst %.2e -> %s\n", worst, worst < 1e-5 ? "OK" : "WRONG");
    return worst < 1e-5 ? 0 : 1;
}
