#!/usr/bin/env python3
"""OmniFile — single comprehensive JSON archive for a video.

Aggregates all per-video artifacts into one portable document:
metadata, transcript, key points, player data, screenshots, and clips.

The omni.json file powers the HTML viewer and can be loaded by external tools.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

OMNI_VERSION = "1.0"


def build_omni(folder: str) -> Optional[dict]:
    """Build OmniFile dict from a video archive folder. Returns None if missing."""
    folder_path = Path(folder)
    if not folder_path.exists():
        return None

    omni = {
        "omni_version": OMNI_VERSION,
        "generated_at": datetime.now().isoformat(),
        "folder": str(folder_path.resolve()),
    }

    # Metadata
    meta_path = folder_path / "metadata.json"
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as f:
            omni["metadata"] = json.load(f)
    else:
        omni["metadata"] = {}

    # Transcript (parsed from markdown)
    transcript_path = folder_path / "transcript.md"
    if transcript_path.exists():
        raw = transcript_path.read_text(encoding="utf-8", errors="replace")
        omni["transcript"] = _parse_transcript_md(raw)
    else:
        omni["transcript"] = {}

    # Key points
    kp_path = folder_path / "key-points.json"
    if kp_path.exists():
        with open(kp_path, encoding="utf-8") as f:
            omni["key_points"] = json.load(f)
    else:
        kp_md = folder_path / "key-points.md"
        if kp_md.exists():
            omni["key_points"] = {
                "raw_md": kp_md.read_text(encoding="utf-8", errors="replace")
            }
        else:
            omni["key_points"] = {}

    # Player data (may already be in metadata.player_data)
    omni["player_data"] = omni["metadata"].get("player_data", {})

    # Screenshots
    ss_manifest = folder_path / "_screenshots_manifest.json"
    if ss_manifest.exists():
        with open(ss_manifest, encoding="utf-8") as f:
            omni["screenshots"] = json.load(f)
    else:
        ss_dir = folder_path / "screenshots"
        if ss_dir.exists():
            omni["screenshots"] = [
                {"path": f"screenshots/{fname}", "timestamp": None, "ok": True}
                for fname in sorted(os.listdir(ss_dir))
                if fname.lower().endswith((".jpg", ".jpeg", ".png"))
            ]
        else:
            omni["screenshots"] = []

    # Clips
    clips_manifest = folder_path / "_clips_manifest.json"
    if clips_manifest.exists():
        with open(clips_manifest, encoding="utf-8") as f:
            omni["clips"] = json.load(f)
    else:
        clips_dir = folder_path / "clips"
        if clips_dir.exists():
            omni["clips"] = [
                {"path": f"clips/{fname}", "timestamp": None, "ok": True}
                for fname in sorted(os.listdir(clips_dir))
                if fname.lower().endswith((".mp4", ".webm", ".mov"))
            ]
        else:
            omni["clips"] = []

    # Synthesised moments — heatmap peaks enriched with transcript snippets
    # Useful in transcript-only mode: gives key moment context without any video download
    omni["synthesised_moments"] = _synthesise_moments(omni)

    # File index
    files = {}
    for entry in sorted(folder_path.iterdir()):
        if entry.is_file():
            stat = entry.stat()
            files[entry.name] = {
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
    omni["files"] = files

    return omni


def _synthesise_moments(omni: dict) -> list:
    """Combine heatmap peaks + chapters + transcript snippets into enriched moment objects.

    No video needed — works entirely from transcript and player_data.
    Returns a list sorted by timestamp, each entry has:
      timestamp, chapter_title, heatmap_score, transcript_excerpt, source
    """
    pd = omni.get("player_data", {})
    chapters = pd.get("chapters", [])
    heatmap = pd.get("heatmap", [])
    ts_text = omni.get("transcript", {}).get("timestamped_text", "")

    if not heatmap and not chapters:
        return []

    # Build chapter lookup: for a given timestamp, find its chapter title
    def find_chapter(ts_sec: float) -> str:
        best = ""
        for ch in chapters:
            ch_start = ch.get("start_time", 0) or 0
            if ch_start <= ts_sec:
                best = ch.get("title", "")
        return best

    # Find transcript excerpt near a timestamp
    def find_excerpt(ts_sec: float, window: int = 30) -> str:
        if not ts_text:
            return ""
        import re as _re
        # Find lines with timestamps within ±window seconds
        lines = ts_text.split("\n")
        excerpts = []
        for line in lines:
            m = _re.search(r"\[(\d+):(\d+)(?::(\d+))?\]", line)
            if m:
                g = m.groups()
                if g[2] is not None:
                    line_ts = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2])
                else:
                    line_ts = int(g[0]) * 60 + int(g[1])
                if abs(line_ts - ts_sec) <= window:
                    text = _re.sub(r"\[\d+:\d+(?::\d+)?\]\s*", "", line).strip()
                    if text:
                        excerpts.append(text)
        return " ".join(excerpts[:4])[:300]

    moments = []
    seen_ts = set()

    # Top heatmap peaks (top 15 by engagement)
    top_hm = sorted(heatmap, key=lambda x: x.get("heat_value", x.get("value", 0)), reverse=True)[:15]
    for h in top_hm:
        ts_s = (h.get("start_millis") or 0) / 1000
        ts_rounded = round(ts_s)
        if ts_rounded in seen_ts:
            continue
        seen_ts.add(ts_rounded)
        moments.append({
            "timestamp": ts_s,
            "chapter_title": find_chapter(ts_s),
            "heatmap_score": round(h.get("heat_value", h.get("value", 0)), 4),
            "transcript_excerpt": find_excerpt(ts_s),
            "source": "heatmap",
        })

    # Chapter start points not already covered
    for ch in chapters:
        ts_s = float(ch.get("start_time", 0) or 0)
        ts_rounded = round(ts_s)
        if ts_rounded in seen_ts:
            continue
        seen_ts.add(ts_rounded)
        moments.append({
            "timestamp": ts_s,
            "chapter_title": ch.get("title", ""),
            "heatmap_score": None,
            "transcript_excerpt": find_excerpt(ts_s),
            "source": "chapter",
        })

    # Sort chronologically
    moments.sort(key=lambda x: x["timestamp"])
    return moments


def _parse_transcript_md(raw: str) -> dict:
    """Extract full_text and timestamped_text from transcript.md sections."""
    lines = raw.split("\n")
    full_lines = []
    ts_lines = []
    mode = None

    for line in lines:
        if "## Plain Text" in line:
            mode = "plain"
            continue
        if "## Timestamped Transcript" in line:
            mode = "ts"
            continue
        if line.startswith("## ") or line.startswith("# "):
            mode = None
            continue
        if line.startswith("---"):
            mode = None
            continue
        if mode == "plain":
            full_lines.append(line)
        elif mode == "ts":
            ts_lines.append(line)

    return {
        "full_text": "\n".join(full_lines).strip(),
        "timestamped_text": "\n".join(ts_lines).strip(),
    }


def write_omni(folder: str) -> Optional[str]:
    """Build and write omni.json to the archive folder. Returns output path or None."""
    omni = build_omni(folder)
    if not omni:
        return None
    out_path = Path(folder) / "omni.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(omni, f, indent=2, ensure_ascii=False, default=str)
    return str(out_path)
