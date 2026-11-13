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
ENGLISH = re.compile(r"\b(the|you|your|i'm|i am|is|are|was|what|where|why|how|hello|hi|yes|no|please|thank|thanks|watch|look|come|go|it's|don't|let's|we|they|my|this|that)\b", re.I)
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(ich|du|wir|ihr|nicht|und|der|die|das|ist|bin|bist|sind|mein|dein|hallo|ja|nein|was|wie|wo|warum|komm|geh)\b", re.I)


def dialogue(prompt):
    """Speech in the form of MiniMax's prompt guide (VIDEO_PROMPT_WRITING_GUIDE_base_en):
    who speaks and the speaker ID outside, then ": <d>[Language] the words</d>",
    the words verbatim and never translated. A prompt that has <d> already is
    left as it is; otherwise every quoted passage („...", "...", »...«) becomes
    one line of speech, and when no speaker ID is given it is (S1). Nothing is
    made up."""
    if "<d>" in prompt:
        return prompt

    def line(found):
        text = next(g for g in found.groups() if g is not None).strip()
        english = ENGLISH.search(text) and not GERMAN.search(text)   # German unless it is plainly English
        return ": <d>[%s] %s</d>" % ("English" if english else "German", text)
    spoken = re.sub(r"\s*:?\s*(?=„|“|»|\")", "", prompt) if QUOTES.search(prompt) else prompt
    spoken = QUOTES.sub(line, spoken)
    if spoken != prompt and not re.search(r"\(S\d+(,S\d+)*\)", spoken):
        spoken = spoken.replace(": <d>", " (S1): <d>")
    return spoken


FIRST_FRAME = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."


def context(prompt, first_frame, continuing):
    """The prompt as H3 was trained to read it (MiniMax's guide, the "H3
    Context IR"): with a first frame the alignment line and a blank line, then
    integrated_multimodal_description: [Shot 1] ..., overall_soundscape: ...,
    non_diegetic_music: .... A prompt that is in this form already (a bot's,
    as video_generate asks) keeps its words; only a missing alignment line is
    put in front."""
    if "integrated_multimodal_description" not in prompt:
        body = dialogue(prompt).strip()
        if continuing:
            body = ("The shot shown in <Picture 1> continues seamlessly with no cut: the same characters, appearance, "
                    "place, light, camera and voices carry on as " + body)
        elif first_frame:
            body = "Starting exactly from <Picture 1>, preserving its characters, appearance and composition, " + body
        prompt = ("integrated_multimodal_description: [Shot 1] " + body + "\n\n"
                  "overall_soundscape: The ambient sound of the place and the sounds of the actions described, "
                  "in sync with what is seen.\n\n"
                  "non_diegetic_music: N/A")
    if first_frame and "is fully referenced" not in prompt and "aligns with the" not in prompt:
        prompt = FIRST_FRAME + "\n\n" + prompt
    return prompt


# The small model that writes a prompt typed in the page in MiniMax's form, in
# English (ops/video/h3/fetch-rewriter.sh: Qwen3-4B-Instruct-2507, Apache-2.0).
# It runs here, on the CPU cores of this stage (28-55), with the llama.cpp
# runtime of the model servers, for a moment and never on Lena's or Noah's
# servers. What it writes is checked: the three fields must be there and every
# spoken line must be the owner's own words; else the rule form of context().
REWRITER = BASE / "weights/h3-rewriter"
REWRITE_RULES = """You rewrite a user's video idea into the prompt format of the MiniMax H3 audio-video model. Output ONLY the final prompt, nothing else.

Format, exactly three fields separated by one blank line, all in English:
integrated_multimodal_description: [Shot 1] <style (e.g. Live-action, cinematic / 3D cartoon), shot size, camera motion with amplitude and speed, subjects with appearance and position, what they do along the timeline, synchronized sounds>
overall_soundscape: <1-4 English sentences of ambient sound, action sounds and non-verbal human sounds; no dialogue>
non_diegetic_music: <1-3 English sentences of background music, or N/A when the user asks for none or does not mention music>

Rules:
- Translate the user's directions into English and keep their intent; you may add concrete visual and sound details that fit it, but no new events.
- Use one shot ([Shot 1]) unless the user asks for cuts; a cut is written "[Shot 2] At 00:04.000, the camera cuts to ...".
- Speech: only words the user wants spoken (quoted text or clearly given lines). Never invent lines. Each speaker gets a stable ID (S1), (S2); describe the speaker and the voice first, then the ID, then the verb and a colon, then the line: the old fox with a deep, calm voice (S1) says: <d>[German] Komm her!</d>
- Inside <d> put only the language tag ([German], [English], ...) and the spoken words exactly as the user wrote them, never translated, never changed.
- Characters who do not speak get no ID."""


def rewrite(job, prompt, first_frame, continuing):
    """MiniMax's form written by the small model, once per job (h3prompt.txt);
    None when it is not there, fails or writes something that does not hold."""
    done = job / "h3prompt.txt"
    if done.is_file():
        return done.read_text()
    model = REWRITER / "qwen3-4b-instruct-q4_k_m.gguf"
    try:
        rt = json.loads(Path("/usr/local/lib/mag-models/models.json").read_text())["runtime"]
        server = Path(rt["dir"]) / rt["rev"] / "bin/llama-server"
    except (OSError, ValueError, KeyError):
        return None
    if not (REWRITER / ".complete").is_file() or not server.is_file():
        return None
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    say("Regie schreiben")
    task = ("The video starts exactly from a given first frame <Picture 1>; begin the description from it and keep its "
            "characters, appearance and composition.\n" if first_frame and not continuing else "")
    if continuing:
        task = ("The video continues seamlessly, with no cut, from a given frame <Picture 1>: the same characters, "
                "appearance, place, light, camera and voices carry on.\n")
    proc = subprocess.Popen([str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(port), "-c", "8192",
                             "-t", "24", "-np", "1", "--no-webui"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(240):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2) as r:
                    if r.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            if proc.poll() is not None:
                return None
            time.sleep(0.5)
        body = json.dumps({"messages": [{"role": "system", "content": REWRITE_RULES},
                                        {"role": "user", "content": task + "User's idea:\n" + prompt}],
                           "temperature": 0.3, "seed": 1, "max_tokens": 1500}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            text = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    except (OSError, ValueError, KeyError, IndexError, urllib.error.URLError):
        return None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    if not holds(prompt, text):
        print("MAG-NOTE Regie in der festen Form (der Übersetzer hielt sich nicht an die Form)", flush=True)
        return None
    done.write_text(text)
    return text


def holds(asked, text):
    """The three fields are there, and each spoken line is the owner's own
    words: inside <d> nothing that was not in what he typed."""
    if not all(f in text for f in ("integrated_multimodal_description:", "overall_soundscape:", "non_diegetic_music:")):
        return False
    plain = re.sub(r"\s+", " ", asked)
    for line in re.findall(r"<d>\s*(?:\[[^\]]*\])?\s*(.*?)</d>", text, re.S):
        if re.sub(r"\s+", " ", line).strip() not in plain:
            return False
    quoted = [next(g for g in m.groups() if g is not None).strip() for m in QUOTES.finditer(asked)]
    return all(q in text for q in quoted)


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
    prompt = str(asked.get("prompt", "")).strip()[:6000]
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
    if use == "ref":
        prompt = dialogue(prompt)
        if "<Picture 1>" not in prompt:
            prompt = "<Picture 1> " + prompt
    else:
        # MiniMax's form (a first frame: also a continued video's last frame)
        # written once: a job taken up again samples on with the very same words
        final = job / "h3prompt.final.txt"
        if final.is_file():
            prompt = final.read_text()
        else:
            written = None
            if "integrated_multimodal_description" not in prompt:
                written = rewrite(job, prompt, picture is not None, previous is not None)
            prompt = context(written or prompt, picture is not None, previous is not None)
            final.write_text(prompt)
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
