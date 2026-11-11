#!/usr/bin/env python3
"""A picture from words: FLUX.2 klein 4B (Black Forest Labs, Apache-2.0) on the MI50.

    imagegen.py JOB_FOLDER

Reads JOB_FOLDER/prompt.json ({"prompt", "width", "height", "seed"}) and
writes JOB_FOLDER/image.png. Runs in Idol's environment (its ROCm torch and
diffusers), as a stage of the Idol worker's one queue: the MI50 does one
thing at a time. The card has no bf16, so the transformer and the VAE run in
fp16; the text encoder (Qwen3) reads the words in fp32 first and leaves the
card again, because its hidden states outgrow fp16. Prints
"MAG-IMAGE k/n" per step for the progress in the chat; MAG-ERROR in words.
"""
import json
import os
import sys
from pathlib import Path

import torch

WEIGHTS = os.environ.get("MAG_FLUX_WEIGHTS", "/home/chris/idol-mi50/weights/flux2-klein-4b")
STEPS = 4                                   # the distilled klein models are made for four steps


def embed(prompt):
    """The words as the transformer reads them, read by Qwen3 in fp32."""
    from diffusers import Flux2KleinPipeline
    from transformers import AutoTokenizer, Qwen3ForCausalLM
    tokenizer = AutoTokenizer.from_pretrained(WEIGHTS, subfolder="tokenizer")
    # bf16 from the file into memory, fp32 only on the card, tensor by tensor.
    encoder = Qwen3ForCausalLM.from_pretrained(WEIGHTS, subfolder="text_encoder", dtype=torch.bfloat16)
    encoder = encoder.to("cuda", torch.float32).eval()
    with torch.no_grad():
        embeds = Flux2KleinPipeline._get_qwen3_prompt_embeds(encoder, tokenizer, prompt, dtype=torch.float32, device="cuda")
    del encoder
    torch.cuda.empty_cache()
    limit = torch.finfo(torch.float16).max
    return embeds.clamp(-limit, limit).half()


def main():
    job = Path(sys.argv[1])
    asked = json.loads((job / "prompt.json").read_text())
    prompt = str(asked.get("prompt", "")).strip()[:2000]
    if not prompt:
        print("MAG-ERROR Es fehlt die Beschreibung des Bildes.", flush=True)
        sys.exit(2)
    width = max(256, min(1536, int(asked.get("width", 1024)) // 16 * 16))
    height = max(256, min(1536, int(asked.get("height", 1024)) // 16 * 16))
    from diffusers import Flux2KleinPipeline
    print("MAG-IMAGE 0/%d" % STEPS, flush=True)
    embeds = embed(prompt)
    pipe = Flux2KleinPipeline.from_pretrained(WEIGHTS, text_encoder=None, tokenizer=None,
                                              dtype=torch.float16).to("cuda")

    def step(_pipe, i, _t, kwargs):
        print("MAG-IMAGE %d/%d" % (i + 1, STEPS), flush=True)
        return kwargs

    generator = torch.Generator(device="cuda").manual_seed(int(asked.get("seed", 0)) or 1)
    with torch.no_grad():
        image = pipe(prompt_embeds=embeds, width=width, height=height, num_inference_steps=STEPS, guidance_scale=1.0,
                     generator=generator, callback_on_step_end=step).images[0]
    tmp = job / "image.part.png"
    image.save(tmp)
    tmp.rename(job / "image.png")
    print("MAG-DONE %dx%d" % (width, height), flush=True)


if __name__ == "__main__":
    main()
