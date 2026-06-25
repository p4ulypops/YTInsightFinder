#!/usr/bin/env python3
"""Archive pipeline orchestration.

Coordinates all stages: transcript -> metadata -> download -> screenshots ->
clips -> keypoints -> tracker. Supports stage selection and progress callbacks
for TUI integration.

Designed to be importable as middleware:
    from nuxtube.archiver import ArchivePipeline
    from nuxtube.config import Config
    pipeline = ArchivePipeline(Config.load("config.yaml"))
    result = pipeline.archive("https://youtube.com/watch?v=...")
"""
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from .config import Config
from .transcript import (
    extract_video_id, fetch_transcript, fetch_oembed, slugify,
    format_timestamp, parse_ts,
)
from .media import (
    download_video, find_visual_cues, take_screenshots, extract_clips,
    cleanup_temp, VISUAL_CUES, CLIP_KEYWORDS,
)
from .keypoints import extract_keypoints as run_keypoint_extraction
from .tracker import TrackerCSV


# Category suggestion keyword table
CATEGORY_KEYWORDS = [
    ("ai-agents", ["agent", "operating system", "orchestrat", "autonomous",
                    "llm", "claude", "hermes", "ai ", "gpt", "gemini"]),
    ("seo", ["seo", "keyword", "rank", "backlink", "search console", "serp"]),
    ("coding", ["code", "python", "javascript", "react", "api", "function",
                "debug", "programming", "developer", "typescript"]),
    ("productivity", ["productivity", "workflow", "notion", "obsidian",
                       "note-taking", "second brain", "zettelkasten", "pkm"]),
    ("marketing", ["marketing", "ads", "funnel", "audience", "campaign",
                    "brand", "social media"]),
    ("business", ["business", "revenue", "startup", "client", "agency",
                   "profit", "entrepreneur", "saas"]),
    ("design", ["design", "ui", "ux", "figma", "css", "tailwind", "aesthetic"]),
]


def suggest_category(title: str, text: str) -> str:
    """Suggest a category based on keyword scoring."""
    t = (title + " " + text[:2000]).lower()
    best, score = "uncategorized", 0
    for cat, kws in CATEGORY_KEYWORDS:
        s = sum(t.count(kw) for kw in kws)
        if s > score:
            best, score = cat, s
    return best


@dataclass
class ArchiveResult:
    """Result of archiving a single video."""
    video_id: str = ""
    title: str = ""
    url: str = ""
    category: str = ""
    status: str = "unknown"  # success | failed | partial | skipped
    stages_completed: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    folder: str = ""
    screenshot_count: int = 0
    clip_count: int = 0
    duration: str = "?"
    segment_count: int = 0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


# Callback type aliases
LogCallback = Callable[[str, str], None]  # (level, message)
ProgressCallback = Callable[[str, int, int, str], None]  # (stage, cur, total, msg)


class ArchivePipeline:
    """Orchestrates the full archive pipeline for a single video."""

    def __init__(self, config: Config):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.tracker = TrackerCSV(
            str(self.output_dir / "master_tracker.csv")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, cb: LogCallback, level: str, msg: str):
        if cb:
            cb(level, msg)

    def _progress(self, cb: ProgressCallback, stage: str, cur: int, total: int, msg: str):
        if cb:
            cb(stage, cur, total, msg)

    def _find_existing(self, video_id: str) -> Optional[Path]:
        """Check if video already archived."""
        for meta_path in self.output_dir.glob("*/*/metadata.json"):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if meta.get("video_id") == video_id:
                    return meta_path.parent
            except Exception:
                continue
        return None

    def archive(self, url: str, category: str = None,
                on_log: LogCallback = None,
                on_progress: ProgressCallback = None) -> ArchiveResult:
        """Run the full archive pipeline for a single video.

        Args:
            url: YouTube video URL or ID
            category: Force a category (auto-suggested if None)
            on_log: Callback for log messages (level, message)
            on_progress: Callback for progress updates (stage, cur, total, msg)

        Returns:
            ArchiveResult with status and metadata
        """
        stages = self.config.pipeline.stages
        result = ArchiveResult(url=url)
        video_id = extract_video_id(url)

        if not video_id:
            result.status = "failed"
            result.errors.append(f"Could not extract video ID from: {url}")
            return result

        result.video_id = video_id

        # Check if already archived
        existing = self._find_existing(video_id)
        if existing:
            self._log(on_log, "info", f"Already archived: {existing}")
            result.status = "skipped"
            result.folder = str(existing)
            return result

        # --- Stage 1: Transcript ---
        if "transcript" in stages:
            self._log(on_log, "info", f"Fetching transcript for {video_id}...")
            self._progress(on_progress, "transcript", 0, 1, "Fetching...")
            transcript = fetch_transcript(url)
            if not transcript:
                result.status = "failed"
                result.errors.append("Transcript fetch failed (all 3 tiers)")
                self._log(on_log, "error", "Transcript fetch failed")
                return result
            result.segment_count = transcript.get("segment_count", 0)
            result.duration = transcript.get("duration", "?")
            self._log(on_log, "ok", f"Got {result.segment_count} segments ({result.duration})")
            self._progress(on_progress, "transcript", 1, 1, f"{result.segment_count} segments")
            result.stages_completed.append("transcript")
        else:
            result.status = "failed"
            result.errors.append("Transcript stage disabled - cannot archive")
            return result

        # --- Stage 2: Metadata ---
        if "metadata" in stages:
            self._progress(on_progress, "metadata", 0, 1, "Fetching oEmbed...")
            oembed = fetch_oembed(url)
            title = oembed.get("title", video_id)
            channel = oembed.get("author_name", "Unknown")
            channel_url = oembed.get("author_url", "")
            thumbnail = oembed.get("thumbnail_url",
                f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
            self._progress(on_progress, "metadata", 1, 1, title)
            self._log(on_log, "ok", f"Title: {title}")
            result.stages_completed.append("metadata")
        else:
            title = video_id
            channel = "Unknown"
            channel_url = ""
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        result.title = title

        # --- Stage 2b: Player Data (chapters, heatmap) ---
        player_data = None
        if "player_data" in stages:
            self._progress(on_progress, "player_data", 0, 1, "Fetching chapters + heatmap...")
            self._log(on_log, "info", "Fetching player data (chapters, heatmap)...")
            from .player_data import fetch_player_data
            player_data = fetch_player_data(url)
            if player_data:
                self._log(on_log, "ok",
                    f"Player data: {len(player_data.chapters)} chapters, "
                    f"{len(player_data.heatmap)} heatmap entries, "
                    f"{player_data.view_count} views")
                self._progress(on_progress, "player_data", 1, 1,
                    f"{len(player_data.chapters)} chapters, {len(player_data.heatmap)} heatmap")
                result.stages_completed.append("player_data")
            else:
                self._log(on_log, "warn", "Player data fetch failed (non-critical)")
                self._progress(on_progress, "player_data", 1, 1, "failed (non-critical)")
        else:
            self._log(on_log, "info", "Skipping player data stage")

        # Determine category
        if not category:
            category = suggest_category(title, transcript.get("full_text", ""))
        result.category = category

        # Create folder
        slug = slugify(title) or video_id
        folder = self.output_dir / category / slug
        folder.mkdir(parents=True, exist_ok=True)
        result.folder = str(folder)

        # Write transcript.md
        self._write_transcript_md(folder, transcript, title, channel, url)

        # Write metadata.json
        metadata = {
            "title": title,
            "video_id": video_id,
            "url": url,
            "channel": channel,
            "channel_url": channel_url,
            "category": category,
            "duration": result.duration,
            "segment_count": result.segment_count,
            "thumbnail_url": thumbnail,
            "fetched_at": datetime.now().isoformat(),
            "source": transcript.get("source", "unknown"),
            "files": {
                "transcript": "transcript.md",
                "metadata": "metadata.json",
            },
        }

        # --- Stages 3-5: Download, Screenshots, Clips ---
        screenshots = []
        clips = []
        video_path = None
        tmp_path = None

        # Determine key moments (smart mode uses player data)
        cue_timestamps = []
        cues = []
        if self.config.pipeline.key_moment_mode == "smart" and player_data:
            # Use smart key moment detection
            if "download" in stages:
                cues_from_transcript = find_visual_cues(transcript["segments"])
                cue_timestamps = [c["timestamp"] for c in cues_from_transcript]
            key_moments = player_data.find_key_moments(cue_timestamps)
            self._log(on_log, "info",
                f"Smart key moments: {len(key_moments)} found "
                f"(chapters={len(player_data.chapters)}, "
                f"heatmap={'yes' if player_data.has_heatmap else 'no'}, "
                f"cues={len(cue_timestamps)})")
            # Convert to cue format for screenshots
            cues = [{"timestamp": m.timestamp, "phrase": m.title,
                     "context": m.chapter_title or m.title} for m in key_moments]
        else:
            # Original visual-cue-only mode
            cues_from_transcript = find_visual_cues(transcript["segments"])
            cues = cues_from_transcript

        # Check capture mode
        capture_mode = self.config.pipeline.capture_mode
        if capture_mode == "transcript":
            # Transcript-only mode — skip all download/screenshot/clip stages
            self._log(on_log, "info", "Transcript-only mode — skipping media download")
        elif "download" in stages:
            if capture_mode == "audio":
                # Audio-only mode — download audio, no screenshots/clips
                self._progress(on_progress, "download", 0, 1, "Downloading audio...")
                self._log(on_log, "info", "Audio-only mode — downloading audio...")
                from .player_data import download_segment
                audio_path = str(folder / "audio.mp3")
                # Download full audio
                video_path = download_video(
                    url, self.config.pipeline.client_cycle,
                    max_height=480,  # smallest video that has audio
                )
                if video_path:
                    # Extract audio with ffmpeg
                    import subprocess
                    try:
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", video_path, "-vn",
                             "-acodec", "libmp3lame", "-q:a", "2", audio_path],
                            capture_output=True, timeout=120,
                        )
                        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 100:
                            metadata["files"]["audio"] = "audio.mp3"
                            self._log(on_log, "ok", f"Audio extracted: {os.path.getsize(audio_path) // 1024}KB")
                            result.stages_completed.append("download")
                        cleanup_temp(video_path)
                        video_path = None
                    except Exception as e:
                        self._log(on_log, "warn", f"Audio extraction failed: {e}")
                        cleanup_temp(video_path)
                        video_path = None
                else:
                    self._log(on_log, "warn", "Audio download failed")

            elif self.config.pipeline.segment_download and cues:
                # Smart segment download — download only key segments, not whole video
                self._log(on_log, "info",
                    f"Segment download mode — downloading {len(cues)} segments "
                    f"instead of full video")
                self._progress(on_progress, "download", 0, len(cues), "Downloading segments...")
                from .player_data import download_segment as dl_seg

                segment_paths = []
                for i, cue in enumerate(cues):
                    seg_path = str(folder / "screenshots" / f"seg_{i:02d}_"
                                   f"{int(cue['timestamp']//60):02d}m{int(cue['timestamp']%60):02d}s")
                    start = max(0, cue["timestamp"] + self.config.pipeline.clip_start_offset)
                    dur = self.config.pipeline.clip_duration + abs(self.config.pipeline.clip_start_offset) + 3
                    ok = dl_seg(url, start, dur, seg_path,
                               quality=self.config.pipeline.quality, audio_only=False)
                    if ok:
                        # Find the actual file (extension may have been added)
                        for f in os.listdir(folder / "screenshots"):
                            if f.startswith(f"seg_{i:02d}"):
                                segment_paths.append((cue, os.path.join("screenshots", f)))
                                break
                    self._progress(on_progress, "download", i + 1, len(cues),
                                   f"Segment {i+1}/{len(cues)}")

                if segment_paths:
                    self._log(on_log, "ok", f"Downloaded {len(segment_paths)} segments")
                    result.stages_completed.append("download")

                    # Take screenshots from segments
                    if "screenshots" in stages:
                        self._progress(on_progress, "screenshots", 0, len(segment_paths), "Extracting screenshots...")
                        for i, (cue, seg_rel) in enumerate(segment_paths):
                            seg_path = str(folder / seg_rel)
                            ss_name = f"{int(cue['timestamp']//60):02d}m{int(cue['timestamp']%60):02d}s.jpg"
                            ss_path = str(folder / "screenshots" / ss_name)
                            try:
                                subprocess.run(
                                    ["ffmpeg", "-y", "-ss", str(self.config.pipeline.screenshot_offset),
                                     "-i", seg_path, "-frames:v", "1", "-q:v", "2", ss_path],
                                    capture_output=True, text=True, timeout=15,
                                )
                                if os.path.exists(ss_path) and os.path.getsize(ss_path) > 100:
                                    screenshots.append({
                                        "timestamp": cue["timestamp"],
                                        "screenshot": f"screenshots/{ss_name}",
                                        "ok": True,
                                    })
                            except Exception:
                                pass
                            self._progress(on_progress, "screenshots", i + 1, len(segment_paths), "")

                        ok_count = len([s for s in screenshots if s.get("ok")])
                        self._log(on_log, "ok", f"Screenshots: {ok_count}")
                        result.screenshot_count = ok_count
                        result.stages_completed.append("screenshots")

                        if screenshots:
                            with open(folder / "_screenshots_manifest.json", "w") as f:
                                json.dump(screenshots, f, indent=2)

                    # Segments ARE the clips
                    if "clips" in stages:
                        for cue, seg_rel in segment_paths:
                            clips.append({
                                "timestamp": cue["timestamp"],
                                "clip": seg_rel,
                                "ok": True,
                                "score": 1,
                            })
                        ok_count = len([c for c in clips if c.get("ok")])
                        self._log(on_log, "ok", f"Clips: {ok_count} (from segments)")
                        result.clip_count = ok_count
                        result.stages_completed.append("clips")

                        if clips:
                            with open(folder / "_clips_manifest.json", "w") as f:
                                json.dump(clips, f, indent=2)

                    # Clean up segments (they served their purpose)
                    if not self.config.pipeline.keep_video:
                        for _, seg_rel in segment_paths:
                            try:
                                os.unlink(str(folder / seg_rel))
                            except Exception:
                                pass

            else:
                # Full video download (original mode)
                self._progress(on_progress, "download", 0, 1, "Downloading video...")
                self._log(on_log, "info",
                    f"Downloading ({self.config.pipeline.max_height}p)...")
                video_path = download_video(
                    url, self.config.pipeline.client_cycle,
                    self.config.pipeline.max_height,
                )
                if video_path:
                    tmp_path = video_path
                    self._log(on_log, "ok", f"Downloaded: {os.path.basename(video_path)}")
                    self._progress(on_progress, "download", 1, 1, "Downloaded")
                    result.stages_completed.append("download")

                    self._log(on_log, "info", f"Found {len(cues)} key moments")

                    # Screenshots
                    if "screenshots" in stages and cues:
                        self._progress(on_progress, "screenshots", 0, len(cues), "Taking screenshots...")
                        screenshots = take_screenshots(
                            video_path, cues, str(folder / "screenshots"),
                            self.config.pipeline.screenshot_offset,
                        )
                        ok_count = len([s for s in screenshots if s.get("ok")])
                        self._log(on_log, "ok", f"Screenshots: {ok_count}/{len(screenshots)}")
                        result.screenshot_count = ok_count
                        result.stages_completed.append("screenshots")

                        with open(folder / "_screenshots_manifest.json", "w") as f:
                            json.dump(screenshots, f, indent=2)

                    # Clips
                    if "clips" in stages and cues:
                        clips = extract_clips(
                            video_path, cues, str(folder / "clips"),
                            self.config.pipeline,
                        )
                        ok_count = len([c for c in clips if c.get("ok")])
                        self._log(on_log, "ok", f"Clips: {ok_count}/{len(clips)}")
                        result.clip_count = ok_count
                        result.stages_completed.append("clips")

                        if clips:
                            with open(folder / "_clips_manifest.json", "w") as f:
                                json.dump(clips, f, indent=2)

                    # Keep or delete video
                    if self.config.pipeline.keep_video:
                        import shutil
                        dst = folder / "source.mp4"
                        shutil.move(video_path, str(dst))
                        metadata["files"]["source_video"] = "source.mp4"
                        tmp_path = None
                    else:
                        cleanup_temp(video_path)
                        tmp_path = None
                else:
                    self._log(on_log, "warn", "Video download failed (all clients returned 403)")
                    result.errors.append("Video download failed")

        # Always cleanup temp file if still exists
        if tmp_path:
            cleanup_temp(tmp_path)

        # Write metadata.json
        metadata["media"] = {
            "screenshots_dir": "screenshots/" if screenshots else None,
            "screenshot_count": len([s for s in screenshots if s.get("ok")]),
            "clips_dir": "clips/" if clips else None,
            "clip_count": len([c for c in clips if c.get("ok")]),
        }
        # Add player data to metadata
        if player_data:
            metadata["player_data"] = player_data.to_dict()
            metadata["capture_mode"] = self.config.pipeline.capture_mode
            metadata["quality"] = self.config.pipeline.quality
            metadata["key_moment_mode"] = self.config.pipeline.key_moment_mode
        with open(folder / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        # --- Stage 6: Key-point extraction ---
        if "keypoints" in stages:
            self._progress(on_progress, "keypoints", 0, 1, "Extracting key points via LLM...")
            self._log(on_log, "info", "Extracting key points...")
            success = run_keypoint_extraction(str(folder), self.config)
            if success:
                self._log(on_log, "ok", "Key points extracted")
                result.stages_completed.append("keypoints")
            else:
                self._log(on_log, "warn", "Key-point extraction failed")
                result.errors.append("Key-point extraction failed")

        # --- Stage 7: Tracker CSV ---
        if "tracker" in stages:
            self._log(on_log, "info", "Updating tracker CSV...")
            rel_folder = f"{category}/{slug}"
            self.tracker.append(metadata, rel_folder, screenshots, clips,
                                status="Done" if not result.errors else "Partial")
            result.stages_completed.append("tracker")

        # Determine final status
        if not result.errors:
            result.status = "success"
        elif result.stages_completed:
            result.status = "partial"
        else:
            result.status = "failed"

        self._log(on_log, "ok" if result.status == "success" else "warn",
                  f"Archive complete: {result.status} ({len(result.stages_completed)} stages)")

        return result

    def _write_transcript_md(self, folder: Path, transcript: dict,
                             title: str, channel: str, url: str):
        """Write transcript.md with header, timestamped text, and visual references."""
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
