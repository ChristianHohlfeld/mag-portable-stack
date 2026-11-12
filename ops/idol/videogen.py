#!/usr/bin/env python3
"""A video from a picture: Wan 2.2 I2V-A14B (Wan-AI, Apache-2.0) on the MI50.

    videogen.py JOB_FOLDER

Reads JOB_FOLDER/input.png and JOB_FOLDER/prompt.json ({"prompt", "seed"})
and writes JOB_FOLDER/video.mp4: about five seconds (81 frames at 16 fps),
about 480p in the picture's own shape. Runs as a stage of the Idol worker's
one queue (the MI50 does one thing at a time), in its own environment
(~/idol-mi50/video/venv, which uses Idol's ROCm torch and diffusers without
changing them).

The two experts of A14B (high noise, then low noise) are GGUF Q6_K
(QuantStack), each with the Lightning 4-step LoRA (lightx2v): four Euler steps
(shift 5, as it was distilled; two per expert),
no classifier-free guidance; every finished step is kept (state.pt), so an
interrupted stage goes on after its last step. The card has no bf16 and no fast attention:
the experts compute in fp16, attention is exact but built for a block of
queries at a time, and the text encoder (UMT5) reads the words in fp32
first and leaves the card again. Prints "MAG-VIDEO k/n" as it gets on and
"MAG-VIDEO-STAGE <words>" between its parts; MAG-ERROR in words.
"""
import gc
import json
import os
import subprocess
import sys
from pathlib import Path

import torch
import torch.nn.functional as F
# The Idol environment of this host's owner (ops/host.env; ~/idol-mi50).
IDOL_BASE = os.environ.get("MAG_IDOL_BASE", os.path.expanduser("~/idol-mi50"))

WEIGHTS = Path(os.environ.get("MAG_VIDEO_WEIGHTS", os.path.join(IDOL_BASE, "weights/wan22-i2v")))
STEPS = 4                                   # what the Lightning LoRA is made for
SIGMAS = [1.0, 0.75, 0.5, 0.25]             # its steps (1000, 750, 500, 250), shifted by 5, Euler
SHIFT = 5.0
FRAMES, FPS = 81, 16
AREA = 480 * 832                            # 480p, in the picture's own shape
CHUNK = int(float(os.environ.get("MAG_VIDEO_ATTN_MB", "1024")) * 2**20)
_sdpa = F.scaled_dot_product_attention


def attention(query, key, value, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None, enable_gqa=False):
    """Exact attention without the full score matrix: gfx906 has no flash or
    memory-efficient kernel, and 33 000 tokens would need 80 GB of scores.
    Takes torch's own argument names: diffusers calls it with query=, key=, value=."""
    q, k, v = query, key, value
    if attn_mask is not None or dropout_p or is_causal or enable_gqa or not q.is_cuda or q.dim() != 4:
        return _sdpa(q, k, v, attn_mask=attn_mask, dropout_p=dropout_p, is_causal=is_causal, scale=scale)
    n, h, l, c = q.shape
    step = max(16, CHUNK // max(1, n * h * k.shape[2] * 6))
    kt = k.transpose(-1, -2)
    q = q * (scale if scale is not None else c ** -0.5)
    out = torch.empty(n, h, l, v.shape[-1], dtype=q.dtype, device=q.device)
    for i in range(0, l, step):
        scores = torch.matmul(q[:, :, i:i + step], kt).float()
        out[:, :, i:i + step] = torch.matmul(scores.softmax(-1).to(v.dtype), v)
        del scores
    return out


F.scaled_dot_product_attention = attention


def say(stage):
    print("MAG-VIDEO-STAGE " + stage, flush=True)


def size_of(picture):
    """Width and height near 480p in the picture's own shape, multiples of 16."""
    ratio = picture.height / picture.width
    height = (AREA * ratio) ** 0.5
    width = height / ratio
    return max(256, int(round(width / 16)) * 16), max(256, int(round(height / 16)) * 16)


def main():
    job = Path(sys.argv[1])
    asked = json.loads((job / "prompt.json").read_text())
    prompt = str(asked.get("prompt", "")).strip()[:1500] or "natural, lively motion; the camera stays steady"
    if not (WEIGHTS / ".complete").is_file():
        print("MAG-ERROR Das Videomodell ist noch nicht ganz heruntergeladen.", flush=True)
        sys.exit(2)
    from PIL import Image
    picture = Image.open(job / "input.png")
    picture.load()
    if picture.mode in ("RGBA", "LA") or "transparency" in picture.info:
        white = Image.new("RGB", picture.size, (255, 255, 255))
        white.paste(picture.convert("RGBA"), mask=picture.convert("RGBA").getchannel(3))
        picture = white
    picture = picture.convert("RGB")
    width, height = size_of(picture)
    picture = picture.resize((width, height), Image.LANCZOS)
    torch.set_num_threads(16)

    from diffusers import (AutoencoderKLWan, FlowMatchEulerDiscreteScheduler, GGUFQuantizationConfig,
                           WanImageToVideoPipeline, WanTransformer3DModel)
    from transformers import UMT5EncoderModel

    # Every step leaves its result in the job folder (state.pt: the words as
    # read, the picture's latent, the latents so far and the scheduler with
    # its history), so a stage that was interrupted (a reboot) goes on after
    # the last finished step instead of from the start.
    saved = job / "state.pt"
    state = None
    if saved.is_file():
        try:
            state = torch.load(saved, map_location="cuda", weights_only=False)
            if (state.get("width"), state.get("height")) != (width, height):
                state = None
        except Exception as exc:                               # a half-written one: from the start
            print("MAG-NOTE state not read (%s)" % type(exc).__name__, flush=True)
            state = None

    def keep(**fields):
        part = job / "state.part.pt"
        torch.save(fields, part)
        part.rename(saved)

    pipe = WanImageToVideoPipeline.from_pretrained(str(WEIGHTS), transformer=None, transformer_2=None, text_encoder=None,
                                                   vae=None, torch_dtype=torch.float16)
    pipe.vae = AutoencoderKLWan.from_pretrained(str(WEIGHTS), subfolder="vae", torch_dtype=torch.float32).to("cuda")
    pipe.vae.enable_tiling()                                    # the picture's latent as well as the frames

    if state is None:
        # 1. The words, read by UMT5 in fp32 (its states outgrow fp16), then it leaves the card.
        say("Beschreibung lesen")
        encoder = UMT5EncoderModel.from_pretrained(str(WEIGHTS), subfolder="text_encoder", dtype=torch.bfloat16)
        pipe.text_encoder = encoder.to("cuda", torch.float32).eval()
        with torch.no_grad():
            embeds, _ = pipe.encode_prompt(prompt, do_classifier_free_guidance=False, device=torch.device("cuda"), dtype=torch.float32)
        pipe.text_encoder = None
        del encoder
        torch.cuda.empty_cache()
        limit = torch.finfo(torch.float16).max
        embeds = embeds.clamp(-limit, limit).half()
        # The picture as the experts see it, and the noise they start from.
        generator = torch.Generator(device="cuda").manual_seed(int(asked.get("seed", 0)) or 1)
        # The Lightning LoRA was distilled on Euler steps, not on the base's UniPC.
        pipe.scheduler = FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=SHIFT)
        pipe.scheduler.set_timesteps(sigmas=SIGMAS, device="cuda")
        with torch.no_grad():
            image = pipe.video_processor.preprocess(picture, height=height, width=width).to("cuda", dtype=torch.float32)
            latents, condition = pipe.prepare_latents(image, 1, pipe.vae.config.z_dim, height, width, FRAMES,
                                                      torch.float32, torch.device("cuda"), generator, None, None)[:2]
        state = dict(width=width, height=height, embeds=embeds, condition=condition, latents=latents,
                     scheduler=pipe.scheduler, done=0)
        keep(**state)
    else:
        print("MAG-NOTE going on after step %d of %d" % (state["done"], STEPS), flush=True)
    embeds, condition, latents, scheduler = state["embeds"], state["condition"], state["latents"], state["scheduler"]
    timesteps = scheduler.timesteps
    boundary = pipe.config.boundary_ratio * scheduler.config.num_train_timesteps

    # 2. The experts one after the other, each with its Lightning LoRA, in
    # fp16 on the card: the high-noise one for the first steps, then it
    # leaves and the low-noise one comes (both at once would not fit in 32 GB).
    quant = GGUFQuantizationConfig(compute_dtype=torch.float16)
    files = {"high": ("high.gguf", "transformer", "high_noise_model"), "low": ("low.gguf", "transformer_2", "low_noise_model")}

    # Progress from the blocks themselves: every expert call runs all of them.
    blocks = int(json.loads((WEIGHTS / "transformer" / "config.json").read_text()).get("num_layers", 40))
    total, ran_blocks = STEPS * blocks, [state["done"] * blocks]

    def ran(*_):
        ran_blocks[0] += 1
        if ran_blocks[0] % 4 == 0 or ran_blocks[0] == total:
            print("MAG-VIDEO %d/%d" % (min(ran_blocks[0], total), total), flush=True)

    def load(key):
        say("Videomodell laden")
        name, folder, lora = files[key]
        expert = WanTransformer3DModel.from_single_file(str(WEIGHTS / "gguf" / name), quantization_config=quant,
                                                        config=str(WEIGHTS), subfolder=folder, torch_dtype=torch.float16).to("cuda")
        pipe.transformer = expert                                # the LoRA is loaded into "the" transformer
        pipe.load_lora_weights(str(WEIGHTS / "lora" / (lora + ".safetensors")), adapter_name="lightning_" + key)
        pipe.transformer = None
        for name_, param in expert.named_parameters():           # the card has no bf16
            if "lora_" in name_ and param.dtype != torch.float16:
                param.data = param.data.to(torch.float16)
        for block in expert.blocks:
            block.register_forward_hook(ran)
        say("Video rechnen")
        print("MAG-VIDEO %d/%d" % (ran_blocks[0], total), flush=True)
        return expert

    # 3. Four steps, without guidance; each one kept when it is done.
    expert, current = None, None
    with torch.no_grad():
        for i in range(state["done"], len(timesteps)):
            t = timesteps[i]
            key = "high" if float(t) >= boundary else "low"
            if key != current:
                expert = None
                gc.collect()
                torch.cuda.empty_cache()
                expert, current = load(key), key
            model_input = torch.cat([latents, condition], dim=1).to(torch.float16)
            with expert.cache_context("cond"):
                noise = expert(hidden_states=model_input, timestep=t.expand(latents.shape[0]), encoder_hidden_states=embeds,
                               encoder_hidden_states_image=None, return_dict=False)[0]
            latents = scheduler.step(noise.float(), t, latents, return_dict=False)[0]
            del noise, model_input
            keep(width=width, height=height, embeds=embeds, condition=condition, latents=latents, scheduler=scheduler, done=i + 1)
    expert = None
    gc.collect()
    torch.cuda.empty_cache()

    # 4. The frames, decoded in tiles.
    say("Bilder entpacken")
    vae = pipe.vae
    with torch.no_grad():
        latents = latents.to("cuda", torch.float32)
        mean = torch.tensor(vae.config.latents_mean).view(1, vae.config.z_dim, 1, 1, 1).to(latents)
        std = 1.0 / torch.tensor(vae.config.latents_std).view(1, vae.config.z_dim, 1, 1, 1).to(latents)
        video = vae.decode(latents / std + mean, return_dict=False)[0]
    frames = ((video[0].clamp(-1, 1) + 1) * 127.5).round().to(torch.uint8).permute(1, 2, 3, 0).cpu().numpy()  # t h w c

    # 5. H.264, playable everywhere.
    say("Video schreiben")
    part = job / "video.part.mp4"
    encode = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                               "-s", "%dx%d" % (frames.shape[2], frames.shape[1]), "-r", str(FPS), "-i", "-",
                               "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                               "-movflags", "+faststart", "-threads", "8", str(part)], stdin=subprocess.PIPE)
    encode.stdin.write(frames.tobytes())
    encode.stdin.close()
    if encode.wait() != 0 or not part.is_file():
        print("MAG-ERROR Das Video ließ sich nicht schreiben.", flush=True)
        sys.exit(3)
    part.rename(job / "video.mp4")
    print("MAG-DONE %dx%d, %d frames" % (width, height, len(frames)), flush=True)


if __name__ == "__main__":
    main()
