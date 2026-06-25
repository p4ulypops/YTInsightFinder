#!/usr/bin/env python3
"""NuxTube — YouTube Archive Pipeline & Playlist Watcher

Entry point. Run this to start the TUI dashboard.

Usage:
    python3 nuxtube.py                          # Launch TUI (first run = interactive setup)
    python3 nuxtube.py --tui                    # Explicitly launch TUI
    python3 nuxtube.py --archive URL            # Quick archive without TUI
    python3 nuxtube.py --archive URL --category coding
    python3 nuxtube.py --config my.yaml         # Use custom config
    python3 nuxtube.py --check-playlist URL     # List videos in a playlist
    python3 nuxtube.py --version

First run: If no config.yaml exists, runs interactive setup wizard.
"""
import argparse
import os
import sys
import json

# Ensure the package can be imported when run as a script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nuxtube.config import Config, load_or_setup, interactive_setup
from nuxtube.archiver import ArchivePipeline
from nuxtube.watcher import PlaylistWatcher, extract_playlist_videos, channel_disclaimer
from nuxtube.tracker import TrackerCSV


def main():
    parser = argparse.ArgumentParser(
        description="NuxTube — YouTube Archive Pipeline & Playlist Watcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 nuxtube.py                        # Launch TUI dashboard
  python3 nuxtube.py --archive "https://youtube.com/watch?v=..."
  python3 nuxtube.py --archive URL --category coding
  python3 nuxtube.py --config custom.yaml   # Use custom config
  python3 nuxtube.py --check-playlist "https://youtube.com/playlist?list=..."
  python3 nuxtube.py --setup                 # Re-run interactive setup
  python3 nuxtube.py --version
        """,
    )

    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--tui", action="store_true", help="Launch TUI dashboard (default)")
    parser.add_argument("--archive", metavar="URL", help="Archive a single video without TUI")
    parser.add_argument("--category", help="Force category for --archive")
    parser.add_argument("--setup", action="store_true", help="Re-run interactive setup")
    parser.add_argument("--check-playlist", metavar="URL", help="List videos in a playlist")
    parser.add_argument("--check-channel", metavar="URL", help="List videos in a channel (with disclaimer)")
    parser.add_argument("--version", action="store_true", help="Show version")

    args = parser.parse_args()

    if args.version:
        from nuxtube import __version__
        print(f"NuxTube v{__version__}")
        sys.exit(0)

    # --- Setup mode ---
    if args.setup:
        cfg = interactive_setup()
        print(f"\nConfig saved. Run 'python3 nuxtube.py' to start the TUI.")
        sys.exit(0)

    # --- Check playlist mode ---
    if args.check_playlist:
        print(f"Fetching playlist: {args.check_playlist}")
        videos = extract_playlist_videos(args.check_playlist)
        print(f"Found {len(videos)} videos:\n")
        for i, (vid, title) in enumerate(videos, 1):
            print(f"  {i:3d}. {title[:60]}")
            print(f"       https://youtube.com/watch?v={vid}")
        sys.exit(0)

    # --- Check channel mode (with disclaimer) ---
    if args.check_channel:
        print(channel_disclaimer())
        confirm = input("Continue? [y/N] > ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)
        print(f"\nFetching channel: {args.check_channel}")
        from nuxtube.watcher import extract_channel_videos
        videos = extract_channel_videos(args.check_channel)
        print(f"Found {len(videos)} videos:\n")
        for i, (vid, title) in enumerate(videos, 1):
            print(f"  {i:3d}. {title[:60]}")
            print(f"       https://youtube.com/watch?v={vid}")
        sys.exit(0)

    # --- Quick archive mode (no TUI) ---
    if args.archive:
        config = load_or_setup(args.config)
        pipeline = ArchivePipeline(config)

        def on_log(level, msg):
            icons = {"info": "[i]", "ok": "[+]", "warn": "[!]", "error": "[X]"}
            print(f"  {icons.get(level, '[ ]')} {msg}")

        def on_progress(stage, cur, total, msg):
            if total > 1:
                print(f"  [{stage}] {cur}/{total} — {msg}")

        print(f"Archiving: {args.archive}")
        result = pipeline.archive(
            args.archive,
            category=args.category,
            on_log=on_log,
            on_progress=on_progress,
        )
        print(f"\nResult: {result.status.upper()}")
        print(f"  Title:     {result.title}")
        print(f"  Category:  {result.category}")
        print(f"  Folder:    {result.folder}")
        print(f"  Screens:   {result.screenshot_count}")
        print(f"  Clips:     {result.clip_count}")
        print(f"  Stages:    {', '.join(result.stages_completed)}")
        if result.errors:
            print(f"  Errors:    {len(result.errors)}")
            for e in result.errors:
                print(f"    — {e}")
        sys.exit(0 if result.status != "failed" else 1)

    # --- Default: launch TUI ---
    config = load_or_setup(args.config)

    from nuxtube.tui import NuxTubeTUI
    tui = NuxTubeTUI(config)
    tui.run()


if __name__ == "__main__":
    main()
