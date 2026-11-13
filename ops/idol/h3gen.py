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
patch) natively on ggml's HIP backend for gfx906 (ROCm 6.3.4): its int8 dp4a
matrix kernels, and our own attention for gfx906 (--sage-attn: int8 Q*K^T by
v_dot4, P*V by v_dot2, ops/video/h3/gfx906-attention.patch); parts that wait are kept in memory
(--offload-to-cpu), the VAE decodes in tiles. The licence was granted to Chris personally (private, nothing passed
on, made by AI said so): the file says in its metadata that it is made by
AI with MiniMax H3. Prints "MAG-VIDEO k/4" per step and "MAG-VIDEO-STAGE
<words>" between parts; MAG-ERROR in words.
"""
import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(os.environ.get("MAG_IDOL_BASE", os.path.expanduser("~/idol-mi50")))
WEIGHTS = Path(os.environ.get("MAG_H3_WEIGHTS", str(BASE / "weights/hyperflow-h3")))
CLI = Path(os.environ.get("MAG_H3_CLI", str(BASE / "h3/sd-cli-hip")))          # HIP for gfx906
ROCM = BASE / "rocm-6.3.4/current"                                            # unpacked by ops/video/h3/rocm.sh
STEPS, FPS = 4, 24
SIGMAS = "1.0,0.962575793,0.755781889,0.312497854,0.0"   # Video Rebirth's pre-shifted 4-step schedule
FRAMES = {5: 124, 10: 243}                  # 17n + 5 frames: 5.2 s and 10.1 s
AREA = 864 * 480                            # 480p: minutes on the MI50 (the workflow's megapixel takes about an hour)
LABEL = "KI-generiert mit MiniMax H3 (HyperFlow H3 Flash)"
PROGRESS = re.compile(r"\|\s*(\d+)/(\d+)\s*-")
# h3d (mag-h3d.service): stable-diffusion.cpp's server, the same build, which
# keeps the model loaded between clips; the MI50's other stages unload it
# before they use the card. Where it does not answer, sd-cli makes the clip.
H3D = os.environ.get("MAG_H3D_URL", "http://127.0.0.1:8796")
MODELS = {"diffusion_model": "diffusion_models/{}.gguf", "llm": "text_encoders/text_encoder.gguf",
          "vae": "vae/video_vae.safetensors", "audio_vae": "vae/audio_vae.safetensors"}
# madebyollin's tiny autoencoder for H3 (taeh3, MIT, ops/video/h3/fetch.sh): the
# finished video is decoded by it in seconds instead of the VAE's minutes (the
# VAE still encodes a first frame). The sound keeps its own VAE.
TAE = "vae/taeh3.safetensors"


def h3d(method, path, body=None, timeout=10):
    """One request to h3d: (status, JSON or None); (0, None) when it does not answer."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(H3D + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"null")
        except ValueError:
            return err.code, None
    except (OSError, ValueError):
        return 0, None


def by_h3d(job, prompt, width, height, frames, seed, picture, use):
    """The clip through h3d into job/h3.part.webm. False when h3d is not there
    (or turns the clip down, or stays away a quarter of an hour), so that
    sd-cli makes it, from the last finished step on.

    Resumable like every job of the MI50's line: h3d writes the sampler's
    state after every step into job/h3state (it is told where by the file
    jobs/.h3d-state-dir: one clip at a time, under the MI50's lock). A restart
    of the worker leaves this stage running and h3d with it; a restart of h3d
    or a reboot loses h3d's job, so it is given again and goes on after the
    last finished step (and only decodes when all steps were done)."""
    if h3d("GET", "/sdcpp/v1/models/status", timeout=3)[0] != 200:
        return False
    kind = "ref2va" if use == "ref" else "fl2va"
    mark = job / "h3d.json"                  # the h3d job of this stage: followed again after a restart
    control = job.parent / ".h3d-state-dir"
    state_dir = job / "h3state"
    state_dir.mkdir(exist_ok=True)

    def submit():
        say("Modell laden")
        control.write_text(str(state_dir) + "\n")
        recipe = "minimax-h3-" + kind
        loaded = h3d("GET", "/sdcpp/v1/models/status", timeout=10)[1] or {}
        # Loaded already: nothing to load (a load while h3d still holds a job is
        # refused with 409, even for the same model). Another model: load it,
        # waiting while h3d finishes what it holds.
        models = {slot: name.format(kind) for slot, name in MODELS.items()}
        if (WEIGHTS / "taeh3.safetensors").is_file():
            models["tae"] = TAE
        if not (loaded.get("state") == "ready" and loaded.get("recipe") == recipe and loaded.get("models") == models):
            h3d("POST", "/sdcpp/v1/models/refresh", timeout=60)   # files that came after its start
            for _ in range(180):
                status, state = h3d("POST", "/sdcpp/v1/models/load", {"recipe": recipe, "models": models}, timeout=1800)
                if status != 409:
                    break
                time.sleep(10)
            if status != 200:
                print("h3d load: %s %s" % (status, (state or {}).get("error", "")), flush=True)
                return None
        image = base64.b64encode(picture.read_bytes()).decode() if picture else None
        body = {"prompt": prompt, "negative_prompt": "", "width": width, "height": height, "seed": seed,
                "video_frames": frames, "fps": FPS, "init_image": image if use == "first" else None,
                "end_image": None, "ref_images": [image] if use == "ref" and image else [], "control_frames": [],
                "ref_image_args": "", "lora": [],
                "sample_params": {"sample_method": "euler", "sample_steps": STEPS,
                                  "custom_sigmas": [float(x) for x in SIGMAS.split(",")],
                                  "guidance": {"txt_cfg": 1.0}},
                "vae_tiling_params": {"enabled": True}, "preview": "proj", "preview_interval": 1,
                "output_format": "webm"}
        status, state = h3d("POST", "/sdcpp/v1/vid_gen", body, timeout=60)
        if status != 202 or not (state or {}).get("id"):
            print("h3d vid_gen: %s %s" % (status, (state or {}).get("error", "")), flush=True)
            return None
        mark.write_text(json.dumps({"id": state["id"]}))
        return state["id"]

    def let_go():
        mark.unlink(missing_ok=True)
        try:
            if control.read_text().strip() == str(state_dir):
                control.write_text("")
        except OSError:
            pass

    ident = None

    def cancel(*_):
        if ident:
            h3d("POST", "/sdcpp/v1/jobs/%s/cancel" % ident)
        let_go()
        sys.exit(5)
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    told, away = None, None
    while True:
        ident = json.loads(mark.read_text()).get("id") if mark.is_file() else None
        status, state = h3d("GET", "/sdcpp/v1/jobs/" + ident) if ident else (404, None)
        if status == 0:                      # h3d is away (restarting): wait for it
            away = away or time.monotonic()
            if time.monotonic() - away > 900:
                print("h3d away for a quarter of an hour; sd-cli goes on from the last step", flush=True)
                let_go()
                return False
            time.sleep(5)
            continue
        away = None
        if status != 200 or not state:       # h3d does not know it (restarted): given again, resumed
            if ident:
                print("h3d lost the clip; given again, from the last finished step", flush=True)
            told = None
            if submit() is None:
                let_go()
                return False
            continue
        now = state.get("status")
        if now == "completed":
            data = base64.b64decode(((state.get("result") or {}).get("b64_json") or ""))
            if not data:
                print("MAG-ERROR H3 hat kein Video geliefert.", flush=True)
                sys.exit(3)
            (job / "h3.part.webm").write_bytes(data)
            let_go()
            return True
        if now in ("failed", "cancelled"):
            let_go()
            error = state.get("error") or {}
            print("h3d: %s" % (error.get("message") if isinstance(error, dict) else error), flush=True)
            print("MAG-ERROR H3 hat kein Video erzeugt.", flush=True)
            sys.exit(3)
        preview = state.get("preview") or {}
        step = (preview.get("step"), preview.get("total_steps"))
        if now == "generating" and told is None:
            say("Video und Ton rechnen")
            told = (0, 0)
        if now == "generating" and step[0] and step != told:
            told = step
            print("MAG-VIDEO %d/%d" % (min(step[0], STEPS), STEPS), flush=True)
            if step[0] >= (step[1] or STEPS):
                say("Bilder und Ton dekodieren")
        time.sleep(2)


QUOTES = re.compile(r'„([^“”"]+)[“”"]|“([^”"]+)[”"]|»([^«]+)«|"([^"]+)"')
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(ich|du|wir|ihr|nicht|und|der|die|das|ist|bin|bist|sind|mein|dein|hallo|ja|nein|was|wie|wo|warum|komm|geh)\b", re.I)


def dialogue(prompt):
    """H3's own form for speech: what is said, word for word and never
    translated, inside <d>[Language] ... </d>, said by (S1), (S2) ... A
    prompt that has <d> already is left as it is; otherwise every quoted
    passage („...", "...", »...«) becomes one line of speech, and when no
    speaker is marked it is the one speaker (S1). Nothing is made up."""
    if "<d>" in prompt:
        return prompt

    def line(found):
        text = next(g for g in found.groups() if g is not None).strip()
        return "<d>[%s] %s</d>" % ("German" if GERMAN.search(text) else "English", text)
    spoken = QUOTES.sub(line, prompt)
    if spoken != prompt and not re.search(r"\(S\d+\)", spoken):
        spoken = spoken.replace("<d>", "(S1) <d>")
    return spoken


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


def run(job, command, env):
    """One run of sd-cli, its progress told as MAG-VIDEO lines. Returns
    (exit code, whether sampling had begun)."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=job)
    sampling = False
    began = False

    def heard(part):
        nonlocal sampling, began
        text = part.decode("utf-8", "replace")
        found = PROGRESS.search(text)
        if found:
            if sampling and int(found.group(2)) == STEPS:
                print("MAG-VIDEO %s/%d" % (found.group(1), STEPS), flush=True)
        elif "sampling using" in text:
            sampling = began = True
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
    return proc.wait(), began


def main():
    job = Path(sys.argv[1])
    asked = json.loads((job / "prompt.json").read_text())
    prompt = str(asked.get("prompt", "")).strip()[:1500]
    seconds = 10 if int(asked.get("seconds", 5) or 5) >= 10 else 5
    previous = job / "prev.mp4" if asked.get("continues") and (job / "prev.mp4").is_file() else None
    if previous and not (job / "input.png").is_file():
        # Going on: the new part begins with the very last frame of the video so far.
        say("Letztes Bild lesen")
        part = job / "input.part.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-1", "-i", str(previous), "-vsync", "0",
                        "-update", "1", "-frames:v", "100", str(part)])
        if not part.is_file():
            print("MAG-ERROR Das letzte Bild des Videos ließ sich nicht lesen.", flush=True)
            sys.exit(2)
        part.rename(job / "input.png")
    picture = job / "input.png" if (job / "input.png").is_file() else None
    use = "ref" if picture and asked.get("use") == "ref" else "first"
    if not prompt:
        print("MAG-ERROR Es fehlt, was im Video geschehen soll.", flush=True)
        sys.exit(2)
    if not CLI.is_file() or not ROCM.is_dir() or not (WEIGHTS / ".complete").is_file():
        print("MAG-ERROR H3 ist noch nicht ganz eingerichtet (Gewichte oder Programm fehlen).", flush=True)
        sys.exit(2)
    prompt = dialogue(prompt)               # speech in H3's form: <d>[German] word for word</d>
    if previous:
        # The same shot goes on: the model is told so, so that people, look and sound stay.
        prompt = ("Continuation of the same shot, the same characters, look, light and sound, no cut: " + prompt)[:1500]
    if use == "ref" and "<Picture 1>" not in prompt:
        prompt = "<Picture 1> " + prompt
    sound = job / "h3.webm"                 # what the model made; done is never done twice
    if not sound.is_file() and by_h3d(job, prompt, *shape(picture), FRAMES[seconds],
                                       int(asked.get("seed", 0)) or 1, picture, use):
        (job / "h3.part.webm").rename(sound)
    if not sound.is_file():
        width, height = shape(picture)
        say("Modell laden")
        command = [str(CLI), "-M", "vid_gen",
                   "--diffusion-model", str(WEIGHTS / ("ref2va.gguf" if use == "ref" else "fl2va.gguf")),
                   "--llm", str(WEIGHTS / "text_encoder.gguf"),
                   "--vae", str(WEIGHTS / "video_vae.safetensors"),
                   "--audio-vae", str(WEIGHTS / "audio_vae.safetensors"),
                   *(["--tae", str(WEIGHTS / "taeh3.safetensors")] if (WEIGHTS / "taeh3.safetensors").is_file() else []),
                   "--sampling-method", "euler", "--cfg-scale", "1.0", "--steps", str(STEPS), "--sigmas", SIGMAS,
                   "-W", str(width), "-H", str(height), "--video-frames", str(FRAMES[seconds]), "--fps", str(FPS),
                   "-s", str(int(asked.get("seed", 0)) or 1), "-p", prompt,
                   "--offload-to-cpu", "--diffusion-fa", "--sage-attn", "--vae-tiling",
                   "-t", str(len(os.sched_getaffinity(0))), "-o", str(job / "h3.part.webm")]
        if picture:
            command += ["--ref-image" if use == "ref" else "--init-img", str(picture)]
        # One path: ggml's HIP backend for gfx906 (int8 dp4a matrix products,
        # fused flash attention) against the unpacked ROCm 6.3.4.
        (job / "h3state").mkdir(exist_ok=True)   # the sampler's state after every step: resumed from there
        env = dict(os.environ, HIP_VISIBLE_DEVICES="0", ROCR_VISIBLE_DEVICES="0", SD_STATE_DIR=str(job / "h3state"),
                   LD_LIBRARY_PATH=str(ROCM / "lib") + ":" + os.environ.get("LD_LIBRARY_PATH", ""))
        code, _ = run(job, command, env)
        if code != 0 or not (job / "h3.part.webm").is_file():
            print("MAG-ERROR H3 hat kein Video erzeugt (Exit %d)." % code, flush=True)
            sys.exit(3)
        (job / "h3.part.webm").rename(sound)
    say("Video schreiben")
    part = job / "video.part.mp4"
    if previous:
        # The whole video: what there was, then the new part without its first
        # frame (the same as the last one before), its sound shifted by that
        # frame and welded on with 40 ms of crossfade; the new part in the size
        # of the old one.
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", str(previous)], capture_output=True, text=True).stdout.strip().split(",")
        width, height = (int(size[0]), int(size[1])) if len(size) == 2 and all(x.isdigit() for x in size) else shape(picture)
        graph = ("[1:v]trim=start_frame=1,setpts=PTS-STARTPTS,scale=%d:%d,fps=%d,format=yuv420p[nv];"
                 "[0:v]fps=%d,format=yuv420p[ov];[ov][nv]concat=n=2:v=1:a=0[v];"
                 "[1:a]atrim=start=%.6f,asetpts=PTS-STARTPTS,aresample=32000[na];[0:a]aresample=32000[oa];"
                 "[oa][na]acrossfade=d=0.04:c1=tri:c2=tri[a]") % (width, height, FPS, FPS, 1.0 / FPS)
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(previous), "-i", str(sound), "-filter_complex", graph,
                               "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                               "-metadata", "comment=" + LABEL, "-metadata", "description=" + LABEL, str(part)])
    else:
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
