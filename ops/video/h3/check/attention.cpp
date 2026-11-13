// Checks sage_gfx906's attention against exact attention on the CPU, on small
// random tensors (no model, no weights): lengths that are not multiples of
// 64, two query heads per key head, K with a large common offset (its mean is
// taken out on the card). Prints the worst relative error per head and
// exits 1 above the bound. Build: ops/video/h3/check.sh. MAG stack.
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>
#include "sage_gfx906_kernels.h"

using namespace sd_sage906;

#define OK(x) do { if ((x) != hipSuccess) { std::printf("HIP error %s at %d\n", hipGetErrorString(x), __LINE__); return 2; } } while (0)

int main() {
    const int lq = 150, lk = 133, hq = 4, hk = 2, batch = 1, group = hq / hk;
    const float scale = 1.f / std::sqrt(float(D));
    std::mt19937 rng(7);
    std::normal_distribution<float> normal(0.f, 1.f);
    std::vector<float> q(size_t(lq) * D * hq * batch), k(size_t(lk) * D * hk * batch);
    std::vector<_Float16> v(size_t(lk) * D * hk * batch);
    for (auto& x : q) x = 2.f * normal(rng);
    for (size_t i = 0; i < k.size(); ++i) k[i] = 2.f * normal(rng) + 3.f * float(i % D % 5);   // a common offset per channel
    for (auto& x : v) x = (_Float16)normal(rng);

    const int qb = (lq + BQ - 1) / BQ, kb = (lk + BK - 1) / BK;
    float *dq, *dk, *dqs, *dks, *dkm, *dout;
    int8_t *dqi, *dki;
    _Float16* dv;
    OK(hipMalloc(&dq, q.size() * 4)); OK(hipMalloc(&dk, k.size() * 4)); OK(hipMalloc(&dv, v.size() * 2));
    OK(hipMalloc(&dqi, size_t(qb) * BQ * D * hq * batch)); OK(hipMalloc(&dki, size_t(kb) * BK * D * hk * batch));
    OK(hipMalloc(&dqs, qb * BQ * hq * batch * 4)); OK(hipMalloc(&dks, kb * hk * batch * 4)); OK(hipMalloc(&dkm, D * hk * batch * 4));
    OK(hipMalloc(&dout, size_t(lq) * D * hq * batch * 4));
    OK(hipMemcpy(dq, q.data(), q.size() * 4, hipMemcpyHostToDevice));
    OK(hipMemcpy(dk, k.data(), k.size() * 4, hipMemcpyHostToDevice));
    OK(hipMemcpy(dv, v.data(), v.size() * 2, hipMemcpyHostToDevice));
    key_mean<<<dim3(hk * batch), D>>>(dk, dkm, lk);
    quantize_rows<<<dim3(qb, hq * batch), 256>>>(dq, dqi, dqs, lq);
    quantize<<<dim3(kb, hk * batch), 256>>>(dk, dkm, dki, dks, lk);
    attend<<<dim3(qb, hq * batch), 256>>>(dqi, dki, dv, dqs, dks, dout, lq, lk, hq, group, batch, scale * 1.4426950408889634f);
    OK(hipGetLastError());
    OK(hipDeviceSynchronize());
    std::vector<float> out(size_t(lq) * D * hq * batch);
    OK(hipMemcpy(out.data(), dout, out.size() * 4, hipMemcpyDeviceToHost));

    // Exact attention in double; output layout [D, heads, tokens, batch].
    double worst = 0.0;
    for (int b = 0; b < batch; ++b)
        for (int h = 0; h < hq; ++h) {
            const int kh = b * hk + h / group;
            double err = 0.0, ref_norm = 0.0;
            for (int i = 0; i < lq; ++i) {
                const float* qi = &q[(size_t(b * hq + h) * lq + i) * D];
                std::vector<double> s(lk);
                double top = -1e300;
                for (int j = 0; j < lk; ++j) {
                    const float* kj = &k[(size_t(kh) * lk + j) * D];
                    double dot = 0.0;
                    for (int c = 0; c < D; ++c) dot += double(qi[c]) * kj[c];
                    s[j] = dot * scale;
                    top = std::max(top, s[j]);
                }
                double sum = 0.0;
                for (auto& x : s) { x = std::exp(x - top); sum += x; }
                for (int c = 0; c < D; ++c) {
                    double ref = 0.0;
                    for (int j = 0; j < lk; ++j) ref += s[j] / sum * double(v[(size_t(kh) * lk + j) * D + c]);
                    const double got = out[((size_t(b) * lq + i) * hq + h) * D + c];
                    err += (got - ref) * (got - ref);
                    ref_norm += ref * ref;
                }
            }
            const double rel = std::sqrt(err / ref_norm);
            std::printf("head %d: relative error %.4f\n", h, rel);
            worst = std::max(worst, rel);
        }
    // int8 Q and K (as SageAttention) cost about a percent; a wrong kernel is far off.
    std::printf("worst %.4f -> %s\n", worst, worst < 0.05 ? "OK" : "WRONG");
    return worst < 0.05 ? 0 : 1;
}
