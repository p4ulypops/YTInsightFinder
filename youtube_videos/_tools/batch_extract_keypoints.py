#!/usr/bin/env python3
"""Batch extract key points from all videos that don't have them yet."""
import subprocess, os, sys, time

BASE = "/Users/user/NuxOS/youtube_videos"
SCRIPT = os.path.join(BASE, "_tools", "extract_keypoints.py")

todo = []
for cat in os.listdir(BASE):
    cat_path = os.path.join(BASE, cat)
    if not os.path.isdir(cat_path) or cat.startswith("_") or cat.startswith("."):
        continue
    for vid in os.listdir(cat_path):
        vid_path = os.path.join(cat_path, vid)
        if not os.path.isdir(vid_path):
            continue
        if not os.path.exists(os.path.join(vid_path, "key-points.json")):
            todo.append(f"{cat}/{vid}")

print(f"Processing {len(todo)} videos...")
for i, v in enumerate(todo, 1):
    print(f"[{i}/{len(todo)}] {v}", flush=True)
    r = subprocess.run(
        ["python3", SCRIPT, v],
        capture_output=True, text=True, timeout=180,
        cwd=BASE
    )
    if r.returncode == 0:
        print(f"  OK: {r.stdout.strip().split(chr(10))[-1]}", flush=True)
    else:
        print(f"  FAIL: {r.stderr[:200]}", flush=True)

print(f"\nDone! Processed {len(todo)} videos.")
