#!/usr/bin/env python3
"""Playlist and channel monitoring.

Watches YouTube playlists and channels for new videos.
Supports multiple sources (playlists, channels, individual videos).

Channel watching includes a disclaimer system that warns about:
- VPS/computer overload from mass downloading
- Bandwidth and storage costs
- Potential harm to YouTubers
- Legal/ToS implications
"""
import json
import os
import re
import subprocess
import sys
import time
import threading
from datetime import datetime
from typing import Callable, List, Optional, Tuple
from .config import Source


def extract_playlist_videos(playlist_url: str, timeout: int = 60) -> List[Tuple[str, str]]:
    """Extract video IDs and titles from a playlist.

    Returns list of (video_id, title) tuples.
    Uses --print format (most reliable) with yt-dlp binary,
    falling back to python -m yt_dlp if binary not on PATH.
    """
    # Build command — prefer binary yt-dlp, fall back to python -m yt_dlp
    def _make_cmd(yt_bin: List[str]) -> List[str]:
        return yt_bin + [
            "--flat-playlist",
            "--no-warnings",
            "--print", "%(id)s|||%(title)s",
            playlist_url,
        ]

    for yt_bin in [["yt-dlp"], [sys.executable, "-m", "yt_dlp"]]:
        try:
            result = subprocess.run(
                _make_cmd(yt_bin),
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue

            videos = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if "|||" not in line:
                    continue
                vid, _, title = line.partition("|||")
                vid = vid.strip()[:11]
                title = title.strip()
                if validate_video_id(vid):
                    videos.append((vid, title or vid))

            if videos:
                return videos
        except Exception:
            continue

    return []


def extract_channel_videos(channel_url: str, timeout: int = 90) -> List[Tuple[str, str]]:
    """Extract video IDs from a channel's videos page.

    WARNING: Channels can have hundreds/thousands of videos.
    This should only be used with explicit user consent.
    """
    if not channel_url.endswith("/videos"):
        if channel_url.endswith("/"):
            channel_url = channel_url + "videos"
        else:
            channel_url = channel_url + "/videos"

    return extract_playlist_videos(channel_url, timeout=timeout)


def validate_video_id(vid: str) -> bool:
    """Validate that a string is a proper 11-char YouTube video ID."""
    return bool(re.match(r"^[\w-]{11}$", vid))


class PlaylistWatcher:
    """Monitors playlists and channels for new videos.

    Runs check_for_new() periodically and calls on_new_videos callback
    when new videos are detected.

    Can be used standalone or driven by the TUI.
    """

    def __init__(self, sources: List[Source], poll_interval: int = 300,
                 on_new_videos: Callable = None, on_log: Callable = None):
        self.sources = sources
        self.poll_interval = poll_interval
        self.on_new_videos = on_new_videos
        self.on_log = on_log
        self._archived_ids: set = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_check: Optional[str] = None
        self.next_check: Optional[str] = None
        self.total_archived: int = 0
        self.check_count: int = 0
        self._paused = False

    def log(self, level: str, msg: str):
        if self.on_log:
            self.on_log(level, msg)

    def set_archived_ids(self, ids: set):
        """Set the set of already-archived video IDs."""
        self._archived_ids = ids

    def add_archived(self, video_id: str):
        """Mark a video as archived."""
        self._archived_ids.add(video_id)

    def check_for_new(self) -> List[Tuple[str, str, str]]:
        """Check all sources for new videos.

        Returns list of (video_id, title, source_url) tuples.
        """
        new_videos = []
        self.check_count += 1
        self.log("info", f"Checking {len(self.sources)} source(s) for new videos...")

        for source in self.sources:
            if not source.enabled:
                continue

            try:
                if source.type == "playlist":
                    videos = extract_playlist_videos(source.url)
                elif source.type == "channel":
                    videos = extract_channel_videos(source.url)
                elif source.type == "video":
                    from .transcript import extract_video_id
                    vid = extract_video_id(source.url)
                    if vid and vid not in self._archived_ids:
                        new_videos.append((vid, source.name, source.url))
                    continue
                else:
                    continue

                self.log("info", f"  {source.name}: {len(videos)} videos found")

                for vid, title in videos:
                    vid = vid[:11] if len(vid) >= 11 else vid
                    if not validate_video_id(vid):
                        continue
                    if vid not in self._archived_ids:
                        new_videos.append((vid, title or "Untitled", source.url))

            except Exception as e:
                self.log("error", f"  {source.name}: {e}")

        self.last_check = datetime.now().strftime("%H:%M:%S")

        if new_videos:
            self.log("ok", f"Found {len(new_videos)} new video(s)!")
            if self.on_new_videos:
                self.on_new_videos(new_videos)
        else:
            self.log("info", "No new videos found")

        return new_videos

    def start(self):
        """Start watching in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the watcher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def pause(self):
        """Pause checking (TUI can resume)."""
        self._paused = True
        self.log("info", "Watcher paused")

    def resume(self):
        """Resume checking."""
        self._paused = False
        self.log("info", "Watcher resumed")

    @property
    def paused(self) -> bool:
        return self._paused

    def _run(self):
        """Background loop."""
        while self._running:
            if not self._paused:
                try:
                    self.check_for_new()
                except Exception as e:
                    self.log("error", f"Watcher error: {e}")

            for _ in range(self.poll_interval):
                if not self._running:
                    return
                time.sleep(1)
                if self._paused:
                    continue


def channel_disclaimer() -> str:
    """Return the channel watching disclaimer text."""
    return """
============================================================
          !!  CHANNEL WATCHING -- READ THIS  !!
============================================================

Watching entire YouTube channels can:

  1. OVERLOAD your computer/VPS quickly
     Channels can have hundreds or thousands of videos.
     Each video downloads ~50-200MB + screenshots + clips.
     A 500-video channel = 25-100GB of storage + bandwidth.

  2. COST A FORTUNE
     Bandwidth costs on a VPS can be significant.
     API-based transcript fetching may hit rate limits or
     get billed for excessive requests.

  3. POTENTIALLY HARM THE YOUTUBER
     Mass-downloading a channel's content can trigger
     YouTube's anti-scraping protections, potentially
     getting the channel's videos restricted or the
     YouTuber's account flagged.

  4. LEGAL / ToS CONCERNS
     Downloading YouTube videos may violate YouTube's
     Terms of Service. Re-distributing copyrighted content
     is illegal. Use this ONLY for:
       - Your own channels
       - Public domain content
       - Content you have explicit permission to archive

  You have been warned. Proceed at your own risk.

============================================================
"""
