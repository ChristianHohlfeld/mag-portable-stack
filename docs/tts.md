# Read-aloud (Piper in the browser)

Every message can be read aloud from the message popover or the speaker under
the newest message. Nothing autoplays and no audio is produced on the server.

* **Engine**: `mag-room/static/message-audio.js` → Web Worker
  `tts-worker.js` (espeak-ng phonemization from `@diffusionstudio/piper-wasm`
  1.0.0 + Piper VITS voice in `onnxruntime-web` 1.30.0, wasm, one thread).
  The voice is downloaded once on first use and kept in Cache Storage
  (`mag-tts-v1`); synthesis of the next sentence overlaps playback.
* **Fallback**: until the voice is cached, for non-German text, or without
  Worker/WebAudio, the best system voice speaks (names containing
  Natural/Premium/Siri/Enhanced/Neural first, then local voices).
* **Voices** (settings → "Vorlesen: …", stored in `localStorage`):

  | Voice | File | Size | License |
  |---|---|---|---|
  | Thorsten (male, high) | `de_DE-thorsten-high.onnx` | 114 MB | CC0 (Thorsten-Voice dataset) |
  | Kerstin (female, low) | `de_DE-kerstin-low.onnx` | 63 MB | CC0 (dataset-voice-kerstin) |

  Source: `rhasspy/piper-voices` (Piper models: MIT). The vendoring script
  re-checks `License: CC0` in each MODEL_CARD and pins every file by SHA-256.
  Runtime licenses: onnxruntime-web MIT, piper-wasm MIT, espeak-ng data
  GPL-3.0 (data only, unmodified).
* **Hosting**: `ops/vendor-tts-assets.sh build/tts-assets` (run by
  `ops/deploy.sh`; optional, a failed download falls back to system voices)
  stages `/static/tts-<hash>/` and `/static/tts-config.json`; nginx serves them
  via `ops/nginx/mag-speech.inc` (immutable, same-origin). No CDN at runtime.
* **Tests**: `python3 mag-room/tests/test_tts.py` (fallback order); with
  `MAG_TTS_ASSETS=build/tts-assets` it also synthesizes German with both
  voices and checks the cache.
