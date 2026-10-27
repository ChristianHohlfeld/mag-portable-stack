# Read-aloud (Piper in the browser)

Every message can be read aloud from the message popover or the speaker under
the newest message. Nothing autoplays. Synthesis runs in the browser; the
server does not run a TTS engine and is not contacted again for a playback
once the voice is cached.

* **Bot voice messages**: the `speak` tool (and `POST /api/speak`) posts a room
  message `{"kind":"voice","content":"<text>"}` with no audio file. The room
  renders it as a voice bubble with a play button that uses this same Piper
  pipeline.
* **Engine**: `mag-room/static/message-audio.js` → Web Worker
  `tts-worker.js` (espeak-ng phonemization from `@diffusionstudio/piper-wasm`
  1.0.0 + Piper VITS in `onnxruntime-web` 1.30.0). WASM, up to four threads
  when the page is cross-origin isolated, otherwise one thread. WebGPU is not
  used: this VITS graph is unreliable there, and Kokoro has no German voice.
  Supertonic HD speaks German but is WebGPU-only and about 380 MB.
* **Start latency**: German text is split on sentence boundaries (max 120
  characters). The first sentence is synthesized and played while the next
  one is already running. The chosen voice (Thorsten medium by default) is
  warmed in the background shortly after page load and kept in Cache Storage
  (`mag-tts-v1`), so it downloads once. A tap before it is ready shows a
  spinner on the play button and then speaks with Piper.
* **Fallback**: only if Piper fails, for non-German text, or without
  Worker/WebAudio does a system voice speak (Natural/Neural/Premium/Siri/
  Enhanced first; espeak/mbrola/festival/pico voices are never chosen).
* **Microphone**: the mic button is already on-device Moonshine
  (`speech-ui.js`, `ops/vendor-speech-assets.sh`). It does not call
  `POST /api/stt`. That route is whisper.cpp plus an optional correction
  turn for uploaded audio; read-aloud and the mic do not use it.
* **Voices** (settings → "Vorlesen: …", stored in `localStorage`):

  | Voice | File | Size | License |
  |---|---|---|---|
  | Thorsten (male, medium) | `de_DE-thorsten-medium.onnx` | 60 MB | CC0 (Thorsten-Voice dataset) |
  | Kerstin (female, low) | `de_DE-kerstin-low.onnx` | 63 MB | CC0 (dataset-voice-kerstin) |

  Thorsten high (114 MB) was the previous default. On WASM it is several
  times slower than medium and was the cold-download that loaded the host.
  Medium is the usual German Piper quality/speed point (same 22.05 kHz
  family, CC0). Source: `rhasspy/piper-voices` (Piper models: MIT). The
  vendoring script re-checks `License: CC0` in each MODEL_CARD and pins
  every file by SHA-256. Runtime licenses: onnxruntime-web MIT, piper-wasm
  MIT, espeak-ng data GPL-3.0 (data only, unmodified).
* **Hosting**: `ops/vendor-tts-assets.sh build/tts-assets` (run by
  `ops/deploy.sh`; optional, a failed download falls back to system voices)
  stages `/static/tts-<hash>/` and `/static/tts-config.json`. nginx serves
  them via `ops/nginx/mag-speech.inc` (`Cache-Control: immutable`, `gzip off`,
  `sendfile`, same-origin). If a request still reaches magroom, hashed
  speech/TTS files are sent with `sendfile` and are not read into the room
  process. No CDN at runtime. The service worker cache-firsts the runtime
  (js/wasm) and leaves the `.onnx` weights to the worker Cache Storage so
  they are not stored twice.
* **Tests**: `node mag-room/tests/test_piper_warmup.cjs` (warm-up, sentence
  chunking, loading state, fallback), `python3 mag-room/tests/test_tts.py`
  (fallback order); with `MAG_TTS_ASSETS=build/tts-assets` it also synthesizes
  German with both voices, checks the cache, and prints hot start latency.
