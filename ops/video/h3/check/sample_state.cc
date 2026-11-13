// Checks the sampler state on disk (sample_state.hpp) on the CPU: a state
// written after a step is read back bit for bit; it is refused for another
// shape or other sigmas; a run with SD_STATE_DIR unset reads and writes
// nothing. Built and run by check.sh. MAG stack.
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include "runtime/sample_state.hpp"

int main(int argc, char** argv) {
    const std::string dir = argc > 1 ? argv[1] : "/tmp";
    setenv("SD_STATE_DIR", dir.c_str(), 1);
    std::remove((dir + "/sample.state").c_str());
    const std::vector<float> sigmas = {1.f, 0.96f, 0.75f, 0.31f, 0.f};
    sd::Tensor<float> x({5, 3, 2});
    for (int64_t i = 0; i < x.numel(); ++i) x.data()[i] = 0.25f * float(i) - 3.f;
    sd_state::save(x, 2, sigmas);
    sd::Tensor<float> y({5, 3, 2}), wrong({6, 3, 2});
    int step = 0, other = 0;
    bool ok = sd_state::load(y, step, sigmas) && step == 2;
    for (int64_t i = 0; ok && i < x.numel(); ++i) ok = x.data()[i] == y.data()[i];
    const bool refuses_shape = !sd_state::load(wrong, other, sigmas);
    const bool refuses_sigmas = !sd_state::load(y, other, {1.f, 0.5f, 0.f});
    unsetenv("SD_STATE_DIR");
    sd::Tensor<float> z({5, 3, 2});
    const bool off = !sd_state::load(z, other, sigmas);
    std::printf("round trip %s, other shape refused %s, other sigmas refused %s, unset ignored %s\n",
                ok ? "yes" : "NO", refuses_shape ? "yes" : "NO", refuses_sigmas ? "yes" : "NO", off ? "yes" : "NO");
    const bool all = ok && refuses_shape && refuses_sigmas && off;
    std::printf("%s\n", all ? "OK" : "WRONG");
    return all ? 0 : 1;
}
