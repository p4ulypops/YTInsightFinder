#!/usr/bin/env python3
"""Transcript fetching with multi-tier SSL fallback.

Tier 1: youtube-transcript-api (Python requests/urllib3)
Tier 2: yt-dlp subtitle extraction
Tier 3: curl direct fetch from YouTube timedtext API (bypasses Python TLS)

This handles the known OpenSSL/urllib3 v2 incompatibility on Python 3.9/macOS
where the system Python is compiled against LibreSSL 2.8.3.
"""
import json
import re
import subprocess
import sys
import tempfile
import os
from typing import Optional, Tuple


def extract_video_id(url: str) -> Optional[str]:
    """Extract 11-char YouTube video ID from any URL format."""
    patterns = [
        r"(?:youtube\.com/watch\?v=)([\w-]{11})",
        r"(?:youtu\.be/)([\w-]{11})",
        r"(?:youtube\.com/embed/)([\w-]{11})",
        r"(?:youtube\.com/shorts/)([\w-]{11})",
        r"(?:youtube\.com/live/)([\w-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # Bare ID
    if re.match(r"^[\w-]{11}$", url):
        return url
    return None


def format_timestamp(seconds: int) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_ts(ts_str: str) -> Optional[int]:
    """Parse a timestamp string to total seconds.

    Handles M:SS, H:MM:SS, and MM:SS formats.
    This fixes the original bug where the regex only matched M:SS,
    breaking on videos >= 1 hour.
    """
    ts_str = ts_str.strip()
    # H:MM:SS or H:MM:SS
    m = re.match(r"^(\d+):(\d{2}):(\d{2})\s+(.*)$", ts_str)
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mn * 60 + s
    # M:SS or MM:SS
    m = re.match(r"^(\d+):(\d{2})\s+(.*)$", ts_str)
    if m:
        mn, s = int(m.group(1)), int(m.group(2))
        return mn * 60 + s
    # Just seconds
    m = re.match(r"^(\d+)\s+(.*)$", ts_str)
    if m:
        return int(m.group(1))
    return None


def fetch_via_transcript_api(video_id: str) -> Optional[dict]:
    """Tier 1: youtube-transcript-api.
    
    Handles both old API (get_transcript) and new API (fetch).
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        
        # Try new API first (fetch), fall back to old (get_transcript)
        try:
            t = YouTubeTranscriptApi().fetch(video_id)
        except TypeError:
            t = YouTubeTranscriptApi.get_transcript(video_id)
        
        if not t:
            return None
        segments = []
        full_text_parts = []
        for seg in t:
            # Handle both object attributes and dict access
            if hasattr(seg, 'start'):
                start = seg.start
                text = seg.text
            elif isinstance(seg, dict):
                start = seg.get("start", 0)
                text = seg.get("text", "")
            else:
                continue
            text = text.strip()
            if not text:
                continue
            ts = format_timestamp(int(start))
            segments.append(f"{ts} {text}")
            full_text_parts.append(text)
        
        if not segments:
            return None
        
        last_end = 0
        if hasattr(t[-1], 'start') and hasattr(t[-1], 'duration'):
            last_end = int(t[-1].start + t[-1].duration)
        elif isinstance(t[-1], dict):
            last_end = int(t[-1].get("start", 0) + t[-1].get("duration", 0))
        
        return {
            "video_id": video_id,
            "segments": segments,
            "full_text": " ".join(full_text_parts),
            "timestamped_text": "\n".join(segments),
            "segment_count": len(segments),
            "duration": format_timestamp(last_end),
            "source": "youtube-transcript-api",
        }
    except Exception as e:
        if "No transcript" in str(e) or "Subtitles are disabled" in str(e):
            raise  # Re-raise known transcript-not-available errors
        return None  # SSL/connection errors -> try fallback


def fetch_via_ytdlp(video_id: str) -> Optional[dict]:
    """Tier 2: yt-dlp subtitle extraction."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmpdir = tempfile.mkdtemp(prefix="nuxtube_subs_")
    try:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--write-auto-sub", "--sub-lang", "en",
            "--skip-download", "--sub-format", "vtt",
            "-o", os.path.join(tmpdir, "sub"),
            "--extractor-args", "youtube:player_client=android",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return None

        vtt_files = [f for f in os.listdir(tmpdir) if f.endswith(".vtt")]
        if not vtt_files:
            return None

        segments = []
        full_text_parts = []
        seen_times = set()

        with open(os.path.join(tmpdir, vtt_files[0]), "r") as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or "-->" not in line:
                i += 1
                continue
            # Parse VTT timestamp line: 00:01:23.456 --> 00:01:26.789
            m = re.match(
                r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})",
                line,
            )
            if not m:
                i += 1
                continue
            h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
            total_sec = h * 3600 + mn * 60 + s

            # Read text lines until blank or next timestamp
            text_lines = []
            i += 1
            while i < len(lines):
                nl = lines[i].strip()
                if not nl or "-->" in nl:
                    break
                text_lines.append(nl)
                i += 1

            text = " ".join(text_lines)
            # Strip VTT inline timing tags: <00:00:01.520><c> code</c>
            text = re.sub(r"<[^>]+>", "", text)
            text = text.strip()

            if not text or total_sec in seen_times:
                continue
            seen_times.add(total_sec)
            ts = format_timestamp(total_sec)
            segments.append(f"{ts} {text}")
            full_text_parts.append(text)

        if not segments:
            return None

        return {
            "video_id": video_id,
            "segments": segments,
            "full_text": " ".join(full_text_parts),
            "timestamped_text": "\n".join(segments),
            "segment_count": len(segments),
            "duration": format_timestamp(total_sec if segments else 0),
            "source": "yt-dlp",
        }
    except Exception:
        return None
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def fetch_via_curl(video_id: str) -> Optional[dict]:
    """Tier 3: Direct curl fetch from YouTube timedtext API.

    This bypasses Python's TLS stack entirely, working around the
    LibreSSL/urllib3 v2 incompatibility on Python 3.9/macOS.
    """
    # First get the video page to extract caption track URLs
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "15", "-L", url],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return None
        html = result.stdout

        # Extract caption track URL from the page data
        # Look for "captionTracks":[...] in the page source
        m = re.search(r'"captionTracks":(\[.*?\])', html)
        if not m:
            return None

        import json as _json
        try:
            tracks = _json.loads(m.group(1))
        except _json.JSONDecodeError:
            return None

        # Find English track (or first available)
        en_track = None
        for track in tracks:
            if track.get("languageCode", "").startswith("en"):
                en_track = track
                break
        if not en_track and tracks:
            en_track = tracks[0]
        if not en_track:
            return None

        caption_url = en_track.get("baseUrl", "")
        if not caption_url:
            return None

        # Fetch the caption data (XML format)
        result = subprocess.run(
            ["curl", "-s", "--max-time", "15", "-L", caption_url],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return None

        xml = result.stdout
        if not xml:
            return None

        # Parse XML caption format: <text start="12.5" dur="2.3">caption text</text>
        segments = []
        full_text_parts = []
        for m in re.finditer(
            r'<text start="([\d.]+)"[^>]*>(.*?)</text>', xml
        ):
            start = float(m.group(1))
            text = m.group(2)
            # Decode HTML entities
            text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            text = text.replace("&#39;", "'").replace("&quot;", '"')
            text = text.strip()
            if not text:
                continue
            ts = format_timestamp(int(start))
            segments.append(f"{ts} {text}")
            full_text_parts.append(text)

        if not segments:
            return None

        return {
            "video_id": video_id,
            "segments": segments,
            "full_text": " ".join(full_text_parts),
            "timestamped_text": "\n".join(segments),
            "segment_count": len(segments),
            "duration": format_timestamp(int(float(m.group(1))) if segments else 0),
            "source": "curl-timedtext",
        }
    except Exception:
        return None


def fetch_transcript(url_or_id: str) -> Optional[dict]:
    """Fetch transcript using 3-tier fallback strategy.

    Tries youtube-transcript-api → yt-dlp → curl (bypasses Python TLS).
    Returns dict with segments, full_text, timestamped_text, etc.
    """
    video_id = extract_video_id(url_or_id)
    if not video_id:
        return None

    # Tier 1: youtube-transcript-api
    try:
        result = fetch_via_transcript_api(video_id)
        if result:
            return result
    except Exception:
        pass  # Fall through to yt-dlp

    # Tier 2: yt-dlp
    result = fetch_via_ytdlp(video_id)
    if result:
        return result

    # Tier 3: curl (bypasses Python TLS entirely)
    result = fetch_via_curl(video_id)
    if result:
        return result

    return None


def fetch_oembed(url: str) -> dict:
    """Fetch video metadata via YouTube oEmbed API."""
    import subprocess
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "10",
             f"https://www.youtube.com/oembed?url={url}&format=json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout:
            import json
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def slugify(s: str) -> str:
    """Convert title to URL-safe slug."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")[:80]
