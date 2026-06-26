#!/usr/bin/env python3
"""Batch playlist archiver — processes videos 3 at a time with all features.

Usage:
    python3 batch_playlist.py

Reads the playlist, processes videos in groups of 3 (parallel), waits for
each group to finish, then moves to the next group. All output goes to
/Volumes/PSILVER-2TB/YoutubeInsights.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from nuxtube.config import Config
from nuxtube.archiver import ArchivePipeline

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLnz01APIg0zVluzyomfB9b1tbzRzGvzJV"
CONFIG_PATH = str(PROJECT_ROOT / "config_playlist_batch.yaml")
BATCH_SIZE = 3
LOG_FILE = str(PROJECT_ROOT / "batch_playlist.log")


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_playlist_videos():
    """Fetch all video URLs from the playlist."""
    from nuxtube.transcript import extract_video_id
    import subprocess

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
            videos.append({"id": vid, "title": title, "url": url})
        except json.JSONDecodeError:
            continue

    return videos


def archive_one(pipeline, video, idx, total):
    """Archive a single video with logging."""
    url = video["url"]
    title = video["title"]

    def on_log(level, message):
        log(f"  [{idx}/{total}] [{level}] {message}")

    def on_progress(stage, cur, total_stages, msg):
        pass  # Keep terminal clean

    try:
        result = pipeline.archive(url, on_log=on_log, on_progress=on_progress)
        return {"video": video, "result": result, "idx": idx}
    except Exception as e:
        log(f"  [{idx}/{total}] [error] Exception: {e}")
        return {"video": video, "result": None, "idx": idx}


def main():
    log("=" * 70)
    log("NuxTube Batch Playlist Archiver")
    log(f"Playlist: {PLAYLIST_URL}")
    log(f"Output: /Volumes/PSILVER-2TB/YoutubeInsights")
    log(f"Batch size: {BATCH_SIZE} (parallel)")
    log(f"All stages: transcript, metadata, player_data, download, screenshots, clips, keypoints, tracker, omni, export")
    log("=" * 70)

    # Check disk space
    stat = os.statvfs("/Volumes/PSILVER-2TB")
    free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
    log(f"Disk space: {free_gb:.1f} GB free on /Volumes/PSILVER-2TB")

    if free_gb < 10:
        log("ERROR: Less than 10GB free on output drive. Aborting.")
        sys.exit(1)

    # Load config
    config = Config.load(CONFIG_PATH)
    pipeline = ArchivePipeline(config)

    # Get playlist videos
    log("Fetching playlist videos...")
    videos = get_playlist_videos()
    log(f"Found {len(videos)} videos in playlist")

    if not videos:
        log("ERROR: No videos found. Check playlist URL.")
        sys.exit(1)

    # Process in batches of 3
    total = len(videos)
    completed = 0
    succeeded = 0
    failed = 0
    skipped = 0
    partial = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = videos[batch_start:batch_start + BATCH_SIZE]
        batch_num = (batch_start // BATCH_SIZE) + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        log(f"\n{'='*50}")
        log(f"BATCH {batch_num}/{total_batches} — Videos {batch_start+1}-{batch_start+len(batch)}/{total}")
        for i, v in enumerate(batch):
            log(f"  {batch_start+i+1}. {v['title'][:70]}")
        log(f"{'='*50}")

        batch_start_time = time.time()

        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            futures = {}
            for i, video in enumerate(batch):
                idx = batch_start + i + 1
                future = executor.submit(archive_one, pipeline, video, idx, total)
                futures[future] = video

            for future in as_completed(futures):
                video = futures[future]
                try:
                    res = future.result()
                    result = res["result"]
                    idx = res["idx"]
                    if result is None:
                        failed += 1
                        log(f"  [{idx}/{total}] FAILED: {video['title'][:60]}")
                    elif result.status == "success":
                        succeeded += 1
                        log(f"  [{idx}/{total}] SUCCESS: {video['title'][:60]} ({len(result.stages_completed)} stages, {result.screenshot_count} ss, {result.clip_count} clips)")
                    elif result.status == "skipped":
                        skipped += 1
                        log(f"  [{idx}/{total}] SKIPPED (already archived): {video['title'][:60]}")
                    elif result.status == "partial":
                        partial += 1
                        log(f"  [{idx}/{total}] PARTIAL: {video['title'][:60]} ({len(result.stages_completed)} stages, errors: {result.errors})")
                    else:
                        failed += 1
                        log(f"  [{idx}/{total}] FAILED: {video['title'][:60]} — {result.errors}")
                except Exception as e:
                    failed += 1
                    log(f"  EXCEPTION processing {video['title'][:60]}: {e}")

                completed += 1
                log(f"  Progress: {completed}/{total} done | S={succeeded} P={partial} S={skipped} F={failed}")

        batch_elapsed = time.time() - batch_start_time
        log(f"Batch {batch_num} done in {batch_elapsed:.0f}s")

    # Final summary
    log(f"\n{'='*70}")
    log(f"BATCH COMPLETE — {total} videos processed")
    log(f"  Succeeded: {succeeded}")
    log(f"  Partial:   {partial}")
    log(f"  Skipped:   {skipped}")
    log(f"  Failed:    {failed}")
    log(f"Output: /Volumes/PSILVER-2TB/YoutubeInsights")
    log(f"Log: {LOG_FILE}")
    log(f"{'='*70}")


if __name__ == "__main__":
    main()
