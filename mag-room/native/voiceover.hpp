// Non-destructive voiceover for an existing video attachment.
// The original video and every microphone take remain separate room files;
// bake creates an additional MP4 attachment, never overwrites its inputs.
#pragma once
#include "room.hpp"

namespace mag::voiceover {

inline const Val *attachment(const Val &message, const std::string &file) {
  const Val *list = message.find("attachments");
  if (!list || !list->isArr()) return nullptr;
  for (const auto &a : list->asArr())
    if (jstr(a, "file") == file) return &a;
  return nullptr;
}

inline bool original(const Val &message, const std::string &file) {
  const Val *a = attachment(message, file);
  return a && jstr(*a, "kind") == "video" && jstr(*a, "voiceover_kind").empty();
}

inline bool take(const Val &message, const std::string &video, const std::string &file) {
  const Val *a = attachment(message, file);
  return a && jstr(*a, "kind") == "audio" && jstr(*a, "voiceover_kind") == "take" &&
         jstr(*a, "voiceover_for") == video;
}

// Probe the uploaded *contents*, not the browser-provided MIME label.
// ffprobe rejects broken / disguised microphone recordings before persistence.
inline bool audio_ok(const std::string &path) {
  RunOptions opts; opts.timeout_s = 12; opts.cap = 256;
  const RunResult r = run({"ffprobe", "-v", "error", "-select_streams", "a:0",
                           "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", path}, opts);
  return r.spawned && r.code == 0 && trim(r.out) == "audio";
}

inline double seconds(const std::string &path) {
  RunOptions opts; opts.timeout_s = 12; opts.cap = 128;
  const RunResult r = run({"ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path}, opts);
  if (!r.spawned || r.code != 0) return 0;
  try {
    const double s = std::stod(trim(r.out));
    return std::isfinite(s) && s > 0 && s <= 600 ? s : 0;
  } catch (...) { return 0; }
}

inline bool bake(const std::string &video, const std::string &audio, const std::string &out,
                 const std::string &mode, std::string &error) {
  const double length = seconds(video), take_length = seconds(audio);
  if (!length || !take_length || !audio_ok(audio)) { error = "Video oder Tonspur ist beschädigt oder zu lang."; return false; }
  if (take_length > length + 2) { error = "Tonaufnahme ist länger als das Video."; return false; }
  const bool with_original = audio_ok(video);
  std::vector<std::string> args = {
      "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
      "-i", video, "-i", audio};
  if (mode == "mix" && with_original) {
    args.insert(args.end(), {"-filter_complex",
                             "[0:a:0]aresample=async=1:first_pts=0[orig];"
                             "[1:a:0]aresample=async=1:first_pts=0[dub];"
                             "[orig][dub]amix=inputs=2:duration=first:dropout_transition=0:"
                             "weights=0.45 1:normalize=0,alimiter=limit=0.95[mixed]",
                             "-map", "0:v:0", "-map", "[mixed]", "-map", "0:a:0"});
  } else {
    args.insert(args.end(), {"-map", "0:v:0", "-map", "1:a:0"});
    if (with_original) args.insert(args.end(), {"-map", "0:a:0"});
  }
  args.insert(args.end(), {"-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                           "-metadata:s:a:0", "title=Nachvertonung",
                           "-disposition:a:0", "default"});
  if (with_original)
    args.insert(args.end(), {"-metadata:s:a:1", "title=Original", "-disposition:a:1", "0"});
  args.insert(args.end(), {"-t", std::to_string(length), "-movflags", "+faststart", out});
  RunOptions opts; opts.timeout_s = 150; opts.cap = 1400;
  const RunResult result = run(args, opts);
  if (!result.spawned || result.code != 0 || !exists(out) || file_size(out) == 0) {
    error = result.timed_out ? "Video-Nachvertonung hat das Zeitlimit überschritten."
                             : "Audio konnte nicht eingebettet werden: " + clip(trim(result.out), 500);
    return false;
  }
  return true;
}

} // namespace mag::voiceover
