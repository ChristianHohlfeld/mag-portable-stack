#!/usr/bin/env python3
"""Classify /api/state for deploy drain.

Reads one JSON object from stdin. Prints:

    user_busy user_pending selftalk_busy selftalk_held progress

user_busy / user_pending are ordinary replies that must finish before a
release switch. selftalk_busy is bot-to-bot work: a room that honored
deploy-hold (selftalk_held=1) is waited out until the current turn ends;
an older room that cannot pause is not a drain blocker. progress is a
token/text fingerprint used by the stale shortcut.
"""
from __future__ import annotations

import json
import os
import sys


def _jobs(blob):
    if not isinstance(blob, dict):
        return []
    act = blob.get("actives")
    if not isinstance(act, list):
        active = blob.get("active")
        act = [active] if isinstance(active, dict) else []
    jobs = blob.get("jobs")
    if not isinstance(jobs, list):
        jobs = []
    return [j for j in list(act) + jobs if isinstance(j, dict)]


def _queue_jobs(state_dir):
    path = os.path.join(state_dir, "queue.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return _jobs(json.load(fh))
    except (OSError, ValueError, TypeError):
        return None


def classify(data, state_dir=""):
    pending = data.get("pending") or []
    if not isinstance(pending, list):
        pending = []
    user_pending = 0 if data.get("queue_paused") else len(pending)
    bits = []
    for act in data.get("activities") or []:
        if isinstance(act, dict):
            bits.append(
                "%s/%s/%s/%s"
                % (
                    act.get("processed"),
                    act.get("total"),
                    act.get("phase"),
                    len(act.get("steps") or []),
                )
            )
    text = 0
    for msg in data.get("room") or []:
        if isinstance(msg, dict):
            text += len(msg.get("content") or "") + len(msg.get("reasoning") or "")
    progress = "p%s:t%d" % (",".join(bits), text)

    if "user_busy" in data or "selftalk_busy" in data or "selftalk_held" in data:
        return (
            1 if data.get("user_busy") else 0,
            user_pending,
            1 if data.get("selftalk_busy") else 0,
            1 if data.get("selftalk_held") else 0,
            progress,
        )

    selftalk = bool(data.get("selftalk"))
    jobs = _queue_jobs(state_dir) if state_dir else None
    if jobs is not None:
        user_job = any(not j.get("selftalk") for j in jobs)
        talk_job = any(j.get("selftalk") for j in jobs)
        if user_job:
            return 1, user_pending, 1 if talk_job else 0, 0, progress
        if talk_job or (data.get("busy") and selftalk and user_pending == 0):
            return 0, user_pending, 1, 0, progress
        if data.get("busy"):
            return 1, user_pending, 0, 0, progress
        return 0, user_pending, 0, 0, progress

    if data.get("busy") and selftalk and user_pending == 0:
        return 0, user_pending, 1, 0, progress
    if data.get("busy"):
        return 1, user_pending, 0, 0, progress
    return 0, user_pending, 0, 0, progress


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("0 0 0 0 -")
        return 0
    if not isinstance(data, dict):
        print("0 0 0 0 -")
        return 0
    state_dir = os.environ.get("MAG_ROOM_STATE", "")
    user_busy, user_pending, selftalk_busy, selftalk_held, progress = classify(
        data, state_dir
    )
    print(
        "%d %d %d %d %s"
        % (user_busy, user_pending, selftalk_busy, selftalk_held, progress)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
