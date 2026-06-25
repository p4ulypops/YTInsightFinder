#!/usr/bin/env python3
"""
watch_and_archive.py — Wait for YouTube IP block to lift, then archive all new videos.

Re-extracts the playlist each cycle (user may be updating it).
Notifies via stdout markers at each check.
"""
import subprocess, sys, os, time, json, re, glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_video.py")

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLnz01APIg0zVluzyomfB9b1tbzRzGvzJV"
CHECK_INTERVAL = 300  # 5 minutes
ARCHIVE_DELAY = 20    # seconds between each video
ARCHIVE_TIMEOUT = 600 # 10 min per video

# Videos we already successfully archived (from batch 1)
ALREADY_DONE = set()

def get_archived_vids():
    """Scan the filesystem for already-archived video IDs."""
    vids = set()
    for meta_path in glob.glob(os.path.join(BASE, "*", "*", "metadata.json")):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
                vid = meta.get("video_id", "")
                if vid and len(vid) == 11:
                    vids.add(vid)
        except Exception:
            pass
    return vids

def test_block():
    """Test if YouTube transcript API is unblocked."""
    try:
        r = subprocess.run(
            ["python3", "-c",
             "from youtube_transcript_api import YouTubeTranscriptApi; api=YouTubeTranscriptApi(); list(api.fetch('dQw4w9WgXcQ'))"],
            capture_output=True, text=True, timeout=15
        )
        # Check stderr for block message
        if "blocking" in r.stderr.lower() or "Too Many Requests" in r.stderr or "429" in r.stderr:
            return False
        if r.returncode == 0:
            return True
        # If it's a different error (like no transcript for Rick Astley), the API is working
        if "Could not retrieve" in r.stderr and "blocking" not in r.stderr.lower():
            return True  # API works, just no transcript for that video
        return False
    except:
        return False

def extract_playlist():
    """Re-extract the playlist via yt-dlp."""
    r = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--print", "%(id)s|%(title)s", PLAYLIST_URL],
        capture_output=True, text=True, timeout=90
    )
    if r.returncode != 0:
        return []
    videos = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("|", 1)
        vid = parts[0].strip()
        title = parts[1].strip() if len(parts) > 1 else ""
        if vid and len(vid) >= 8:
            videos.append((vid, title))
    return videos

def archive_one(vid, title, idx, total):
    """Archive a single video."""
    url = f"https://www.youtube.com/watch?v={vid}"
    print(f"  [{idx}/{total}] {vid}: {title[:60]}", flush=True)
    try:
        r = subprocess.run(
            ["python3", SCRIPT, url, "--csv-append"],
            capture_output=True, text=True, timeout=ARCHIVE_TIMEOUT, cwd=BASE
        )
        if r.returncode == 0:
            print(f"  [{idx}/{total}] DONE {vid}", flush=True)
            return True
        else:
            err = r.stderr.strip().splitlines()[-1] if r.stderr else "unknown"
            print(f"  [{idx}/{total}] FAIL {vid}: {err[:120]}", flush=True)
            return False
    except subprocess.TimeoutExpired:
        print(f"  [{idx}/{total}] TIMEOUT {vid}", flush=True)
        return False
    except Exception as e:
        print(f"  [{idx}/{total}] ERROR {vid}: {e}", flush=True)
        return False

def main():
    print("=== WATCH AND ARCHIVE: waiting for YouTube block to lift ===", flush=True)
    print(f"    Checking every {CHECK_INTERVAL}s, {ARCHIVE_DELAY}s between videos", flush=True)
    print(f"    Re-extracting playlist each cycle", flush=True)
    
    cycle = 0
    while True:
        cycle += 1
        print(f"\n>>> CHECK #{cycle} ({time.strftime('%H:%M:%S')})", flush=True)
        
        if test_block():
            print("BLOCK_LIFTED: YouTube transcript API is responding!", flush=True)
            break
        else:
            print(f"STILL_BLOCKED: YouTube still rate-limiting (check #{cycle})", flush=True)
            time.sleep(CHECK_INTERVAL)
    
    # Block lifted — re-extract playlist
    print("\n>>> RE-EXTRACTING PLAYLIST (latest version)...", flush=True)
    playlist = extract_playlist()
    print(f"    Found {len(playlist)} videos in playlist", flush=True)
    
    # Check which are already archived
    archived = get_archived_vids()
    print(f"    {len(archived)} already archived on disk", flush=True)
    
    # Find new ones to archive
    todo = [(vid, title) for vid, title in playlist if vid not in archived]
    print(f"    {len(todo)} new videos to archive", flush=True)
    
    if not todo:
        print("BATCH_COMPLETE: nothing to do — all videos already archived!", flush=True)
        return
    
    # Archive sequentially
    print(f"\n>>> STARTING SEQUENTIAL ARCHIVE: {len(todo)} videos", flush=True)
    succeeded = 0
    failed = 0
    
    for i, (vid, title) in enumerate(todo, 1):
        ok = archive_one(vid, title, i, len(todo))
        if ok:
            succeeded += 1
        else:
            failed += 1
        
        if i < len(todo):
            print(f"    waiting {ARCHIVE_DELAY}s...", flush=True)
            time.sleep(ARCHIVE_DELAY)
        
        # Re-check block status mid-batch
        if i % 5 == 0 and not test_block():
            print(f"    WARNING: block re-triggered at video {i}, pausing 5 min...", flush=True)
            time.sleep(300)
            if not test_block():
                print(f"    Still blocked after pause. Stopping batch.", flush=True)
                break
            print(f"    Block lifted, resuming...", flush=True)
    
    print(f"\nBATCH_COMPLETE: {len(todo)} processed, {succeeded} succeeded, {failed} failed", flush=True)
    print(f"  Total archived on disk: {len(get_archived_vids())}", flush=True)


if __name__ == "__main__":
    main()
