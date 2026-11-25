# Experimental gfx906 dev_v2 runtime (not enabled by default)

Pinned source: https://github.com/AlwaysLearning-dev/gfx906-llama-cpp/tree/36e84ddb7e5792af6994ee79e12452ba4c918e63 (dev_v2).
The production CPU and HIP runtime revision is unchanged. No model files, BIOS, kernel, ROCm packages or systemd units are changed by the experimental build. Do not deploy this branch before review and acceptance.

## Build
As MAG owner, reuse the existing MAG ROCm 6.3.4 installation:
```sh
bash ops/models/build-gfx906-dev-v2.sh
```
The build uses the same ROCm 6.3.4 path as the existing MAG HIP runtime. The fork's reference build used ROCm 7.1.1; compatibility with 6.3.4 is not yet established. If compilation fails, investigate the specific incompatibility before considering a single shared ROCm upgrade. Do not install a second ROCm distribution or change Idol. The build checks the exact source revision, applies the ported MAG shutdown patch with git apply --check, builds in a fresh directory and checks the CLI flags. Binaries are staged under the separate pinned runtime directory. No install or activation happens automatically.

## Acceptance procedure
* On the branch run `bash -n ops/models/build-gfx906-dev-v2.sh ops/models/run-mi50.sh` and the existing MAG CI suite; build the experimental runtime. A successful source-context comparison is not a build.
* In a scheduled maintenance window, stop only the GPU bot through the normal supervisor; keep Idol/CPU bots intact. Use the existing GPU guard for any live experimental server, never run a second server on occupied GPUs. Verify SIGTERM during idle, prefill, generation, MTP and DFlash; verify Idol demand preempts the process and releases both GPUs and that the server resumes. Test /health, streaming chat completions, reasoning, vision, slot save/restore, model status and cancellation. Test restart and rollback to upstream.
* With two cards physically on the same NUMA node, first measure a baseline without verified native P2P (`devices: [0,1]`, `native_p2p_verified: false`). Check topology, BARs, HIP peer accessibility and driver errors; record the actual transfer path. After any separately approved firmware/kernel changes, verify native peer transfers in both directions and measure latency and bandwidth before repeating the identical inference benchmark with `native_p2p_verified: true`. The setting does not force HIP peer access on or off; hardware/runtime measurements determine the actual path.
* Compare upstream against dev_v2 with the exact same GGUF, context, seed, clocks, power limits, batching, and prompt. Record prefill, decode, acceptance rate, VRAM, errors and output quality. For one and two MI50 compare no draft / embedded MTP / matching DFlash2, and with Q8_0 repacking on/off. The reported 58.5 t/s was for Q8_0 with DFlash2 on other hardware; it is not a MAG result. Repacking is a Q8_0 path, not a promise of Q6 acceleration.

## Explicit opt-in only after acceptance
In a reviewed test configuration for the existing Qwen GPU bot (do not edit the production config until accepted), add:
```json
{
  "gpu_runtime_variant": "gfx906-dev-v2",
  "gfx906_experiment": {
    "validated": true,
    "devices": [0],
    "native_p2p_verified": false,
    "q8_repack": true
  }
}
```
These fields are absent from the committed default configuration. The validation booleans are operator attestations, not automated P2P detection. For two cards, set devices to [0,1] and explicitly set native_p2p_verified to false for the baseline, or true only after actual native peer transfer verification. Missing or non-boolean values are rejected. The existing validated opt-in remains required; use a test configuration for the baseline, not production. HIP ordinals must be checked on every changed hardware topology. The shared GPU lock protects both devices and retains Idol priority. Existing MTP stays the default when available. To evaluate DFlash2 add a dflash object with absolute path and exact SHA256 for a compatible draft GGUF to gfx906_experiment; it is exclusive with MTP. No weights are downloaded or deleted. Repacking can be explicitly disabled. Experimental slot files use a separate directory; modeld advertises that directory and retains the shared guard status location.

Rollback: remove the experimental fields, retain the original pinned runtime, and use the regular Qwen stop/restart path. Do not move or delete model files. No changes to Blake or Noah's configuration.

## Status
Ported shutdown logic against the pinned fork source; no host compile, HIP benchmark, API or preemption test has been performed here. This is a draft for on-host validation, not a production release.
