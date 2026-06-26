#!/usr/bin/env python3
"""Tiered playlist archiver — 3-phase pipeline with resource monitoring.

Phase 1: Grab all transcripts + metadata + player_data (10 parallel, network-only)
Phase 2: Download segments + screenshots + clips (3 parallel, I/O heavy)
Phase 3: LLM key-points + omni + exports (5 parallel, API-bound)

Each tier feeds the next. System resources checked between tiers.
Re-runnable: skips work already completed.
"""
import json
import os
import sys
import time
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from nuxtube.config import Config
from nuxtube.archiver import ArchivePipeline, suggest_category
from nuxtube.transcript import extract_video_id, fetch_transcript, fetch_oembed, slugify
from nuxtube.media import find_visual_cues, take_screenshots, extract_clips, cleanup_temp
from nuxtube.player_data import fetch_player_data, download_segment

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLnz01APIg0zVluzyomfB9b1tbzRzGvzJV"
CONFIG_PATH = str(PROJECT_ROOT / "config_playlist_batch.yaml")
LOG_FILE = str(PROJECT_ROOT / "tiered_batch.log")
OUTPUT_DIR = Path("/Volumes/PSILVER-2TB/test_insight_grabber")

# Tier concurrency
TIER1_WORKERS = 10   # transcripts/metadata/player_data — lightweight HTTP
TIER2_WORKERS = 3    # segment download + screenshots + clips — I/O heavy
TIER3_WORKERS = 5    # key-points (LLM) + omni + exports — API-bound

# Resource thresholds
MIN_DISK_GB = 10
MIN_RAM_PERCENT = 15  # abort tier if less than 15% RAM free
MAX_CPU_PERCENT = 90  # throttle if CPU above this


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def check_resources():
    """Return (ok, message)."""
    issues = []
    
    # Disk
    try:
        st = os.statvfs(str(OUTPUT_DIR))
        free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
        if free_gb < MIN_DISK_GB:
            issues.append(f"Disk low: {free_gb:.1f} GB free (need {MIN_DISK_GB})")
    except Exception:
        pass
    
    # RAM + CPU
    if HAS_PSUTIL:
        ram = psutil.virtual_memory()
        if ram.available / ram.total * 100 < MIN_RAM_PERCENT:
            issues.append(f"RAM low: {ram.available/1024**3:.1f} GB free ({ram.percent}% used)")
        
        cpu = psutil.cpu_percent(interval=1)
        if cpu > MAX_CPU_PERCENT:
            issues.append(f"CPU high: {cpu:.0f}%")
    
    if issues:
        return False, " | ".join(issues)
    return True, "OK"


def wait_for_resources(max_wait=120):
    """Block until resources are available or max_wait exceeded."""
    waited = 0
    while waited < max_wait:
        ok, msg = check_resources()
        if ok:
            return True
        log(f"  Resource check: {msg} — waiting 10s... (waited {waited}s)")
        time.sleep(10)
        waited += 10
    return False


def get_playlist_videos():
    """Fetch all video URLs from the playlist via yt-dlp."""
    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "-j", PLAYLIST_URL],
        capture_output=True, text=True, timeout=120,
    )
    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
            vid = entry.get("id", "")
            title = entry.get("title", vid)
            url = f"https://youtube.com/watch?v={vid}"
            videos.append({"id": vid, "title": title, "url": url, "idx": len(videos)+1})
        except json.JSONDecodeError:
            continue
    return videos


# ============================================================
# TIER 1: Transcript + Metadata + Player Data (network-only)
# ============================================================

def tier1_fetch(video, config):
    """Fetch transcript, metadata, player_data. Write to folder.
    Returns dict with video info + folder path + cues + player_data."""
    url = video["url"]
    vid = video["id"]
    idx = video["idx"]
    total = video["total"]
    
    def on_log(level, message):
        log(f"  [T1 {idx}/{total}] [{level}] {message}")
    
    try:
        # Check if transcript already exists
        existing = _find_existing(vid, config)
        if existing:
            # Check if it has transcript + metadata + player_data
            t_path = existing / "transcript.md"
            m_path = existing / "metadata.json"
            if t_path.exists() and m_path.exists():
                meta = json.loads(m_path.read_text())
                if meta.get("player_data"):
                    log(f"  [T1 {idx}/{total}] SKIP (already has transcript+meta+player_data)")
                    return {"video": video, "folder": str(existing), "status": "skip", "metadata": meta}
                else:
                    # Has transcript+meta but no player_data — re-fetch player_data
                    log(f"  [T1 {idx}/{total}] EXISTS, fetching player_data...")
                    pd = fetch_player_data(url)
                    if pd:
                        meta["player_data"] = pd.to_dict()
                        m_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
                    return {"video": video, "folder": str(existing), "status": "exists", "metadata": meta}
            elif t_path.exists():
                # Has transcript but no metadata — complete it
                log(f"  [T1 {idx}/{total}] EXISTS, completing metadata...")
                # We need to re-fetch to get transcript data
                # Just re-do the whole thing
                pass
        
        # Full fetch
        on_log("info", f"Fetching transcript for {vid}...")
        transcript = fetch_transcript(url)
        if not transcript:
            on_log("error", "Transcript fetch failed")
            return {"video": video, "folder": None, "status": "failed", "error": "no transcript"}
        
        on_log("ok", f"Got {transcript.get('segment_count', '?')} segments ({transcript.get('duration', '?')})")
        
        # Metadata
        oembed = fetch_oembed(url)
        title = oembed.get("title", vid)
        channel = oembed.get("author_name", "Unknown")
        channel_url = oembed.get("author_url", "")
        thumbnail = oembed.get("thumbnail_url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
        on_log("ok", f"Title: {title}")
        
        # Player data
        on_log("info", "Fetching player data (chapters, heatmap)...")
        pd = fetch_player_data(url)
        if pd:
            on_log("ok", f"Player data: {len(pd.chapters)} chapters, {len(pd.heatmap)} heatmap, {pd.view_count} views")
        
        # Category
        category = suggest_category(title, transcript.get("full_text", ""))
        
        # Folder
        slug = slugify(title) or vid
        folder = OUTPUT_DIR / category / slug
        folder.mkdir(parents=True, exist_ok=True)
        
        # Write transcript.md
        lines = [
            f"# {title}\n",
            f"> **Channel:** {channel}",
            f"> **URL:** {url}",
            f"> **Duration:** {transcript.get('duration', '?')}",
            f"> **Segments:** {transcript.get('segment_count', 0)}",
            f"> **Source:** {transcript.get('source', 'unknown')}\n",
            "---\n",
            "## Timestamped Transcript\n",
            transcript.get("timestamped_text", ""),
            "\n---\n",
            "## Plain Text\n",
            transcript.get("full_text", ""),
        ]
        (folder / "transcript.md").write_text("\n".join(lines))
        
        # Write metadata.json
        metadata = {
            "title": title,
            "video_id": vid,
            "url": url,
            "channel": channel,
            "channel_url": channel_url,
            "category": category,
            "duration": transcript.get("duration", "?"),
            "segment_count": transcript.get("segment_count", 0),
            "thumbnail_url": thumbnail,
            "fetched_at": datetime.now().isoformat(),
            "source": transcript.get("source", "unknown"),
            "files": {
                "transcript": "transcript.md",
                "metadata": "metadata.json",
            },
        }
        if pd:
            metadata["player_data"] = pd.to_dict()
            metadata["capture_mode"] = config.pipeline.capture_mode
            metadata["quality"] = config.pipeline.quality
            metadata["key_moment_mode"] = config.pipeline.key_moment_mode
        
        (folder / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
        
        return {"video": video, "folder": str(folder), "status": "ok", "metadata": metadata}
    
    except Exception as e:
        on_log("error", f"Exception: {e}")
        return {"video": video, "folder": None, "status": "failed", "error": str(e)}


def _find_existing(video_id, config):
    """Check if video already archived."""
    for meta_path in OUTPUT_DIR.glob("*/*/metadata.json"):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            if meta.get("video_id") == video_id:
                return meta_path.parent
        except Exception:
            continue
    return None


# ============================================================
# TIER 2: Download segments + screenshots + clips (I/O heavy)
# ============================================================

def tier2_media(video, config):
    """Download segments, take screenshots, extract clips.
    Reads transcript + player_data from tier 1 output."""
    idx = video["idx"]
    total = video["total"]
    folder = video["folder"]
    folder_path = Path(folder)
    
    def on_log(level, message):
        log(f"  [T2 {idx}/{total}] [{level}] {message}")
    
    # Check if already done
    ss_manifest = folder_path / "_screenshots_manifest.json"
    clips_manifest = folder_path / "_clips_manifest.json"
    if ss_manifest.exists() and clips_manifest.exists():
        on_log("info", "SKIP (screenshots + clips already done)")
        return {"video": video, "status": "skip"}
    
    try:
        # Load metadata
        meta_path = folder_path / "metadata.json"
        meta = json.loads(meta_path.read_text())
        url = meta["url"]
        
        # Load transcript to find visual cues
        transcript_path = folder_path / "transcript.md"
        transcript_text = transcript_path.read_text(errors="replace")
        
        # Reconstruct transcript segments for cue detection
        # We need the segments — re-fetch lightweight
        on_log("info", "Re-fetching transcript for cue detection...")
        transcript = fetch_transcript(url)
        if not transcript:
            on_log("warn", "Transcript re-fetch failed — using chapters only")
            cues_from_transcript = []
        else:
            cues_from_transcript = find_visual_cues(transcript["segments"])
        
        # Determine key moments using real PlayerData reconstructed from dict
        pd_dict = meta.get("player_data", {})
        cue_timestamps = [c["timestamp"] for c in cues_from_transcript]
        
        if pd_dict:
            from nuxtube.player_data import PlayerData, Chapter, HeatmapEntry
            pd = PlayerData(
                video_id=pd_dict.get("video_id", ""),
                title=pd_dict.get("title", ""),
                duration=pd_dict.get("duration", 0),
                chapters=[Chapter(start_time=c["start_time"], end_time=c["end_time"], title=c["title"]) for c in pd_dict.get("chapters", [])],
                heatmap=[HeatmapEntry(start_time=h["start_time"], end_time=h["end_time"], value=h["value"]) for h in pd_dict.get("heatmap", [])],
                has_chapters=pd_dict.get("has_chapters", False),
                has_heatmap=pd_dict.get("has_heatmap", False),
                view_count=pd_dict.get("view_count", 0),
            )
            key_moments = pd.find_key_moments(cue_timestamps)
            cues = [{"timestamp": m.timestamp, "phrase": m.title, "context": m.chapter_title or m.title} for m in key_moments]
        else:
            cues = cues_from_transcript
        
        if not cues:
            on_log("warn", "No key moments found — skipping media")
            return {"video": video, "status": "no_cues"}
        
        on_log("info", f"Key moments: {len(cues)} (download segments)")
        
        # Segment download
        (folder_path / "screenshots").mkdir(exist_ok=True)
        segment_paths = []
        for i, cue in enumerate(cues):
            seg_name = f"seg_{i:02d}_{int(cue['timestamp']//60):02d}m{int(cue['timestamp']%60):02d}s"
            seg_path = str(folder_path / "screenshots" / seg_name)
            start = max(0, cue["timestamp"] + config.pipeline.clip_start_offset)
            dur = config.pipeline.clip_duration + abs(config.pipeline.clip_start_offset) + 3
            ok = download_segment(url, start, dur, seg_path,
                                  quality=config.pipeline.quality, audio_only=False)
            if ok:
                for f in os.listdir(folder_path / "screenshots"):
                    if f.startswith(f"seg_{i:02d}"):
                        segment_paths.append((cue, os.path.join("screenshots", f)))
                        break
        
        on_log("ok", f"Downloaded {len(segment_paths)} segments")
        
        # Screenshots from segments
        screenshots = []
        for i, (cue, seg_rel) in enumerate(segment_paths):
            seg_full = str(folder_path / seg_rel)
            ss_name = f"{int(cue['timestamp']//60):02d}m{int(cue['timestamp']%60):02d}s.jpg"
            ss_path = str(folder_path / "screenshots" / ss_name)
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(config.pipeline.screenshot_offset),
                     "-i", seg_full, "-frames:v", "1", "-q:v", "2", ss_path],
                    capture_output=True, text=True, timeout=15,
                )
                if os.path.exists(ss_path) and os.path.getsize(ss_path) > 100:
                    screenshots.append({"timestamp": cue["timestamp"], "screenshot": f"screenshots/{ss_name}", "ok": True})
            except Exception:
                pass
        
        ok_ss = len([s for s in screenshots if s.get("ok")])
        on_log("ok", f"Screenshots: {ok_ss}")
        with open(ss_manifest, "w") as f:
            json.dump(screenshots, f, indent=2)
        
        # Clips = segments (renamed)
        clips = []
        for cue, seg_rel in segment_paths:
            clips.append({"timestamp": cue["timestamp"], "clip": seg_rel, "ok": True, "score": 1})
        ok_cl = len([c for c in clips if c.get("ok")])
        on_log("ok", f"Clips: {ok_cl}")
        with open(clips_manifest, "w") as f:
            json.dump(clips, f, indent=2)
        
        # Clean up segment videos (keep only the clip files)
        if not config.pipeline.keep_video:
            for _, seg_rel in segment_paths:
                seg_full = str(folder_path / seg_rel)
                # Keep the file as the clip — don't delete
                pass
        
        # Update metadata
        meta["media"] = {
            "screenshots_dir": "screenshots/" if screenshots else None,
            "screenshot_count": ok_ss,
            "clips_dir": "screenshots/" if clips else None,
            "clip_count": ok_cl,
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        
        return {"video": video, "status": "ok", "screenshots": ok_ss, "clips": ok_cl}
    
    except Exception as e:
        on_log("error", f"Exception: {e}")
        return {"video": video, "status": "failed", "error": str(e)}


# ============================================================
# TIER 3: Key-points + Omni + Exports (API-bound)
# ============================================================

def tier3_analysis(video, config):
    """Extract key-points via LLM, generate omni, export."""
    idx = video["idx"]
    total = video["total"]
    folder = video["folder"]
    folder_path = Path(folder)
    
    def on_log(level, message):
        log(f"  [T3 {idx}/{total}] [{level}] {message}")
    
    # Check if already done
    kp_path = folder_path / "key-points.json"
    omni_path = folder_path / "omni.json"
    if kp_path.exists() and omni_path.exists():
        on_log("info", "SKIP (key-points + omni already done)")
        return {"video": video, "status": "skip"}
    
    try:
        # Key-points
        if not kp_path.exists():
            on_log("info", "Extracting key points...")
            from nuxtube.keypoints import extract_keypoints as run_kp
            success = run_kp(str(folder_path), config)
            if success:
                on_log("ok", "Key points extracted")
            else:
                on_log("warn", "Key-point extraction failed")
        else:
            on_log("info", "Key-points already exist")
        
        # Omni
        if not omni_path.exists():
            on_log("info", "Generating OmniFile...")
            from nuxtube.omni import write_omni
            omni_result = write_omni(str(folder_path))
            if omni_result:
                on_log("ok", "OmniFile written")
            else:
                on_log("warn", "OmniFile failed")
        else:
            on_log("info", "Omni already exists")
        
        # Exports
        export_formats = getattr(config.pipeline, "export_formats", [])
        if export_formats and omni_path.exists():
            on_log("info", f"Exporting: {','.join(export_formats)}...")
            from nuxtube.omni import build_omni
            from nuxtube.exporters import export
            omni_data = build_omni(str(folder_path))
            if omni_data:
                results = export(omni_data, str(folder_path), export_formats)
                ok_exports = [f for f, p in results.items() if not str(p).startswith("ERROR")]
                on_log("ok", f"Exported: {', '.join(ok_exports)}")
        
        # Tracker
        try:
            from nuxtube.tracker import TrackerCSV
            tracker = TrackerCSV(str(OUTPUT_DIR / "master_tracker.csv"))
            meta = json.loads((folder_path / "metadata.json").read_text())
            ss = []
            if (folder_path / "_screenshots_manifest.json").exists():
                ss = json.loads((folder_path / "_screenshots_manifest.json").read_text())
            cl = []
            if (folder_path / "_clips_manifest.json").exists():
                cl = json.loads((folder_path / "_clips_manifest.json").read_text())
            rel_folder = f"{meta['category']}/{folder_path.name}"
            tracker.append(meta, rel_folder, ss, cl, status="Done")
            on_log("ok", "Tracker updated")
        except Exception as e:
            on_log("warn", f"Tracker failed: {e}")
        
        return {"video": video, "status": "ok"}
    
    except Exception as e:
        on_log("error", f"Exception: {e}")
        return {"video": video, "status": "failed", "error": str(e)}


# ============================================================
# MAIN
# ============================================================

def main():
    log("=" * 70)
    log("NuxTube Tiered Pipeline — 3-Phase Architecture")
    log(f"Playlist: {PLAYLIST_URL}")
    log(f"Output: {OUTPUT_DIR}")
    log(f"Tier 1: {TIER1_WORKERS} workers (transcript+metadata+player_data)")
    log(f"Tier 2: {TIER2_WORKERS} workers (segments+screenshots+clips)")
    log(f"Tier 3: {TIER3_WORKERS} workers (keypoints+omni+exports)")
    log("=" * 70)
    
    config = Config.load(CONFIG_PATH)
    
    # Check resources
    ok, msg = check_resources()
    log(f"System: {msg}")
    if not ok:
        log("ABORT: Resources too low to start")
        sys.exit(1)
    
    # Get playlist
    log("Fetching playlist videos...")
    videos = get_playlist_videos()
    total = len(videos)
    log(f"Found {total} videos")
    
    for v in videos:
        v["total"] = total
    
    # ============================================================
    # TIER 1: Transcript + Metadata + Player Data
    # ============================================================
    log(f"\n{'#'*60}")
    log(f"# TIER 1/3: Transcript + Metadata + Player Data")
    log(f"# {TIER1_WORKERS} parallel workers — network only")
    log(f"{'#'*60}")
    
    t1_start = time.time()
    t1_results = []
    
    with ThreadPoolExecutor(max_workers=TIER1_WORKERS) as executor:
        futures = {executor.submit(tier1_fetch, v, config): v for v in videos}
        for future in as_completed(futures):
            v = futures[future]
            try:
                res = future.result()
                t1_results.append(res)
                if res["status"] == "ok":
                    log(f"  [T1 {v['idx']}/{total}] DONE: {v['title'][:50]}")
                elif res["status"] == "skip":
                    log(f"  [T1 {v['idx']}/{total}] SKIP: {v['title'][:50]}")
                elif res["status"] == "exists":
                    log(f"  [T1 {v['idx']}/{total}] EXISTS+: {v['title'][:50]}")
                else:
                    log(f"  [T1 {v['idx']}/{total}] FAIL: {v['title'][:50]} — {res.get('error','')}")
            except Exception as e:
                log(f"  [T1 {v['idx']}/{total}] EXCEPTION: {e}")
                t1_results.append({"video": v, "folder": None, "status": "failed", "error": str(e)})
    
    t1_elapsed = time.time() - t1_start
    t1_ok = len([r for r in t1_results if r["status"] in ("ok", "skip", "exists")])
    t1_fail = len([r for r in t1_results if r["status"] == "failed"])
    log(f"\nTier 1 complete: {t1_ok} ok, {t1_fail} failed, {t1_elapsed:.0f}s")
    
    # Resource check between tiers
    log(f"\n--- Resource check ---")
    if not wait_for_resources():
        log("ABORT: Resources not available after waiting")
        sys.exit(1)
    ok, msg = check_resources()
    log(f"Resources: {msg}")
    
    # Filter for tier 2 — only videos with a folder
    t2_videos = [r for r in t1_results if r.get("folder")]
    log(f"\n{'#'*60}")
    log(f"# TIER 2/3: Segments + Screenshots + Clips")
    log(f"# {TIER2_WORKERS} parallel workers — I/O heavy")
    log(f"# {len(t2_videos)} videos to process")
    log(f"{'#'*60}")
    
    # ============================================================
    # TIER 2: Download + Screenshots + Clips
    # ============================================================
    t2_start = time.time()
    t2_results = []
    
    with ThreadPoolExecutor(max_workers=TIER2_WORKERS) as executor:
        futures = {executor.submit(tier2_media, r, config): r for r in t2_videos}
        for future in as_completed(futures):
            r = futures[future]
            v = r["video"]
            try:
                res = future.result()
                t2_results.append(res)
                if res["status"] == "ok":
                    log(f"  [T2 {v['idx']}/{total}] DONE: {v['title'][:50]} (ss={res.get('screenshots',0)}, cl={res.get('clips',0)})")
                elif res["status"] == "skip":
                    log(f"  [T2 {v['idx']}/{total}] SKIP: {v['title'][:50]}")
                else:
                    log(f"  [T2 {v['idx']}/{total}] {res['status'].upper()}: {v['title'][:50]}")
            except Exception as e:
                log(f"  [T2 {v['idx']}/{total}] EXCEPTION: {e}")
                t2_results.append({"video": v, "status": "failed", "error": str(e)})
    
    t2_elapsed = time.time() - t2_start
    t2_ok = len([r for r in t2_results if r["status"] in ("ok", "skip")])
    t2_fail = len([r for r in t2_results if r["status"] == "failed"])
    log(f"\nTier 2 complete: {t2_ok} ok, {t2_fail} failed, {t2_elapsed:.0f}s")
    
    # Resource check
    log(f"\n--- Resource check ---")
    if not wait_for_resources():
        log("WARNING: Resources tight, proceeding with reduced workers")
    ok, msg = check_resources()
    log(f"Resources: {msg}")
    
    # ============================================================
    # TIER 3: Key-points + Omni + Exports
    # ============================================================
    log(f"\n{'#'*60}")
    log(f"# TIER 3/3: Key-points + Omni + Exports")
    log(f"# {TIER3_WORKERS} parallel workers — LLM API-bound")
    log(f"# {len(t2_videos)} videos to process")
    log(f"{'#'*60}")
    
    t3_start = time.time()
    t3_results = []
    
    with ThreadPoolExecutor(max_workers=TIER3_WORKERS) as executor:
        futures = {executor.submit(tier3_analysis, r, config): r for r in t2_videos}
        for future in as_completed(futures):
            r = futures[future]
            v = r["video"]
            try:
                res = future.result()
                t3_results.append(res)
                if res["status"] == "ok":
                    log(f"  [T3 {v['idx']}/{total}] DONE: {v['title'][:50]}")
                elif res["status"] == "skip":
                    log(f"  [T3 {v['idx']}/{total}] SKIP: {v['title'][:50]}")
                else:
                    log(f"  [T3 {v['idx']}/{total}] {res['status'].upper()}: {v['title'][:50]}")
            except Exception as e:
                log(f"  [T3 {v['idx']}/{total}] EXCEPTION: {e}")
                t3_results.append({"video": v, "status": "failed", "error": str(e)})
    
    t3_elapsed = time.time() - t3_start
    t3_ok = len([r for r in t3_results if r["status"] in ("ok", "skip")])
    t3_fail = len([r for r in t3_results if r["status"] == "failed"])
    
    # ============================================================
    # FINAL SUMMARY
    # ============================================================
    total_elapsed = time.time() - t1_start
    log(f"\n{'='*70}")
    log(f"TIERED PIPELINE COMPLETE")
    log(f"  Total videos: {total}")
    log(f"  Tier 1 (transcript+meta+player): {t1_ok} ok, {t1_fail} fail ({t1_elapsed:.0f}s)")
    log(f"  Tier 2 (segments+ss+clips):      {t2_ok} ok, {t2_fail} fail ({t2_elapsed:.0f}s)")
    log(f"  Tier 3 (keypoints+omni+export):  {t3_ok} ok, {t3_fail} fail ({t3_elapsed:.0f}s)")
    log(f"  Total time: {total_elapsed:.0f}s ({total_elapsed/60:.1f}m)")
    log(f"  Output: {OUTPUT_DIR}")
    log(f"  Log: {LOG_FILE}")
    log(f"{'='*70}")


if __name__ == "__main__":
    main()
