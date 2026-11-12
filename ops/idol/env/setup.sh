#!/usr/bin/env bash
# The Idol worker's environment on a new host, from pins in this repository:
# ~/idol-mi50 with the ROCm torch venv, idol-sdk at its commit with the MI50
# patch (Apache-2.0, speridlabs), TripoSG at its commit (MIT, VAST-AI) and the
# weights. Run as root; the files belong to MAG_USER (default chris). Leaves
# what is already there alone. The gated Idol weights need a Hugging Face
# token in /etc/mag-stack/hf-token (template: ops/install/secrets.md).
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
here=$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
user=${MAG_USER:-chris}
BASE=$(getent passwd "$user" | cut -d: -f6)/idol-mi50
as() { sudo -u "$user" -H "$@"; }
IDOL_SDK_COMMIT=4702a7f00850e34b6e83a2170cc123c3e9570744
TRIPOSG_COMMIT=fc5c409
install -d -o "$user" -g "$user" "$BASE" "$BASE/weights"
# 1. The venv: torch 2.7.1 for ROCm 6.3 (gfx906 works with it), then the rest exactly.
if [[ ! -x $BASE/venv/bin/python ]]; then
  as python3 -m venv "$BASE/venv"
  as "$BASE/venv/bin/pip" install -q --index-url https://download.pytorch.org/whl/rocm6.3 \
    torch==2.7.1+rocm6.3 torchvision==0.22.1+rocm6.3 pytorch-triton-rocm==3.3.1
  as "$BASE/venv/bin/pip" install -q --no-deps -r "$here/requirements.lock"
fi
# 2. idol-sdk with the MI50 patch, editable.
if [[ ! -d $BASE/idol-sdk/.git ]]; then
  as git clone -q https://github.com/speridlabs/idol-sdk.git "$BASE/idol-sdk"
  as git -C "$BASE/idol-sdk" checkout -q "$IDOL_SDK_COMMIT"
  as git -C "$BASE/idol-sdk" apply "$here/idol-sdk-mi50.patch"
  as "$BASE/venv/bin/pip" install -q --no-deps -e "$BASE/idol-sdk"
fi
# 3. TripoSG's code (its environment is made by ops/idol/install.sh).
if [[ ! -d $BASE/triposg/src/.git ]]; then
  install -d -o "$user" -g "$user" "$BASE/triposg"
  as git clone -q https://github.com/VAST-AI-Research/TripoSG.git "$BASE/triposg/src"
  as git -C "$BASE/triposg/src" checkout -q "$TRIPOSG_COMMIT"
fi
# 4. Weights, each set fetched once and resumable (curl -C -).
get() { # url file [token]
  [[ -s $2 && ! -e $2.part ]] && return 0
  as install -d "$(dirname "$2")"
  local auth=()
  [[ -z ${3:-} ]] || auth=(-H "Authorization: Bearer $3")
  as curl -fL --retry 10 --retry-delay 5 -C - "${auth[@]}" -o "$2.part" "$1" && as mv "$2.part" "$2"
}
W=$BASE/weights
token=$(cat /etc/mag-stack/hf-token 2>/dev/null || true)
if [[ -n $token ]]; then
  for f in config.json denoiser.safetensors decoder.safetensors decoder_gs.safetensors birefnet.safetensors; do
    get "https://huggingface.co/speridlabs/idol/resolve/main/$f" "$W/idol/$f" "$token"
  done
else
  echo 'idol: /etc/mag-stack/hf-token missing; the gated Idol weights are not fetched (photo to 3D stays off)' >&2
fi
for f in config.json diffusion_pytorch_model.safetensors; do
  get "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/main/vae/$f" "$W/flux2vae/vae/$f"
done
for f in config.json model.safetensors LICENSE.md; do
  get "https://huggingface.co/timm/vit_huge_plus_patch16_dinov3.lvd1689m/resolve/main/$f" "$W/dinov3-timm/$f"
done
R=https://huggingface.co/VAST-AI/TripoSG/resolve/2c1c516d22d58db486a058d98d31bb6177344e06
for f in transformer/diffusion_pytorch_model.safetensors vae/diffusion_pytorch_model.safetensors image_encoder_dinov2/model.safetensors \
         transformer/config.json vae/config.json image_encoder_dinov2/config.json feature_extractor_dinov2/preprocessor_config.json \
         scheduler/scheduler_config.json model_index.json; do
  get "$R/$f" "$W/triposg/$f"
done
# Pictures (FLUX.2 klein) and videos (Wan 2.2) have fetch scripts of their own.
as bash "$here/../../imagegen/fetch.sh" || echo 'idol: picture weights incomplete; run ops/imagegen/fetch.sh again' >&2
as bash "$here/../../video/fetch.sh" || echo 'idol: video weights incomplete; run ops/video/fetch.sh again' >&2
echo "idol: environment in $BASE"
