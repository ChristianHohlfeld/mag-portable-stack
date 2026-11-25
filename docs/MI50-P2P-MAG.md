# MAG: dual MI50 native PCIe P2P validation

Target host: Intel S2600CWR, dual Xeon E5-2680 v4, two MI50 32 GiB on NUMA0. Do not infer that both GPUs are already installed or visible.

## Firmware and kernel targets (not automatic deployment settings)

| Setting | Last reported MAG value | Candidate target |
|---|---|---|
| Above 4G Decoding | Enabled | Enabled |
| MMIO High Base | 56 TiB | 4 TiB |
| MMIO High Size | 256 GiB | 256 GiB |
| Framebuffer BAR per card | 16 GiB | 32 GiB / all VRAM visible |
| PCIe external links | Gen3 x16 on observed card | Gen3 x16 each, same NUMA node |
| Host bridge | Intel 8086:6f00 | REQ_SAME_HOST_BRIDGE support |

Evidence:
- AMD BAR address limits: https://rocm.docs.amd.com/en/develop/reference/system-optimization/bar-access-limits.html — both framebuffer and doorbell BARs must fit the GPU physical addressing limit. 16 TiB is exclusive, not a BIOS base recommendation.
- https://github.com/JeanP00l/mi50-qwen3.8-fast/blob/main/docs/en/hardware.md — reports 4T / 256G on another Xeon E5 platform, not the exact S2600CWR.
- https://github.com/DByte308/mi50-p2p-fixes — reports working low MMIO (1T / 1024G), full BAR and Broadwell host-bridge support on a different motherboard; increasing the DMA mask instead produced corruption.
- https://lists.openwall.net/linux-kernel/2026/10/07/1245 — Broadwell 8086:6f00 whitelist patch, restricted to the same host bridge.

Do not blindly write UEFI variables, modify GPU firmware, force a 48-bit DMA mask, globally disable ACS, or install another ROCm. Verify board revision, BIOS version, exact setting encoding, supported ReBAR sizes, and recovery capability on MAG before a separately authorized firmware change. The known working BIOS/kernel remain the rollback target. No firmware or kernel install is performed by the scripts in this repository. A locally built test kernel must be validated against the actual source/config before boot.

## Test sequence

1. Install both cards, verify the actual BDFs with lspci and their NUMA topology. Record stock inference performance before changing firmware or kernel.
2. Inspect the actual BAR addresses, sizes, visible VRAM, NUMA nodes and links (replace BDF_A/BDF_B with the observed full PCI addresses):
   ```sh
   bash ops/models/check-mi50-p2p.sh inspect BDF_A BDF_B
   ```
3. Only after the baseline and separate firmware approval, change MMIO base and inspect again after reboot. Verify full BAR support separately. If the running kernel lacks same-host-bridge support, test the prepared separate kernel with a working boot fallback. Never assume changing MMIO alone enables P2P.
4. Stop the GPU bot using its normal supervisor, ensure Idol is idle, then run as MAG owner:
   ```sh
   bash ops/models/check-mi50-p2p.sh test BDF_A BDF_B
   ```
   The script reuses MAG's existing ROCm 6.3.4, acquires the existing GPU lock and runs a bounded HIP test. It checks peer accessibility in both directions, then runs actual remote-memory loads and stores from GPU kernels using separate pseudo-random data for each direction and operation. This avoids treating a successful host-staged hipMemcpyPeer as proof. It reports GB/s and mismatches. Run only in a maintenance window: broken P2P may hang the GPU despite a userspace timeout. No services or firmware are changed.
5. Accept native P2P only if both GPU BARs are inside 44 bits, full VRAM is visible, both directions pass every data check and the active topology/kernel/IOMMU mode are understood. An IOMMU group alone does not establish whether address translation is active. The test proves kernel access to remote memory, not a particular physical routing path; confirm topology and translation settings separately.
6. Repeat the same dev_v2 inference benchmark with identical model, draft, prompt, context, seed, power cap and clocks. Report decode/prefill and correctness; use the existing native_p2p_verified configuration field only after verification.

See [GFX906-DEV-V2.md](GFX906-DEV-V2.md) for runtime configuration. Firmware settings and hardware changes remain host-side, not normal application deployment. This diagnostic has not yet been compiled/run on MAG.
