#!/usr/bin/env python3
"""Idol MI50 adapter: uses upstream pipeline, local encoders, explicit FP16."""
import argparse
import json
import os
from pathlib import Path
import torch
import torch.nn.functional as F
from transformers import AutoModel
from diffusers.models import AutoencoderKLFlux2
from idol import pipeline as pipeline_module
from idol.conditioning import IMAGENET_MEAN, IMAGENET_STD
from tensordict import TensorDict

class MI50Encoder:
    """Override upstream's hard-coded BF16 VAE on gfx906 without editing idol-sdk."""
    def __init__(self, dinov3_repo, flux2_vae_repo, dinov3_dtype="fp16", device="cuda:0"):
        self.device=torch.device(device)
        self.dinov3=AutoModel.from_pretrained(dinov3_repo, torch_dtype=torch.float16)
        self.dinov3=self.dinov3.eval().requires_grad_(False).to(self.device)
        self.flux2_vae=AutoencoderKLFlux2.from_pretrained(flux2_vae_repo, subfolder="vae",
                                                          torch_dtype=torch.float16)
        self.flux2_vae=self.flux2_vae.eval().requires_grad_(False).to(self.device)

    @torch.no_grad()
    def __call__(self, images):
        images=images.to(self.device)
        mean=torch.tensor(IMAGENET_MEAN,device=self.device).view(1,3,1,1)
        std=torch.tensor(IMAGENET_STD,device=self.device).view(1,3,1,1)
        normalized=(images.float()-mean)/std
        feature1=self.dinov3(pixel_values=normalized.to(torch.float16)).last_hidden_state.float()
        feature1=F.layer_norm(feature1,feature1.shape[-1:])
        vae=self.flux2_vae
        latents=vae.encode(images.to(torch.float16)*2-1).latent_dist.sample()
        batch,channels,height,width=latents.shape
        latents=latents.view(batch,channels,height//2,2,width//2,2).permute(0,1,3,5,2,4)
        latents=latents.reshape(batch,channels*4,height//2,width//2)
        mean=vae.bn.running_mean.view(1,-1,1,1).to(latents.device,latents.dtype)
        std=torch.sqrt(vae.bn.running_var.view(1,-1,1,1)+vae.config.batch_norm_eps).to(latents.device,latents.dtype)
        tokens=((latents-mean)/std).float().flatten(2).transpose(1,2).contiguous()
        padding=torch.zeros(batch,5,tokens.shape[2],dtype=tokens.dtype,device=tokens.device)
        return TensorDict({"feature1": feature1, "feature2":torch.cat([padding,tokens],dim=1)},
                          batch_size=[batch],device=self.device)

def main():
    args=argparse.ArgumentParser()
    args.add_argument("--image",required=True)
    args.add_argument("--out",required=True)
    args.add_argument("--weights",required=True)
    args.add_argument("--dinov3",required=True)
    args.add_argument("--flux2vae",required=True)
    args.add_argument("--device",default="cuda:0")
    args=args.parse_args()
    if not torch.cuda.is_available(): raise RuntimeError("MI50 ROCm cuda:0 nicht verfügbar; CPU-Fallback verboten")
    if "gfx906" not in (torch.cuda.get_device_properties(0).gcnArchName or ""):
        raise RuntimeError("GPU ist nicht MI50/gfx906; keine automatische Umschaltung")
    weights=Path(args.weights)
    config=json.loads((weights/"config.json").read_text())
    config["image_encoders"]={"dinov3":args.dinov3,"flux2_vae":args.flux2vae}
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    # The model reads this config and four safetensors. Symlinks stay inside
    # the private job folder, original weights are never rewritten/deleted.
    overlay=out/"local-weights"
    overlay.mkdir(exist_ok=True)
    (overlay/"config.json").write_text(json.dumps(config))
    for name in ("denoiser","decoder","decoder_gs","birefnet"):
        path=overlay/(name+".safetensors")
        if not path.exists(): path.symlink_to((weights/(name+".safetensors")).resolve())
    pipeline_module.ImageEncoder=MI50Encoder
    from idol.models import layers
    layers.ATTN_BACKEND="sdpa"
    pipeline=pipeline_module.IdolPipeline.from_pretrained(str(overlay),device=args.device,
                                                           dinov3_dtype="fp16",optimize=False)
    output=pipeline(args.image,mode="real",num_gaussians=131072,verbose=True)
    output.gaussian.save_ply(str(out/"input.ply"))
    output.preprocessed_image.save(out/"input_preprocessed.png")
    (out/"input_camera.json").write_text(json.dumps(output.camera,indent=2))
    print("Idol MI50 FP16 completed")

if __name__=="__main__":main()
