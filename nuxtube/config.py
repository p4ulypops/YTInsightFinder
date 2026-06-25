#!/usr/bin/env python3
"""Configuration management for NuxTube.

Handles loading/saving YAML config and the interactive first-run setup wizard.
"""
import os
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional


@dataclass
class Source:
    """A watchable source (playlist, channel, or individual video)."""
    url: str
    name: str = ""
    type: str = "playlist"  # playlist | channel | video
    enabled: bool = True


@dataclass
class PipelineConfig:
    """Which stages to run and their parameters."""
    stages: List[str] = field(default_factory=lambda: [
        "transcript", "metadata", "download", "screenshots",
        "clips", "keypoints", "tracker"
    ])
    screenshot_offset: int = 3       # seconds after cue
    clip_duration: int = 16          # seconds
    clip_start_offset: int = -4     # seconds before cue
    max_clips: int = 8
    max_height: int = 720
    keep_video: bool = False
    client_cycle: List[str] = field(default_factory=lambda: [
        "android", "ios", "tv", "web_safari", "mweb"
    ])


@dataclass
class WatchConfig:
    """Playlist/channel watching settings."""
    poll_interval: int = 300        # seconds between checks
    auto_archive: bool = True
    max_workers: int = 3
    archive_delay: int = 20         # seconds between starting archives
    archive_timeout: int = 600      # per-video timeout


@dataclass
class Config:
    """Top-level configuration."""
    output_dir: str = "./youtube_videos"
    sources: List[Source] = field(default_factory=list)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    categories: List[str] = field(default_factory=lambda: [
        "ai-agents", "coding", "productivity", "business",
        "seo", "marketing", "design", "uncategorized"
    ])
    config_path: str = "config.yaml"

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    def to_dict(self) -> dict:
        return {
            "output_dir": self.output_dir,
            "sources": [
                {"url": s.url, "name": s.name, "type": s.type, "enabled": s.enabled}
                for s in self.sources
            ],
            "pipeline": {
                "stages": self.pipeline.stages,
                "screenshot_offset": self.pipeline.screenshot_offset,
                "clip_duration": self.pipeline.clip_duration,
                "clip_start_offset": self.pipeline.clip_start_offset,
                "max_clips": self.pipeline.max_clips,
                "max_height": self.pipeline.max_height,
                "keep_video": self.pipeline.keep_video,
                "client_cycle": self.pipeline.client_cycle,
            },
            "watch": {
                "poll_interval": self.watch.poll_interval,
                "auto_archive": self.watch.auto_archive,
                "max_workers": self.watch.max_workers,
                "archive_delay": self.watch.archive_delay,
                "archive_timeout": self.watch.archive_timeout,
            },
            "categories": self.categories,
        }

    def save(self, path: str = None):
        path = path or self.config_path
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
        return path

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        cfg = cls()
        cfg.output_dir = d.get("output_dir", cfg.output_dir)
        cfg.sources = [
            Source(**s) for s in d.get("sources", [])
        ]
        p = d.get("pipeline", {})
        cfg.pipeline = PipelineConfig(
            stages=p.get("stages", cfg.pipeline.stages),
            screenshot_offset=p.get("screenshot_offset", 3),
            clip_duration=p.get("clip_duration", 16),
            clip_start_offset=p.get("clip_start_offset", -4),
            max_clips=p.get("max_clips", 8),
            max_height=p.get("max_height", 720),
            keep_video=p.get("keep_video", False),
            client_cycle=p.get("client_cycle", cfg.pipeline.client_cycle),
        )
        w = d.get("watch", {})
        cfg.watch = WatchConfig(
            poll_interval=w.get("poll_interval", 300),
            auto_archive=w.get("auto_archive", True),
            max_workers=w.get("max_workers", 3),
            archive_delay=w.get("archive_delay", 20),
            archive_timeout=w.get("archive_timeout", 600),
        )
        cfg.categories = d.get("categories", cfg.categories)
        return cfg

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        cfg = cls.from_dict(data)
        cfg.config_path = path
        return cfg


def interactive_setup() -> Config:
    """First-run interactive setup wizard. Asks questions, saves config."""
    print()
    print("=" * 60)
    print("  \U0001f525 NuxTube — First Run Setup")
    print("=" * 60)
    print()
    print("Let's get you configured. You can edit config.yaml later.")
    print()

    # 1. Output directory
    print("\U0001f4c1 Where should archives be saved?")
    output_dir = input("  [./youtube_videos] > ").strip() or "./youtube_videos"
    print()

    # 2. Sources
    sources = []
    print("\U0001f4fa Add YouTube sources to watch:")
    print()

    # Playlists
    print("  \U0001f4dd Playlists (one URL per line, empty line to finish):")
    while True:
        url = input("    > ").strip()
        if not url:
            break
        name = input(f"      Name for this playlist? [{url[-20:]}] > ").strip()
        sources.append(Source(url=url, name=name or url[:40], type="playlist"))
    print()

    # Channels (with BIG disclaimer)
    print("  \U0001f4f1 YouTube Channels (WARNING — read below):")
    print()
    print("  \033[1;31mâš ï¸�  CHANNEL WATCHING DISCLAIMER âš ï¸�\033[0m")
    print("  \033[1;33m")
    print("  âš   Watching entire channels can:")
    print("     â€¢ Overload your computer/VPS quickly")
    print("     â€¢ Cost a fortune in bandwidth + storage")
    print("     â€¢ Potentially harm the YouTuber (mass-downloading")
    print("       their content can trigger YouTube protections)")
    print("     â€¢ Potentially violate YouTube ToS or copyright law")
    print("     â€¢ Generate massive API calls that may get you blocked")
    print()
    print("  âš   Use channel watching ONLY for channels you own,")
    print("     have permission to archive, or are public domain.")
    print("  \033[0m")
    print()
    add_channels = input("  Add channels anyway? [y/N] > ").strip().lower()
    if add_channels == "y":
        print("  Channel URLs (one per line, empty to finish):")
        while True:
            url = input("    > ").strip()
            if not url:
                break
            name = input(f"      Name? [{url[:30]}] > ").strip()
            sources.append(Source(url=url, name=name or url[:40], type="channel"))
    print()

    # 3. Pipeline stages
    all_stages = ["transcript", "metadata", "download", "screenshots",
                  "clips", "keypoints", "tracker"]
    print("\U0001f527 Pipeline stages (which steps to run per video):")
    print("  Available:", ", ".join(all_stages))
    print("  Default: all stages")
    stages_input = input("  Stages (comma-sep, or Enter for all) > ").strip()
    if stages_input:
        stages = [s.strip() for s in stages_input.split(",")]
    else:
        stages = all_stages
    print()

    # 4. Workers
    workers = input("\U0001f9ea Parallel workers [3] > ").strip()
    workers = int(workers) if workers.isdigit() else 3

    # 5. Poll interval
    poll = input("\U0001f501 Playlist check interval in seconds [300] > ").strip()
    poll = int(poll) if poll.isdigit() else 300
    print()

    # 6. Keep video?
    keep = input("\U0001f3a8 Keep source MP4 after archiving? [y/N] > ").strip().lower() == "y"

    cfg = Config(
        output_dir=output_dir,
        sources=sources,
        pipeline=PipelineConfig(stages=stages, keep_video=keep),
        watch=WatchConfig(
            poll_interval=poll,
            max_workers=workers,
        ),
    )

    # Save
    save_path = os.path.join(os.path.dirname(os.path.abspath(output_dir)), "config.yaml")
    # Try to save next to the entry point
    script_dir = os.path.dirname(os.path.abspath(__file__))
    save_path = os.path.join(script_dir, "..", "config.yaml")
    cfg.save(save_path)
    print()
    print(f"\u2705 Config saved to {os.path.abspath(save_path)}")
    print()
    return cfg


def load_or_setup(config_path: str = "config.yaml") -> Config:
    """Load config if it exists, otherwise run interactive setup."""
    if os.path.exists(config_path):
        return Config.load(config_path)
    return interactive_setup()
