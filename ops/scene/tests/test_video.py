#!/usr/bin/env python3
"""What video.py must keep: a person seen in a video becomes a motion of the
scene that is seen from the body itself like the recorded ones (legs that
hang point down, standing straight is no turn), goes the way the person went
in the picture (across the frame and further away), looks where the person
looked, and is given to the figure that stood where the person stood.
Synthetic people, numpy only (no MediaPipe)."""
import math
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import motion  # noqa: E402
import video  # noqa: E402

W, H, FPS = 832, 480, 16.0
SIDE = max(W, H)


def standing_body(face_camera=True):
    """MediaPipe's world points of somebody standing straight (metres around
    the hips; x to the right of the picture, y down, z away). Facing the
    camera its left is on the right of the picture; else it looks to the
    right of the picture (its left is away from the camera)."""
    pts = np.zeros((33, 3))
    def put(j, left, down, ahead):              # in the body's own words
        if face_camera:
            pts[j] = [left, down, -ahead]
        else:
            pts[j] = [ahead, down, left]
    for j, side in ((video.HIP_L, 1), (video.HIP_R, -1)):
        put(j, 0.1 * side, 0.0, 0.0)
    for j, side in ((video.KNEE_L, 1), (video.KNEE_R, -1)):
        put(j, 0.1 * side, 0.45, 0.0)
    for j, side in ((video.ANKLE_L, 1), (video.ANKLE_R, -1)):
        put(j, 0.1 * side, 0.9, 0.0)
    for j, side in ((video.HEEL_L, 1), (video.HEEL_R, -1)):
        put(j, 0.1 * side, 0.95, -0.05)
    for j, side in ((video.TOE_L, 1), (video.TOE_R, -1)):
        put(j, 0.1 * side, 0.95, 0.15)
    for j, side in ((video.SHOULDER_L, 1), (video.SHOULDER_R, -1)):
        put(j, 0.18 * side, -0.5, 0.0)
    for j, side in ((video.ELBOW_L, 1), (video.ELBOW_R, -1)):
        put(j, 0.2 * side, -0.22, 0.0)
    for j, side in ((video.WRIST_L, 1), (video.WRIST_R, -1)):
        put(j, 0.2 * side, 0.03, 0.0)
    for j, side in ((video.EAR_L, 1), (video.EAR_R, -1)):
        put(j, 0.07 * side, -0.68, 0.0)
    put(video.NOSE, 0.0, -0.66, 0.1)
    return pts


def seen_as(world, hips_at, depth):
    """Where a pinhole camera (video.FILM) sees these points: x, y as parts of the frame."""
    pts = world + hips_at
    f = video.FILM * SIDE
    x = (pts[:, 0] * f / (depth + world[:, 2])) / W + 0.5
    y = (pts[:, 1] * f / (depth + world[:, 2])) / H + 0.5
    return np.stack([x, y, np.ones(33)], axis=1)


def walk(across_m, face_camera=True, frames=33, depth=4.0, start=-0.5):
    """Somebody standing straight who slides `across_m` metres to the right of the picture."""
    world = standing_body(face_camera)
    pic = np.zeros((frames, 33, 3))
    for i in range(frames):
        pic[i] = seen_as(world, np.array([start + across_m * i / (frames - 1), 0.4, 0.0]), depth)
    return {"fps": FPS, "width": W, "height": H, "frames": frames, "pic": pic[None], "world": np.repeat(world[None, None], frames, axis=1)}


def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    return bool(ok)


def main():
    good = True
    seen = walk(1.0)
    found = video.people(seen)
    good &= check("one person, seen left of the middle", len(found) == 1 and found[0]["across"] < 0)
    clip, heading, leg = video.clip_of(seen, 0, 4.0)
    good &= check("as long as the video (2 s at 30 a second)", clip.frames == 61 and abs(clip.seconds - 2.0) < 1e-6)
    good &= check("it looks at the camera: back along the scene's x", abs(motion.wrap(heading - math.pi)) < 0.1)
    good &= check("its legs are its legs (0.9 m)", abs(leg - 0.9) < 0.02)
    down = np.array([0.0, 0.0, -1.0])
    good &= check("legs that hang point down", np.all((clip.limbs[:, :4] * down).sum(axis=-1) > 0.99))
    good &= check("standing straight is no turn", np.all(np.abs(clip.hips[:, 0]) > 0.99) and np.all(np.abs(clip.chest[:, 0]) > 0.99))
    good &= check("its head looks ahead", np.all(np.abs(clip.head[:, 0]) > 0.97))
    good &= check("its hips are a leg above its ankles", np.all(np.abs(clip.root[:, 2] - 1.0) < 0.03))
    # To the right of the picture is to its left, facing the camera: a metre, sideways, not turning.
    way = clip.root[-1, :2] * leg
    good &= check("it went a metre to its left (%.2f, %.2f)" % tuple(way), abs(way[1] - 1.0) < 0.12 and abs(way[0]) < 0.12)
    good &= check("it did not turn", np.all(np.abs(clip.root[:, 3]) < 0.05))
    good &= check("its feet are on the ground most of the time", (clip.contact > 0).mean() > 0.6)
    # Looking to the right of the picture and going there: forwards.
    seen = walk(1.0, face_camera=False)
    clip, heading, leg = video.clip_of(seen, 0, 4.0)
    good &= check("it looks to the right of the picture: the scene's -y", abs(motion.wrap(heading + math.pi / 2)) < 0.1)
    way = clip.root[-1, :2] * leg
    good &= check("it went a metre forwards (%.2f, %.2f)" % tuple(way), abs(way[0] - 1.0) < 0.12 and abs(way[1]) < 0.12)
    # Away from the camera: smaller in the picture is further away.
    world = standing_body()
    pic = np.stack([seen_as(world, np.array([0.0, 0.4, 0.0]), 4.0 + 2.0 * i / 32) for i in range(33)])
    far = {"fps": FPS, "width": W, "height": H, "frames": 33, "pic": pic[None], "world": np.repeat(world[None, None], 33, axis=1)}
    clip, heading, leg = video.clip_of(far, 0, 4.0)
    way = clip.root[-1, :2] * leg
    good &= check("further away is backwards for who faces the camera (%.2f, %.2f)" % tuple(way), abs(way[0] + 2.0) < 0.3 and abs(way[1]) < 0.2)
    # Who is who: the figure that stood where the person stood.
    two = walk(0.0, start=0.8)
    two["pic"] = np.concatenate([walk(0.0, start=-0.8)["pic"], two["pic"]])
    two["world"] = np.concatenate([two["world"], two["world"]])
    found = video.people(two)
    pairs = video.match(found, [("Mann", found[1]["across"] + 0.02), ("Frau", found[0]["across"] - 0.01), ("Baum", 0.6)])
    good &= check("each person is the figure that stood there", pairs == {0: "Frau", 1: "Mann"})
    good &= check("nobody is a figure far from where it stood", video.match(found[:1], [("Baum", 0.6)]) == {})
    # The motion plays: a figure of a grown person's build follows it in a scene.
    lib = motion.Library()
    clip, heading, leg = video.clip_of(seen, 0, 4.0, "video-0")
    lib.clips[clip.name] = clip
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from test_motion import grown  # noqa: E402
    bones, front = grown()
    body = motion.Body(bones, front, 1.0, lib.neutral)
    p = motion.Performer(body, lib, 24, (0.0, 0.0), heading)
    p.change(clip.name, at=0.0, span=0.15)
    for _ in range(int(round(clip.seconds * 24))):
        p.tick()
    moved = p.xy - np.array([0.0, 0.0])
    good &= check("the figure goes where the person went (%.2f, %.2f)" % tuple(moved), abs(moved[1] + 1.0 * body.leg / leg) < 0.25 and abs(moved[0]) < 0.25)
    if not good:
        sys.exit(1)


if __name__ == "__main__":
    main()
