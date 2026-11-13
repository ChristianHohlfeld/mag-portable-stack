#!/usr/bin/env python3
"""A video with its sound: HyperFlow H3 Flash (Video Rebirth, a few-step
MiniMax H3) on the MI50.

    h3gen.py JOB_FOLDER

Reads JOB_FOLDER/prompt.json ({"prompt", "seed", "seconds", "use"}) and, if
there is one, JOB_FOLDER/input.png, and writes JOB_FOLDER/video.mp4: 5 or 10
seconds at 24 fps, 480p (864x480 without a picture, else the same area in the
picture's own shape), with stereo sound. A picture is the first frame
("use": "first", fl2va) or a reference for who or what is in the video
("use": "ref", ref2va, "<Picture 1>" in the words). Runs as a stage of the
Idol worker's one queue (the MI50 does one thing at a time).

The setup of Video Rebirth's own workflow: the 14B Flash transformer, its
HyperFlow sigmas, Euler, CFG 1, the MiniMax H3 Qwen3-VL-32B text encoder and
H3's video and audio VAEs. Made for minutes on the MI50, not an hour: four
steps (Video Rebirth's own 4-step schedule) instead of six, and 480p instead
of one megapixel (a third of the tokens, a ninth of the attention). On gfx906
there is no INT8 convrot, NVFP4 or SageAttention (all NVIDIA), so it is the
same model as GGUF Q8_0, run by stable-diffusion.cpp (with Video Rebirth's
patch) on ggml's Vulkan backend: parts that wait are kept in memory
(--offload-to-cpu), attention is ggml's flash shader, the VAE decodes in
tiles. The licence was granted to Chris personally (private, nothing passed
on, made by AI said so): the file says in its metadata that it is made by
AI with MiniMax H3. Prints "MAG-VIDEO k/4" per step and "MAG-VIDEO-STAGE
<words>" between parts; MAG-ERROR in words.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(os.environ.get("MAG_IDOL_BASE", os.path.expanduser("~/idol-mi50")))
WEIGHTS = Path(os.environ.get("MAG_H3_WEIGHTS", str(BASE / "weights/hyperflow-h3")))
CLI = Path(os.environ.get("MAG_H3_CLI", str(BASE / "h3/sd-cli")))
STEPS, FPS = 4, 24
SIGMAS = "1.0,0.962575793,0.755781889,0.312497854,0.0"   # Video Rebirth's pre-shifted 4-step schedule
FRAMES = {5: 124, 10: 243}                  # 17n + 5 frames: 5.2 s and 10.1 s
AREA = 864 * 480                            # 480p: minutes on the MI50 (the workflow's megapixel takes about an hour)
LABEL = "KI-generiert mit MiniMax H3 (HyperFlow H3 Flash)"
PROGRESS = re.compile(r"\|\s*(\d+)/(\d+)\s*-")


def say(stage):
    print("MAG-VIDEO-STAGE " + stage, flush=True)


def picture_size(path):
    """Width and height of the picture (PNG, JPEG or WebP), None if unreadable."""
    try:
        from PIL import Image
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def shape(picture):
    """480p's area, in steps of 32: 864x480 without a picture."""
    ratio = 16 / 9
    size = picture_size(picture) if picture else None
    if size and size[0] > 0 and size[1] > 0:
        ratio = max(9 / 21, min(21 / 9, size[0] / size[1]))
    width = max(320, min(1280, round((AREA * ratio) ** 0.5 / 32) * 32))
    height = max(320, min(1280, round((AREA / ratio) ** 0.5 / 32) * 32))
    return width, height


def main():
    job = Path(sys.argv[1])
    asked = json.loads((job / "prompt.json").read_text())
    prompt = str(asked.get("prompt", "")).strip()[:1500]
    seconds = 10 if int(asked.get("seconds", 5) or 5) >= 10 else 5
    picture = job / "input.png" if (job / "input.png").is_file() else None
    use = "ref" if picture and asked.get("use") == "ref" else "first"
    if not prompt:
        print("MAG-ERROR Es fehlt, was im Video geschehen soll.", flush=True)
        sys.exit(2)
    if not CLI.is_file() or not (WEIGHTS / ".complete").is_file():
        print("MAG-ERROR H3 ist noch nicht ganz eingerichtet (Gewichte oder Programm fehlen).", flush=True)
        sys.exit(2)
    if use == "ref" and "<Picture 1>" not in prompt:
        prompt = "<Picture 1> " + prompt
    sound = job / "h3.webm"                 # what the model made; done is never done twice
    if not sound.is_file():
        width, height = shape(picture)
        say("Modell laden")
        command = [str(CLI), "-M", "vid_gen",
                   "--diffusion-model", str(WEIGHTS / ("ref2va.gguf" if use == "ref" else "fl2va.gguf")),
                   "--llm", str(WEIGHTS / "text_encoder.gguf"),
                   "--vae", str(WEIGHTS / "video_vae.safetensors"),
                   "--audio-vae", str(WEIGHTS / "audio_vae.safetensors"),
                   "--sampling-method", "euler", "--cfg-scale", "1.0", "--steps", str(STEPS), "--sigmas", SIGMAS,
                   "-W", str(width), "-H", str(height), "--video-frames", str(FRAMES[seconds]), "--fps", str(FPS),
                   "-s", str(int(asked.get("seed", 0)) or 1), "-p", prompt,
                   "--offload-to-cpu", "--diffusion-fa", "--vae-tiling",
                   "-t", str(len(os.sched_getaffinity(0))), "-o", str(job / "h3.part.webm")]
        if picture:
            command += ["--ref-image" if use == "ref" else "--init-img", str(picture)]
        env = dict(os.environ, GGML_VK_VISIBLE_DEVICES="0")
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=job)
        sampling = False

        def heard(part):
            nonlocal sampling
            text = part.decode("utf-8", "replace")
            found = PROGRESS.search(text)
            if found:
                if sampling and int(found.group(2)) == STEPS:
                    print("MAG-VIDEO %s/%d" % (found.group(1), STEPS), flush=True)
            elif "sampling using" in text:
                sampling = True
                say("Video und Ton rechnen")
            elif "sampling completed" in text:
                sampling = False
                say("Bilder und Ton dekodieren")
            elif text.strip():
                print(text[:300], flush=True)   # the job's private log

        rest = b""
        while chunk := proc.stdout.read1(65536):
            *lines, rest = re.split(rb"[\r\n]", rest + chunk)
            for part in lines:
                heard(part)
        heard(rest)
        if proc.wait() != 0 or not (job / "h3.part.webm").is_file():
            print("MAG-ERROR H3 hat kein Video erzeugt (Exit %d)." % proc.returncode, flush=True)
            sys.exit(3)
        (job / "h3.part.webm").rename(sound)
    say("Video schreiben")
    part = job / "video.part.mp4"
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(sound), "-c:v", "libx264", "-crf", "18",
                           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                           "-metadata", "comment=" + LABEL, "-metadata", "description=" + LABEL, str(part)])
    if done.returncode != 0 or not part.is_file():
        print("MAG-ERROR Das Video ließ sich nicht schreiben.", flush=True)
        sys.exit(4)
    part.rename(job / "video.mp4")
    print("MAG-DONE", flush=True)


if __name__ == "__main__":
    main()
