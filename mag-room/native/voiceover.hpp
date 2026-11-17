// Non-destructive voiceover for an existing video attachment.
// The original video and every microphone take remain separate room files;
// bake creates an additional MP4 attachment, never overwrites its inputs.
#pragma once
#include "room.hpp"
#include <cmath>

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

inline bool lane(const std::string &name) {
  return name == "voice" || name == "melody" || name == "effects";
}
inline bool take(const Val &message, const std::string &video, const std::string &file,
                 const std::string &lane_name) {
  const Val *a = attachment(message, file);
  // Takes created by the first draft of this feature predate named lanes.
  const std::string stored_lane = (a ? jstr(*a, "voiceover_lane", "voice") : "");
  return a && lane(lane_name) && jstr(*a, "kind") == "audio" &&
         jstr(*a, "voiceover_kind") == "take" && jstr(*a, "voiceover_for") == video &&
         stored_lane == lane_name;
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
    if (std::isfinite(s) && s > 0 && s <= 600) return s;
    if (std::isfinite(s) && s > 600) return 0;
  } catch (...) {}
  // MediaRecorder in Chromium commonly produces valid WebM/Opus without a
  // container duration. Derive it from packet timestamps rather than
  // rejecting a real browser capture. Cap the output/CPU time.
  opts.timeout_s = 18; opts.cap = 2u << 20;
  const RunResult packets = run({"ffprobe", "-v", "error", "-select_streams", "a:0",
                                 "-show_packets", "-show_entries", "packet=pts_time,duration_time",
                                 "-of", "csv=p=0", path}, opts);
  if (!packets.spawned || packets.code != 0 || packets.truncated) return 0;
  double last = 0;
  std::istringstream lines(packets.out);
  for (std::string line; std::getline(lines, line);) {
    const size_t comma = line.find(',');
    if (comma == std::string::npos) continue;
    try {
      const double pts = std::stod(line.substr(0, comma));
      const double duration = std::stod(line.substr(comma + 1));
      if (std::isfinite(pts) && std::isfinite(duration))
        last = std::max(last, pts + duration);
    } catch (...) {}
  }
  return last > 0 && last <= 600 ? last : 0;
}

// Only the melody lane has timeline placement and source in-point.
struct LaneInput {
  std::string name, path;
  double at = 0;   // where music starts on the VIDEO timeline
  double from = 0; // where playback starts in the SOURCE song
  double gain = 1; // user-selected 0..1 gain
  double until = 0; // optional trim-out on video timeline, 0 = natural end
};

// Each of the three lanes is optional. They are deliberately mixed to ONE
// audio master. Original MP4 soundtrack remains an alternate audio track.
inline bool bake(const std::string &video,
                 const std::vector<LaneInput> &lanes,
                 const std::string &out, const std::string &mode, std::string &error, double original_gain = 0.55) {
  const double length = seconds(video);
  if (!length || lanes.empty() || lanes.size() > 3 || !std::isfinite(original_gain) || original_gain < 0 || original_gain > 1) {
    error = "Video oder Tonspuren fehlen oder sind zu lang."; return false;
  }
  const bool original_audio = audio_ok(video);
  std::vector<std::string> args = {"ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", video};
  std::string filters, inputs, weights;
  int n = 0;
  auto append_mix = [&](const std::string &label, const std::string &weight) {
    inputs += "[" + label + "]";
    if (!weights.empty()) weights += " ";
    weights += weight;
    ++n;
  };
  if (mode == "mix" && original_audio) {
    filters += "[0:a:0]aresample=async=1:first_pts=0,apad,atrim=0:" + std::to_string(length) + "[orig];";
    append_mix("orig", std::to_string(original_gain));
  }
  for (size_t i = 0; i < lanes.size(); ++i) {
    const LaneInput &input = lanes[i];
    if (!lane(input.name) || !std::isfinite(input.gain) || input.gain < 0 || input.gain > 1 || !audio_ok(input.path)) {
      error = "Tonspur ist beschädigt oder hat eine ungültige Zuordnung."; return false;
    }
    const double duration = seconds(input.path);
    if (!duration) { error = "Tonspur ist nicht lesbar oder länger als zehn Minuten."; return false; }
    if (input.name != "melody" && duration > length + 2) {
      error = "Sprach- oder Geräuschspur ist länger als das Video."; return false;
    }
    if (input.name == "melody" &&
        (!std::isfinite(input.at) || !std::isfinite(input.from) || !std::isfinite(input.until) ||
         input.at < 0 || input.at >= length || input.from < 0 || input.from >= duration ||
         (input.until != 0 && (input.until <= input.at || input.until > length)))) {
      error = "Melodie-Einsatz oder Song-Start liegt außerhalb der Audiodauer."; return false;
    }
    args.insert(args.end(), {"-i", input.path});
    const std::string label = "lane" + std::to_string(i + 1);
    const std::string channel = "[" + std::to_string(i + 1) + ":a:0]";
    if (input.name == "melody") {
      // Trim SOURCE, reset its clock, then insert silence on the VIDEO timeline.
      // adelay's leading silence may carry NOPTS: without restamping *after*
      // the delay, the muxer can discard it and start music at video 0.
      // Sample-count PTS also keeps A/V in sync for AAC, MP3 and WAV.
      const long long delay_ms = std::llround(input.at * 1000);
      const double audible = std::min(duration - input.from,
        (input.until > input.at ? input.until : length) - input.at);
      const double fade = std::min(0.01, audible / 2);
      filters += channel + "atrim=start=" + std::to_string(input.from) +
                 ",asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,afade=t=in:st=0:d=" +
                 std::to_string(fade) + ",afade=t=out:st=" +
                 std::to_string(std::max(0.0, audible - fade)) + ":d=" + std::to_string(fade) +
                 ",atrim=0:" + std::to_string(audible) + ",adelay=" +
                 std::to_string(delay_ms) + ":all=1,asetpts=N/SR/TB,apad,atrim=0:" +
                 std::to_string(length) + "[" + label + "];";
    } else {
      const double audible = std::min(duration, length);
      const double fade = std::min(0.01, audible / 2);
      filters += channel + "aresample=async=1:first_pts=0,afade=t=in:st=0:d=" +
                 std::to_string(fade) + ",afade=t=out:st=" +
                 std::to_string(std::max(0.0, audible - fade)) + ":d=" + std::to_string(fade) +
                 ",atrim=0:" + std::to_string(audible) + ",apad,atrim=0:" +
                 std::to_string(length) + "[" + label + "];";
    }
    append_mix(label, std::to_string(input.gain));
  }
  // Amix's default "duration=longest" is limited by our pads to the video
  // duration, so short takes do not prematurely cut off music or ambience.
  filters += inputs + "amix=inputs=" + std::to_string(n) +
             ":duration=longest:dropout_transition=0.01:weights='" + weights +
             "':normalize=0,alimiter=limit=0.95[mixed]";
  args.insert(args.end(), {"-filter_complex", filters, "-map", "0:v:0", "-map", "[mixed]"});
  if (original_audio) args.insert(args.end(), {"-map", "0:a:0"});
  args.insert(args.end(), {"-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                           "-metadata:s:a:0", "title=Mix: Stimme Melodie Geraeusche",
                           "-disposition:a:0", "default"});
  if (original_audio)
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

// Extract the mixed first audio stream losslessly into its own M4A. It is a
// real fourth lane for re-use/download; baking does NOT destroy the three
// independent sources or the original video.
inline bool extract_mix(const std::string &mp4, const std::string &m4a) {
  RunOptions opts; opts.timeout_s = 30; opts.cap = 500;
  const RunResult result = run({"ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                                "-i", mp4, "-map", "0:a:0", "-vn", "-c:a", "copy", m4a}, opts);
  return result.spawned && result.code == 0 && exists(m4a) && file_size(m4a) > 0;
}

} // namespace mag::voiceover
