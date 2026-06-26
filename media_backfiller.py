#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  📸 NuxTube Media Backfiller                                      ║
║  ═══════════════════════════════════                              ║
║  For videos that have transcript + metadata but NO screenshots   ║
║  or clips. Downloads video segments via yt-dlp, extracts          ║
║  screenshots and clips via ffmpeg, writes manifests, and          ║
║  updates the Obsidian media-index pages.                          ║
║                                                                  ║
║  Data source: /Volumes/PSILVER-2TB/YoutubeInsights/              ║
║  Vault:        /Volumes/PSILVER-2TB/NuxTubeInsights/              ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
    python3 media_backfiller.py              # foreground (dashboard)
    python3 media_backfiller.py --dry-run    # scan only, report what needs doing
"""

import asyncio
import aiohttp
import json
import os
import re
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

ARCHIVE_ROOT = Path("/Volumes/PSILVER-2TB/YoutubeInsights")
TEST_DATA_ROOT = Path("/Users/user/Projects2026/NeuroD-NuxTube/youtube_videos/_test_data")
VAULT_ROOT = Path("/Volumes/PSILVER-2TB/NuxTubeInsights")
NUXTUBE_ROOT = Path("/Users/user/Projects2026/NeuroD-NuxTube")

CONCURRENCY = 3  # Download concurrency (network-bound, not CPU)
QUALITY = "720p"
SCREENSHOT_OFFSET = 3  # seconds after cue timestamp
CLIP_DURATION = 16
CLIP_START_OFFSET = -4
MAX_CLIPS = 8
MAX_HEIGHT = 720

# Visual cue phrases (from nuxtube/media.py)
VISUAL_CUES = [
    "let me show", "here we can see", "look at this", "as you can see",
    "check this out", "watch this", "so this is", "here is", "this is the",
    "let's look", "let's go to", "switch over", "pull up", "open up",
    "i want to show", "notice how", "you can see", "you can see here",
    "if we look", "if i scroll", "if i go", "navigate to", "go ahead and",
    "let's create", "let's build", "let me demonstrate", "demonstrating",
    "this screen", "this page", "this view", "this tab", "the dashboard",
    "the interface", "the ui", "this feature", "this is what",
    "here's an example", "for example", "as an example", "and here",
    "this right here", "right here", "this part", "this section",
    "and then", "and here we", "and now", "next we",
    "this is where", "this shows", "this displays",
]

CLIP_KEYWORDS = [
    "demo", "demonstrate", "show", "example", "walk through", "walkthrough",
    "tutorial", "how to", "step by step", "let me show", "look at this",
    "check this out", "watch this", "so this is", "build", "create",
    "result", "outcome", "before and after", "comparison",
]

CLIENT_CYCLE = ["android", "ios", "tv", "web_safari", "mweb"]

# ═══════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════

stats = {
    "total": 0,
    "completed": 0,
    "failed": 0,
    "skipped": 0,
    "screenshots_taken": 0,
    "clips_extracted": 0,
    "downloads_failed": 0,
    "ffmpeg_errors": 0,
    "start_time": None,
}

# ═══════════════════════════════════════════════════════════════════
# TERMINAL DASHBOARD
# ═══════════════════════════════════════════════════════════════════

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    DIM = "\033[2m"

def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")

def print_dashboard(current: str = "", status: str = ""):
    clear_screen()
    elapsed = time.time() - stats["start_time"] if stats["start_time"] else 0
    mins, secs = divmod(int(elapsed), 60)
    hours, mins = divmod(mins, 60)
    
    total = stats["total"]
    done = stats["completed"]
    failed = stats["failed"]
    skipped = stats["skipped"]
    remaining = total - done - failed - skipped
    pct = (done / total * 100) if total > 0 else 0
    
    bar_width = 40
    filled = int(bar_width * done / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    
    print(f"{Colors.CYAN}{Colors.BOLD}")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  📸 NUXTUBE MEDIA BACKFILLER                                      ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}")
    
    print(f"  {Colors.GREEN}📊 PROGRESS{Colors.RESET}")
    print(f"  {bar} {pct:.1f}%")
    print(f"  ✅ {done} completed  ❌ {failed} failed  ⏭️  {skipped} skipped  ⏳ {remaining} remaining  /  {total} total")
    print(f"  ⏱️  Elapsed: {hours}h {mins}m {secs}s")
    if done > 0 and elapsed > 0:
        rate = done / (elapsed / 60)
        eta_mins = remaining / rate if rate > 0 else 0
        eta_int = int(eta_mins)
        eta_secs = int((eta_mins - eta_int) * 60)
        print(f"  🚀 Rate: {rate:.1f} videos/min  |  🕐 ETA: {eta_int}m {eta_secs}s")
    
    print()
    print(f"  {Colors.MAGENTA}📸 MEDIA CAPTURED{Colors.RESET}")
    print(f"  🖼️  Screenshots taken: {stats['screenshots_taken']}")
    print(f"  🎬 Clips extracted: {stats['clips_extracted']}")
    print(f"  ⬇️  Download failures: {stats['downloads_failed']}")
    print(f"  🎥 ffmpeg errors: {stats['ffmpeg_errors']}")
    
    if current:
        print()
        print(f"  {Colors.BLUE}▶️  NOW PROCESSING{Colors.RESET}")
        print(f"  {current}")
        if status:
            print(f"  {Colors.DIM}{status}{Colors.RESET}")
    
    print()
    print(f"  {Colors.DIM}Archive: {ARCHIVE_ROOT}{Colors.RESET}")
    print(f"  {Colors.DIM}Vault: {VAULT_ROOT}{Colors.RESET}")
    print(f"  {Colors.DIM}Concurrency: {CONCURRENCY}{Colors.RESET}")
    print(f"  {Colors.DIM}Quality: {QUALITY}{Colors.RESET}")
    print()

def log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    emoji = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "error": "❌", 
             "shot": "🖼️", "clip": "🎬", "dl": "⬇️"}.get(level, "ℹ️")
    entry = f"[{ts}] {emoji} {msg}"
    print(f"  {entry}")

# ═══════════════════════════════════════════════════════════════════
# VIDEO DISCOVERY
# ═══════════════════════════════════════════════════════════════════

def discover_videos_needing_media() -> List[dict]:
    """Find all videos that have transcript but no screenshots."""
    videos = []
    
    sources = [ARCHIVE_ROOT, TEST_DATA_ROOT]
    
    for source in sources:
        if not source.exists():
            continue
        for category_dir in sorted(source.iterdir()):
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            if category.startswith("_") or category.startswith("."):
                continue
            for video_dir in sorted(category_dir.iterdir()):
                if not video_dir.is_dir():
                    continue
                slug = video_dir.name
                
                # Must have metadata + transcript
                metadata_path = video_dir / "metadata.json"
                transcript_path = video_dir / "transcript.md"
                if not metadata_path.exists() or not transcript_path.exists():
                    continue
                
                # Check if screenshots already exist
                ss_dir = video_dir / "screenshots"
                ss_manifest = video_dir / "_screenshots_manifest.json"
                clips_dir = video_dir / "clips"
                clips_manifest = video_dir / "_clips_manifest.json"
                
                has_screenshots = ss_dir.exists() and any(ss_dir.glob("*.jpg"))
                has_clips = clips_dir.exists() and any(clips_dir.glob("*.mp4"))
                
                if has_screenshots and has_clips:
                    stats["skipped"] += 1
                    continue
                
                videos.append({
                    "category": category,
                    "slug": slug,
                    "video_dir": str(video_dir),
                    "metadata_path": str(metadata_path),
                    "transcript_path": str(transcript_path),
                    "screenshots_dir": str(ss_dir),
                    "clips_dir": str(clips_dir),
                    "ss_manifest_path": str(ss_manifest),
                    "clips_manifest_path": str(clips_manifest),
                    "has_screenshots": has_screenshots,
                    "has_clips": has_clips,
                })
    
    return videos

# ═══════════════════════════════════════════════════════════════════
# TRANSCRIPT PARSING (for visual cues)
# ═══════════════════════════════════════════════════════════════════

def parse_transcript_segments(transcript_path: str) -> List[str]:
    """Parse transcript.md into segment strings (timestamp + text)."""
    segments = []
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                # Match lines like "3:24 some text" or "1:02:30 text"
                m = re.match(r'^(\d+:\d+(?::\d+)?)\s+(.+)', line)
                if m:
                    segments.append(line)
    except Exception:
        pass
    return segments

def parse_ts(segment: str) -> Optional[float]:
    """Parse timestamp from a transcript segment."""
    m = re.match(r'^(\d+):(\d+)(?::(\d+))?', segment)
    if not m:
        return None
    if m.group(3):  # H:MM:SS
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return int(m.group(1)) * 60 + int(m.group(2))

def find_visual_cues(segments: List[str], collapse_window: int = 8) -> List[dict]:
    """Find visual-cue moments in transcript segments."""
    cues = []
    for i, seg in enumerate(segments):
        lower = seg.lower()
        for phrase in VISUAL_CUES:
            if phrase in lower:
                ts = parse_ts(seg)
                if ts is None:
                    continue
                if cues and ts - cues[-1]["timestamp"] < collapse_window:
                    break
                # Build context window
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

# ═══════════════════════════════════════════════════════════════════
# PLAYER DATA (chapters + heatmap)
# ═══════════════════════════════════════════════════════════════════

def fetch_player_data(video_id: str) -> dict:
    """Fetch chapters and heatmap via yt-dlp."""
    url = f"https://youtube.com/watch?v={video_id}"
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=60,
        )
        if result.stdout.strip():
            raw = json.loads(result.stdout.strip().split("\n")[0])
            chapters = []
            for ch in raw.get("chapters", []) or []:
                chapters.append({
                    "start_time": ch.get("start_time", 0),
                    "end_time": ch.get("end_time", 0),
                    "title": ch.get("title", ""),
                })
            return {
                "chapters": chapters,
                "has_chapters": len(chapters) > 0,
                "duration": raw.get("duration", 0),
            }
    except Exception as e:
        log(f"Player data fetch failed for {video_id}: {e}", "warn")
    return {"chapters": [], "has_chapters": False, "duration": 0}

def merge_cues_and_chapters(cues: List[dict], chapters: List[dict]) -> List[dict]:
    """Merge visual cues with chapter boundaries for screenshot timestamps."""
    timestamps = set()
    merged = []
    
    # Add visual cues
    for cue in cues:
        if cue["timestamp"] not in timestamps:
            timestamps.add(cue["timestamp"])
            merged.append(cue)
    
    # Add chapter starts
    for ch in chapters:
        ts = ch.get("start_time", 0)
        if ts > 0 and ts not in timestamps:
            timestamps.add(ts)
            merged.append({
                "timestamp": ts,
                "phrase": f"chapter: {ch.get('title', '')}",
                "context": ch.get("title", ""),
                "segment_index": -1,
            })
    
    merged.sort(key=lambda x: x["timestamp"])
    return merged

# ═══════════════════════════════════════════════════════════════════
# VIDEO DOWNLOAD (segments via yt-dlp)
# ═══════════════════════════════════════════════════════════════════

def download_video(url: str, output_path: str) -> bool:
    """Download a video using yt-dlp."""
    cmd = [
        "yt-dlp",
        "-f", f"best[height<={MAX_HEIGHT}]/best",
        "-o", output_path,
        "--no-playlist",
        "--no-warnings",
        url,
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return True
        return False
    except subprocess.TimeoutExpired:
        log(f"Download timed out for {url}", "error")
        return False
    except Exception as e:
        log(f"Download error: {e}", "error")
        return False

def download_segments(url: str, cues: List[dict], video_dir: str) -> Optional[str]:
    """Download only the key segments around cues, stitch into one file for ffmpeg.
    
    Returns path to the downloaded video file, or None.
    """
    # For simplicity, download the full video (yt-dlp is fast for 720p)
    # and let ffmpeg extract screenshots/clips from it
    video_path = os.path.join(video_dir, "_temp_video.mp4")
    
    if os.path.exists(video_path) and os.path.getsize(video_path) > 1000:
        # Already downloaded
        return video_path
    
    log(f"Downloading video...", "dl")
    success = download_video(url, video_path)
    
    if not success:
        stats["downloads_failed"] += 1
        return None
    
    return video_path

# ═══════════════════════════════════════════════════════════════════
# SCREENSHOT + CLIP EXTRACTION
# ═══════════════════════════════════════════════════════════════════

def take_screenshots(video_path: str, cues: List[dict], output_dir: str, offset: int = 3) -> List[dict]:
    """Take a screenshot at each cue timestamp + offset."""
    os.makedirs(output_dir, exist_ok=True)
    results = []
    
    for cue in cues:
        ts = cue["timestamp"] + offset
        fname = f"{int(ts // 60):02d}m{int(ts % 60):02d}s.jpg"
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
            
            if ok:
                stats["screenshots_taken"] += 1
            
            results.append({
                "timestamp": cue["timestamp"],
                "filename": fname,
                "screenshot": f"screenshots/{fname}",
                "ok": ok,
                "context": cue.get("context", ""),
                "phrase": cue.get("phrase", ""),
            })
        except Exception as e:
            stats["ffmpeg_errors"] += 1
            results.append({
                "timestamp": cue["timestamp"],
                "filename": fname,
                "screenshot": f"screenshots/{fname}",
                "ok": False,
                "context": cue.get("context", ""),
                "phrase": cue.get("phrase", ""),
            })
    
    return results

def extract_clips(video_path: str, cues: List[dict], output_dir: str, 
                  max_clips: int = MAX_CLIPS, clip_duration: int = CLIP_DURATION,
                  clip_start_offset: int = CLIP_START_OFFSET) -> List[dict]:
    """Extract short clips for high-value demo moments."""
    os.makedirs(output_dir, exist_ok=True)
    
    # Score cues by keyword matches
    scored = []
    for cue in cues:
        ctx_lower = cue.get("context", "").lower()
        score = sum(1 for kw in CLIP_KEYWORDS if kw in ctx_lower)
        if score > 0:
            scored.append((score, cue))
    scored.sort(key=lambda x: -x[0])
    selected = scored[:max_clips]
    selected.sort(key=lambda x: x[1]["timestamp"])
    
    results = []
    for score, cue in selected:
        start = max(0, cue["timestamp"] + clip_start_offset)
        fname = f"{int(start // 60):02d}m{int(start % 60):02d}s.mp4"
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
            
            if ok:
                stats["clips_extracted"] += 1
            
            results.append({
                "timestamp": cue["timestamp"],
                "filename": fname,
                "clip": f"clips/{fname}",
                "ok": ok,
                "description": cue.get("context", "")[:200],
                "score": score,
            })
        except Exception as e:
            stats["ffmpeg_errors"] += 1
            results.append({
                "timestamp": cue["timestamp"],
                "filename": fname,
                "clip": f"clips/{fname}",
                "ok": False,
                "description": cue.get("context", "")[:200],
                "score": score,
            })
    
    return results

# ═══════════════════════════════════════════════════════════════════
# MANIFEST WRITERS
# ═══════════════════════════════════════════════════════════════════

def write_screenshots_manifest(video: dict, screenshots: List[dict]):
    """Write _screenshots_manifest.json."""
    manifest = {
        "video_id": video.get("video_id", ""),
        "screenshot_count": len([s for s in screenshots if s["ok"]]),
        "screenshots": screenshots,
    }
    with open(video["ss_manifest_path"], "w") as f:
        json.dump(manifest, f, indent=2)

def write_clips_manifest(video: dict, clips: List[dict]):
    """Write _clips_manifest.json."""
    manifest = {
        "video_id": video.get("video_id", ""),
        "clip_count": len([c for c in clips if c["ok"]]),
        "clips": clips,
    }
    with open(video["clips_manifest_path"], "w") as f:
        json.dump(manifest, f, indent=2)

# ═══════════════════════════════════════════════════════════════════
# OBSIDIAN MEDIA INDEX UPDATER
# ═══════════════════════════════════════════════════════════════════

def update_media_index(video: dict, metadata: dict, screenshots: List[dict], clips: List[dict]):
    """Update the media-index.md page in the Obsidian vault."""
    media_path = VAULT_ROOT / "media" / video["category"] / video["slug"] / "media-index.md"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    
    video_dir = video["video_dir"]
    ss_count = len([s for s in screenshots if s["ok"]])
    clip_count = len([c for c in clips if c["ok"]])
    
    ss_md = []
    for ss in screenshots:
        if ss["ok"]:
            full_path = os.path.join(video_dir, "screenshots", ss["filename"])
            ts = ss["timestamp"]
            ts_str = f"{int(ts // 60)}:{int(ts % 60):02d}"
            context = ss.get("context", "")[:100]
            ss_md.append(f"### ⏱ {ts_str}\n- Context: {context}\n- ![{context}]({full_path})\n")
    
    clip_md = []
    for clip in clips:
        if clip["ok"]:
            full_path = os.path.join(video_dir, "clips", clip["filename"])
            ts = clip["timestamp"]
            ts_str = f"{int(ts // 60)}:{int(ts % 60):02d}"
            desc = clip.get("description", clip["filename"])
            clip_md.append(f"### ⏱ {ts_str}\n- {desc}\n- Path: `{full_path}`\n")
    
    content = f"""---
title: "Media Index — {metadata.get('title', video['slug'])}"
type: media-index
video_id: "{metadata.get('video_id', '')}"
category: {video['category']}
screenshot_count: {ss_count}
clip_count: {clip_count}
created: {datetime.now().strftime('%Y-%m-%d')}
updated: {datetime.now().strftime('%Y-%m-%d')}
---

## Screenshots

{chr(10).join(ss_md) if ss_md else 'No screenshots available.'}

## Clips

{chr(10).join(clip_md) if clip_md else 'No clips available.'}
"""
    
    with open(media_path, "w") as f:
        f.write(content)

def update_insight_media_section(video: dict, screenshots: List[dict]):
    """Update the Media section in the insight page to embed top screenshots."""
    insight_path = VAULT_ROOT / "insights" / video["category"] / f"{video['slug']}.md"
    if not insight_path.exists():
        return
    
    with open(insight_path, "r") as f:
        content = f.read()
    
    # Find the ## Media section and replace it
    video_dir = video["video_dir"]
    good_shots = [s for s in screenshots if s["ok"]][:3]
    
    media_lines = []
    media_index_rel = f"media/{video['category']}/{video['slug']}/media-index"
    media_lines.append(f"See [[{media_index_rel}]] for full screenshot and clip gallery.")
    
    for ss in good_shots:
        full_path = os.path.join(video_dir, "screenshots", ss["filename"])
        context = ss.get("context", ss["filename"])[:60]
        media_lines.append(f"\n![{context}]({full_path})")
    
    new_media = "\n".join(media_lines)
    
    # Replace everything after "## Media"
    pattern = r'## Media\n.*'
    replacement = f'## Media\n\n{new_media}\n'
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    with open(insight_path, "w") as f:
        f.write(new_content)

# ═══════════════════════════════════════════════════════════════════
# VIDEO PROCESSOR
# ═══════════════════════════════════════════════════════════════════

async def process_video(video: dict, sem: asyncio.Semaphore):
    """Process a single video: download, take screenshots, extract clips."""
    async with sem:
        slug = video["slug"]
        cat = video["category"]
        display = f"[{cat}] {slug}"
        
        # Read metadata
        try:
            with open(video["metadata_path"]) as f:
                metadata = json.load(f)
        except Exception as e:
            log(f"Failed to read metadata for {slug}: {e}", "error")
            stats["failed"] += 1
            return
        
        video_id = metadata.get("video_id", "")
        url = metadata.get("url", f"https://youtube.com/watch?v={video_id}")
        video["video_id"] = video_id
        
        print_dashboard(display, "Parsing transcript for visual cues...")
        
        # Parse transcript and find visual cues
        segments = parse_transcript_segments(video["transcript_path"])
        if not segments:
            log(f"No segments found in transcript for {slug}", "warn")
            stats["failed"] += 1
            return
        
        cues = find_visual_cues(segments)
        log(f"Found {len(cues)} visual cues in transcript", "info")
        
        # Fetch player data (chapters)
        print_dashboard(display, "Fetching chapters/heatmap...")
        player_data = fetch_player_data(video_id)
        chapters = player_data.get("chapters", [])
        if chapters:
            log(f"Found {len(chapters)} chapters", "info")
        
        # Merge cues with chapter starts
        all_cues = merge_cues_and_chapters(cues, chapters)
        if not all_cues:
            log(f"No cues or chapters found for {slug}", "warn")
            # Use evenly-spaced fallback (every 60s)
            duration = player_data.get("duration", 300)
            for t in range(30, duration, 60):
                all_cues.append({"timestamp": t, "phrase": "interval", "context": "auto-interval", "segment_index": -1})
        
        if not all_cues:
            log(f"Cannot determine screenshot moments for {slug}", "error")
            stats["failed"] += 1
            return
        
        log(f"Total screenshot moments: {len(all_cues)}", "info")
        
        # Download video
        print_dashboard(display, f"Downloading video ({QUALITY})...")
        video_path = download_segments(url, all_cues, video["video_dir"])
        
        if not video_path:
            log(f"Failed to download video for {slug}", "error")
            stats["failed"] += 1
            return
        
        log(f"Video downloaded: {os.path.getsize(video_path) // (1024*1024)}MB", "ok")
        
        # Take screenshots
        print_dashboard(display, f"Taking {len(all_cues)} screenshots...")
        screenshots = take_screenshots(video_path, all_cues, video["screenshots_dir"], SCREENSHOT_OFFSET)
        good_shots = [s for s in screenshots if s["ok"]]
        log(f"Screenshots: {len(good_shots)}/{len(screenshots)} OK", "shot")
        
        # Extract clips
        print_dashboard(display, "Extracting clips...")
        clips = extract_clips(video_path, all_cues, video["clips_dir"])
        good_clips = [c for c in clips if c["ok"]]
        log(f"Clips: {len(good_clips)}/{len(clips)} OK", "clip")
        
        # Write manifests
        write_screenshots_manifest(video, screenshots)
        write_clips_manifest(video, clips)
        
        # Update Obsidian pages
        print_dashboard(display, "Updating Obsidian media index...")
        update_media_index(video, metadata, screenshots, clips)
        update_insight_media_section(video, screenshots)
        
        log(f"✨ Media captured for [{cat}] {slug}: {len(good_shots)} shots, {len(good_clips)} clips", "ok")
        
        # Clean up temp video
        try:
            os.remove(video_path)
        except:
            pass
        
        stats["completed"] += 1
        print_dashboard(display, "✅ Done!")

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

async def main():
    stats["start_time"] = time.time()
    
    dry_run = "--dry-run" in sys.argv
    
    print(f"{Colors.CYAN}{Colors.BOLD}📸 NuxTube Media Backfiller{Colors.RESET}")
    print(f"{Colors.DIM}Scanning for videos needing media...{Colors.RESET}")
    
    videos = discover_videos_needing_media()
    stats["total"] = len(videos) + stats["skipped"]
    
    print(f"\n📊 Found {len(videos)} videos needing media ({stats['skipped']} already have media)")
    
    if dry_run:
        print(f"\n{Colors.YELLOW}DRY RUN — videos that need media capture:{Colors.RESET}")
        for v in videos:
            print(f"  [{v['category']}] {v['slug']}  (screenshots={'NO' if not v['has_screenshots'] else 'YES'}, clips={'NO' if not v['has_clips'] else 'YES'})")
        return
    
    if not videos:
        print(f"{Colors.GREEN}✅ All videos already have media!{Colors.RESET}")
        return
    
    print(f"📦 Total: {stats['total']} videos")
    print(f"⚡ Concurrency: {CONCURRENCY}")
    print(f"🎬 Quality: {QUALITY}")
    print()
    
    sem = asyncio.Semaphore(CONCURRENCY)
    
    # Process in batches to avoid overwhelming the network
    batch_size = CONCURRENCY
    for i in range(0, len(videos), batch_size):
        batch = videos[i:i + batch_size]
        tasks = [process_video(v, sem) for v in batch]
        await asyncio.gather(*tasks)
    
    # Final dashboard
    elapsed = time.time() - stats["start_time"]
    mins, secs = divmod(int(elapsed), 60)
    hours, mins = divmod(mins, 60)
    
    print_dashboard("", "🎉 All done!")
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}🎉 COMPLETE!{Colors.RESET}")
    print(f"  ✅ Completed: {stats['completed']}/{stats['total']}")
    print(f"  ❌ Failed: {stats['failed']}")
    print(f"  ⏭️  Skipped: {stats['skipped']}")
    print(f"  🖼️  Screenshots: {stats['screenshots_taken']}")
    print(f"  🎬 Clips: {stats['clips_extracted']}")
    print(f"  ⬇️  Download failures: {stats['downloads_failed']}")
    print(f"  🎥 ffmpeg errors: {stats['ffmpeg_errors']}")
    print(f"  ⏱️  Time: {hours}h {mins}m {secs}s")

if __name__ == "__main__":
    asyncio.run(main())
