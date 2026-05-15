# Model Manifest

This bundle contains a full production model mirror, not just minimal test assets.

Snapshot highlights (from `config/manifests/model_sizes.txt` and local `du`):

- `models/` total: ~`60G`
- `models/diffusion_models`: ~`22G`
- `models/text_encoders`: ~`17G`
- `models/ollama/.ollama`: ~`9.7G`
- `models/clip`: ~`5.5G`
- `models/unet`: ~`5.3G`
- `models/vae`: ~`1.7G`

## Mandatory Regression Models (FLUX + WAN)

These are the core files required by current E2E generation tests:

- `models/unet/flux1-schnell-Q3_K_S.gguf`
- `models/clip/clip_l.safetensors`
- `models/clip/t5xxl_fp8_e4m3fn.safetensors`
- `models/vae/ae.safetensors`
- `models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`
- `models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`
- `models/vae/wan2.2_vae.safetensors`

## Ollama Store

Bundled from production path `/usr/share/ollama/.ollama` into:

- `models/ollama/.ollama`

Detected manifest entries include:

- `hf.co/unsloth/Qwen3-30B-A3B-GGUF/UD-IQ2_XXS`

## Integrity Files

- Model hashes: `config/manifests/model_sha256.txt` (74 entries)
- Infra hashes: `config/manifests/infra_sha256.txt` (config/code assets)

Use these for transfer verification after copying to another machine.
