#!/usr/bin/env python3
"""Media extraction: video download, screenshots, clips.

Bug fixes from the original archive_video.py:
- Temp files use mkstemp (no collision in parallel mode)
- Temp MP4 always cleaned up (even on failure)
- ffmpeg timeouts and return code checks
- Broader context window for clip keyword matching (5 segments, not 1)
- 1hr+ timestamp parsing fixed
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import List, Tuple, Optional


# Visual-cue phrases that indicate the speaker is showing something on screen
VISUAL_CUES = [
    "as you can see", "you can see", "if you look", "look at this", "look here",
    "over here", "right here", "this section", "this is the", "you'll see",
    "if you have a look", "have a look", "on the screen", "shown here",
    "this dashboard", "this is what", "for example, if i", "let's go over",
    "you can preview", "we can see", "this website", "let me show you",
    "i'll show you", "as you saw", "this whole system", "this setup",
    "look at the", "this chart", "this graph", "according to", "as shown",
    "down here", "up here", "this part", "this page", "this screen",
]

# Keywords suggesting a high-value demo moment worth a clip
CLIP_KEYWORDS = [
    "dashboard", "diagram", "blueprint", "layer", "graph", "workspace",
    "system", "walk you through", "walkthrough", "show you", "demo",
    "preview", "mission control", "knowledge", "chart", "results", "example",
    "setup", "configure", "install", "build", "create", "design",
    "workflow", "pipeline", "architecture", "structure", "framework",
    "interface", "ui", "tool", "plugin", "extension", "template",
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def download_video(url: str, client_cycle: List[str], max_height: int = 720,
                   timeout: int = 120) -> Optional[str]:
    """Download video to a temp file. Returns path or None.

    Uses mkstemp to avoid collision in parallel mode.
    Cycles through client types on 403 errors.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="nuxtube_")
    os.close(fd)
    os.unlink(tmp_path)  # yt-dlp wants to control the filename extension

    base_path = tmp_path.replace(".mp4", "")
    output_template = f"{base_path}.%(ext)s"

    for client in client_cycle:
        try:
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "-f", f"best[height<={max_height}]/best",
                "--merge-output-format", "mp4",
                "-o", output_template,
                "--extractor-args", f"youtube:player_client={client}",
                "--no-warnings",
                url,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                # Find the downloaded file
                for f in os.listdir(os.path.dirname(base_path) or "."):
                    if f.startswith(os.path.basename(base_path)) and f.endswith(".mp4"):
                        return os.path.join(os.path.dirname(base_path) or ".", f)
            # Check for 403
            if "403" in result.stderr or "Forbidden" in result.stderr:
                continue  # Try next client
            # Other errors — try next client too
            continue
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue

    return None


def find_visual_cues(segments: List[str], collapse_window: int = 8) -> List[dict]:
    """Find visual-cue moments in transcript segments.

    Returns list of {timestamp, phrase, context, context_window} dicts.
    Uses a broader context window (5 segments around cue) for clip scoring.
    """
    from .transcript import parse_ts

    cues = []
    for i, seg in enumerate(segments):
        # Parse timestamp and text
        lower = seg.lower()
        for phrase in VISUAL_CUES:
            if phrase in lower:
                ts = parse_ts(seg)
                if ts is None:
                    continue
                # Collapse: skip if within collapse_window seconds of last cue
                if cues and ts - cues[-1]["timestamp"] < collapse_window:
                    break
                # Build context from surrounding segments (5-window: 2 before, current, 2 after)
                context_segs = []
                for j in range(max(0, i - 2), min(len(segments), i + 3)):
                    context_segs.append(segments[j])
                context = " ".join(context_segs)

                cues.append({
                    "timestamp": ts,
                    "phrase": phrase,
                    "context": context,
                    "segment_index": i,
                })
                break

    return cues


def take_screenshots(video_path: str, cues: List[dict],
                     output_dir: str, offset: int = 3) -> List[dict]:
    """Take a screenshot at each cue timestamp + offset.

    Returns list of {timestamp, screenshot, ok} dicts.
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []
    for cue in cues:
        ts = cue["timestamp"] + offset
        fname = f"{ts // 60:02d}m{ts % 60:02d}s.jpg"
        path = os.path.join(output_dir, fname)
        try:
            cmd = [
                "ffmpeg", "-y", "-ss", str(ts),
                "-i", video_path,
                "-frames:v", "1", "-q:v", "2",
                path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            ok = result.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 100
            results.append({
                "timestamp": cue["timestamp"],
                "screenshot": f"screenshots/{fname}",
                "ok": ok,
            })
        except Exception:
            results.append({
                "timestamp": cue["timestamp"],
                "screenshot": f"screenshots/{fname}",
                "ok": False,
            })
    return results


def extract_clips(video_path: str, cues: List[dict],
                  output_dir: str, config) -> List[dict]:
    """Extract short clips for high-value demo moments.

    Scores cues by keyword matches in the broader context window (fixes
    the original bug where only 1 segment was checked, causing 44% of
    videos to get 0 clips).

    Returns list of {timestamp, clip, ok} dicts.
    """
    os.makedirs(output_dir, exist_ok=True)
    max_clips = config.max_clips
    clip_duration = config.clip_duration
    clip_start_offset = config.clip_start_offset

    # Score each cue by keyword matches in its context
    scored = []
    for cue in cues:
        ctx_lower = cue["context"].lower()
        score = sum(1 for kw in CLIP_KEYWORDS if kw in ctx_lower)
        if score > 0:
            scored.append((score, cue))
    scored.sort(key=lambda x: -x[0])  # Highest score first
    selected = scored[:max_clips]
    # Sort by timestamp for chronological clips
    selected.sort(key=lambda x: x[1]["timestamp"])

    results = []
    for score, cue in selected:
        start = max(0, cue["timestamp"] + clip_start_offset)
        fname = f"{start // 60:02d}m{start % 60:02d}s.mp4"
        path = os.path.join(output_dir, fname)
        try:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(clip_duration),
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac",
                path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            ok = result.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 100
            results.append({
                "timestamp": cue["timestamp"],
                "clip": f"clips/{fname}",
                "ok": ok,
                "score": score,
            })
        except Exception:
            results.append({
                "timestamp": cue["timestamp"],
                "clip": f"clips/{fname}",
                "ok": False,
                "score": score,
            })
    return results


def cleanup_temp(path: str):
    """Always clean up temp files, even on failure."""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass
