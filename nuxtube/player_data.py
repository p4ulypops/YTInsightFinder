#!/usr/bin/env python3
"""YouTube player data extraction — chapters, heatmap, formats.

Uses yt-dlp to extract rich metadata that YouTube shows in the player:
  - Chapters: structured segment titles with start/end timestamps
  - Heatmap: viewer engagement data (most-replayed moments)
  - Formats: available audio/video quality options

This lets us capture key moments WITHOUT downloading the entire video:
  - Use heatmap peaks to find the most interesting moments
  - Use chapter boundaries to get structured screenshots
  - Use transcript + heatmap to decide which clips to extract
  - Download only the specific time ranges we need (instead of whole video)

The heatmap data looks like:
  [{'start_time': 0.0, 'end_time': 8.86, 'value': 0.17}, ...]
  value is 0.0 to 1.0 — represents relative viewer engagement

Player data sources:
  - Chapters: creator-defined segments (shown in player progress bar)
  - Heatmap: YouTube's "most replayed" graph (shown as peaks on progress bar)
  - These are the same data points the user sees in the YouTube player UI
"""
import json
import os
import subprocess
import sys
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class Chapter:
    """A YouTube chapter segment."""
    start_time: float
    end_time: float
    title: str

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        return {"start_time": self.start_time, "end_time": self.end_time, "title": self.title}


@dataclass
class HeatmapEntry:
    """A single heatmap data point (viewer engagement at a time range)."""
    start_time: float
    end_time: float
    value: float  # 0.0 to 1.0

    def to_dict(self) -> dict:
        return {"start_time": self.start_time, "end_time": self.end_time, "value": self.value}


@dataclass
class KeyMoment:
    """A identified key moment in the video — for screenshots/clips.

    Sources: chapter boundaries, heatmap peaks, visual cues from transcript.
    """
    timestamp: float
    title: str
    source: str  # "chapter" | "heatmap" | "visual_cue" | "combined"
    heat: float = 0.0  # heatmap value at this moment (0.0-1.0)
    chapter_title: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "title": self.title,
            "source": self.source,
            "heat": self.heat,
            "chapter_title": self.chapter_title,
        }


@dataclass
class PlayerData:
    """All data extracted from the YouTube player."""
    video_id: str = ""
    title: str = ""
    duration: int = 0
    chapters: List[Chapter] = field(default_factory=list)
    heatmap: List[HeatmapEntry] = field(default_factory=list)
    has_chapters: bool = False
    has_heatmap: bool = False
    view_count: int = 0
    like_count: int = 0
    upload_date: str = ""
    availability: str = ""
    categories: List[str] = field(default_factory=list)
    audio_formats: List[dict] = field(default_factory=list)
    video_formats: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "duration": self.duration,
            "has_chapters": self.has_chapters,
            "has_heatmap": self.has_heatmap,
            "chapter_count": len(self.chapters),
            "heatmap_count": len(self.heatmap),
            "chapters": [c.to_dict() for c in self.chapters],
            "heatmap": [h.to_dict() for h in self.heatmap],
            "view_count": self.view_count,
            "like_count": self.like_count,
            "upload_date": self.upload_date,
            "availability": self.availability,
            "categories": self.categories,
        }

    def top_heatmap_moments(self, count: int = 10) -> List[HeatmapEntry]:
        """Return the top N most-replayed moments from the heatmap."""
        if not self.heatmap:
            return []
        return sorted(self.heatmap, key=lambda h: h.value, reverse=True)[:count]

    def find_key_moments(self, visual_cue_timestamps: List[float] = None,
                         max_moments: int = 20) -> List[KeyMoment]:
        """Identify key moments by combining chapters + heatmap + visual cues.

        This is the smart replacement for blindly taking screenshots at every
        "as you can see" phrase. Instead we:

        1. Start with chapter boundaries (creator-curated structure)
        2. Add heatmap peaks (viewer-validated interesting moments)
        3. Add visual-cue timestamps (transcript-detected demo moments)
        4. Score and rank by heat value
        5. Deduplicate (collapse moments within 10 seconds)
        6. Return top N

        Args:
            visual_cue_timestamps: Timestamps from transcript visual-cue detection
            max_moments: Maximum number of key moments to return

        Returns:
            List of KeyMoment objects sorted by timestamp
        """
        moments: Dict[float, KeyMoment] = {}  # timestamp -> KeyMoment

        # 1. Chapter boundaries
        for ch in self.chapters:
            t = ch.start_time
            if t not in moments:
                moments[t] = KeyMoment(
                    timestamp=t, title=ch.title,
                    source="chapter", chapter_title=ch.title,
                )
            else:
                moments[t].title = ch.title
                moments[t].chapter_title = ch.title

        # 2. Heatmap peaks (top moments)
        for h in self.top_heatmap_moments(count=15):
            t = h.start_time
            # Find nearest existing moment within 10 seconds
            nearest = min(moments.keys(), key=lambda k: abs(k - t)) if moments else None
            if nearest is not None and abs(nearest - t) < 10:
                # Merge into existing
                moments[nearest].heat = max(moments[nearest].heat, h.value)
                if moments[nearest].source == "chapter":
                    moments[nearest].source = "combined"
                elif moments[nearest].source == "visual_cue":
                    moments[nearest].source = "combined"
            else:
                # New heatmap moment
                if t not in moments:
                    moments[t] = KeyMoment(
                        timestamp=t, title=f"Heatmap peak ({h.value:.0%})",
                        source="heatmap", heat=h.value,
                    )

        # 3. Visual-cue timestamps
        if visual_cue_timestamps:
            for t in visual_cue_timestamps:
                nearest = min(moments.keys(), key=lambda k: abs(k - t)) if moments else None
                if nearest is not None and abs(nearest - t) < 10:
                    # Merge — boost this moment's importance
                    if moments[nearest].source == "chapter":
                        moments[nearest].source = "combined"
                    elif moments[nearest].source == "heatmap":
                        moments[nearest].source = "combined"
                else:
                    if t not in moments:
                        moments[t] = KeyMoment(
                            timestamp=t, title="Visual cue",
                            source="visual_cue",
                        )

        # 4. Find chapter for each moment
        for t, m in moments.items():
            if not m.chapter_title:
                for ch in self.chapters:
                    if ch.start_time <= t < ch.end_time:
                        m.chapter_title = ch.title
                        break

        # 5. Sort by timestamp, collapse within 10s, take top max_moments
        all_moments = sorted(moments.values(), key=lambda m: m.timestamp)
        collapsed: List[KeyMoment] = []
        for m in all_moments:
            if collapsed and m.timestamp - collapsed[-1].timestamp < 10:
                # Merge: keep the one with higher heat
                if m.heat > collapsed[-1].heat:
                    collapsed[-1] = m
            else:
                collapsed.append(m)

        # 6. Rank by combined score (heat + source priority) and take top N
        source_score = {"combined": 3, "heatmap": 2, "chapter": 1, "visual_cue": 1}
        collapsed.sort(key=lambda m: (m.heat + source_score.get(m.source, 0)), reverse=True)
        top = collapsed[:max_moments]

        # Return sorted by timestamp
        top.sort(key=lambda m: m.timestamp)
        return top


def fetch_player_data(url_or_id: str, timeout: int = 30) -> Optional[PlayerData]:
    """Extract player data (chapters, heatmap, formats) via yt-dlp.

    Args:
        url_or_id: YouTube URL or video ID
        timeout: Network timeout in seconds

    Returns:
        PlayerData object or None on failure
    """
    from .transcript import extract_video_id

    video_id = extract_video_id(url_or_id)
    if not video_id:
        return None

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None

    pd = PlayerData(video_id=video_id)
    pd.title = info.get("title", "")
    pd.duration = info.get("duration", 0) or 0
    pd.view_count = info.get("view_count", 0) or 0
    pd.like_count = info.get("like_count", 0) or 0
    pd.upload_date = info.get("upload_date", "")
    pd.availability = info.get("availability", "")
    pd.categories = info.get("categories", []) or []

    # Chapters
    raw_chapters = info.get("chapters")
    if raw_chapters:
        pd.chapters = [
            Chapter(start_time=ch["start_time"], end_time=ch["end_time"], title=ch.get("title", ""))
            for ch in raw_chapters
        ]
        pd.has_chapters = True

    # Heatmap
    raw_heatmap = info.get("heatmap")
    if raw_heatmap:
        pd.heatmap = [
            HeatmapEntry(start_time=h["start_time"], end_time=h["end_time"], value=h["value"])
            for h in raw_heatmap
        ]
        pd.has_heatmap = True

    # Formats
    formats = info.get("formats", []) or []
    for f in formats:
        fmt = {
            "format_id": f.get("format_id", ""),
            "ext": f.get("ext", ""),
            "height": f.get("height"),
            "width": f.get("width"),
            "vcodec": f.get("vcodec", "none"),
            "acodec": f.get("acodec", "none"),
            "abr": f.get("abr"),
            "tbr": f.get("tbr"),
        }
        if fmt["vcodec"] == "none" and fmt["acodec"] != "none":
            pd.audio_formats.append(fmt)
        elif fmt["vcodec"] != "none":
            pd.video_formats.append(fmt)

    return pd


def download_segment(url: str, start: float, duration: float,
                     output_path: str, quality: str = "720p",
                     audio_only: bool = False, timeout: int = 120) -> bool:
    """Download a specific time segment of a video.

    Uses yt-dlp's --download-sections to fetch only the needed part,
    skipping the rest of the video entirely.

    Args:
        url: YouTube video URL
        start: Start time in seconds
        duration: Duration in seconds
        output_path: Where to save the file
        quality: "720p", "1080p", "480p", "best", "audio"
        audio_only: If True, download audio only (MP3)
        timeout: Network timeout

    Returns:
        True on success
    """
    import tempfile

    height_map = {"480p": "480", "720p": "720", "1080p": "1080", "best": "best"}

    if audio_only:
        format_spec = "bestaudio"
        ext = "mp3"
        post_args = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        h = height_map.get(quality, "720")
        format_spec = f"best[height<={h}]/best" if h != "best" else "best"
        ext = "mp4"
        post_args = ["--merge-output-format", "mp4"]

    # Use --download-sections for time-range download
    end = start + duration
    section = f"*{start:.1f}-{end:.1f}"

    tmpdir = tempfile.mkdtemp(prefix="nuxtube_seg_")
    output_template = os.path.join(tmpdir, f"segment.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", format_spec,
        "--download-sections", section,
        "--force-keyframes-at-cuts",
        "-o", output_template,
        "--extractor-args", "youtube:player_client=android",
        "--no-warnings",
    ] + post_args + [url]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return False

        # Find the output file
        for f in os.listdir(tmpdir):
            if f.startswith("segment"):
                src = os.path.join(tmpdir, f)
                # Move/rename to target path
                if not output_path.endswith(f".{ext}"):
                    output_path = f"{output_path}.{ext}"
                import shutil
                shutil.move(src, output_path)

                # Cleanup
                shutil.rmtree(tmpdir, ignore_errors=True)
                return os.path.exists(output_path) and os.path.getsize(output_path) > 100

    except Exception:
        pass

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    return False
