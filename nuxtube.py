#!/usr/bin/env python3
"""NuxTube — YouTube Archive Pipeline & Playlist Watcher

Entry point. Supports TUI, headless daemon, web dashboard, and CLI modes.

Usage:
    python3 nuxtube.py                          # Launch TUI (first run = interactive setup)
    python3 nuxtube.py --tui                    # Explicitly launch TUI
    python3 nuxtube.py --daemon                 # Headless daemon (no TUI)
    python3 nuxtube.py --daemon --web 8080      # Headless + web dashboard
    python3 nuxtube.py --web 8080               # Web dashboard (with TUI too)
    python3 nuxtube.py --status                 # Query running daemon status
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
import time

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
  python3 nuxtube.py --daemon               # Headless daemon (no TUI)
  python3 nuxtube.py --daemon --web 8080    # Headless + web dashboard
  python3 nuxtube.py --web 8080             # TUI + web dashboard
  python3 nuxtube.py --status               # Query running daemon
  python3 nuxtube.py --archive "https://youtube.com/watch?v=..."
  python3 nuxtube.py --archive URL --category coding
  python3 nuxtube.py --config custom.yaml   # Use custom config
  python3 nuxtube.py --check-playlist "https://youtube.com/playlist?list=..."
  python3 nuxtube.py --setup                # Re-run interactive setup
  python3 nuxtube.py --version
        """,
    )

    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--tui", action="store_true", help="Launch TUI dashboard (default)")
    parser.add_argument("--daemon", action="store_true", help="Run as headless daemon (no TUI)")
    parser.add_argument("--web", type=int, metavar="PORT", nargs="?", const=8080, help="Start web dashboard on PORT (default 8080)")
    parser.add_argument("--status", action="store_true", help="Query status of running daemon")
    parser.add_argument("--archive", metavar="URL", help="Archive a single video without TUI")
    parser.add_argument("--category", help="Force category for --archive")
    parser.add_argument("--setup", action="store_true", help="Re-run interactive setup")
    parser.add_argument("--check-playlist", metavar="URL", help="List videos in a playlist")
    parser.add_argument("--check-channel", metavar="URL", help="List videos in a channel (with disclaimer)")
    parser.add_argument("--version", action="store_true", help="Show version")
    # OmniFile + Viewer generation
    parser.add_argument("--omni", metavar="FOLDER", help="Generate omni.json for an archive folder")
    parser.add_argument("--omni-all", action="store_true", help="Generate omni.json for all archived videos")
    parser.add_argument("--viewer", metavar="FOLDER", help="Generate viewer.html for an archive folder")
    parser.add_argument("--viewer-all", action="store_true", help="Generate viewer.html for all archived videos")
    parser.add_argument("--bake", action="store_true", help="Embed OmniFile data into viewer HTML (use with --viewer/--viewer-all)")
    # Export formats
    parser.add_argument("--export", metavar="FOLDER", help="Export archive folder to structured formats")
    parser.add_argument("--export-all", action="store_true", help="Export all archived videos")
    parser.add_argument("--formats", default="markdown,obsidian,csv,context_card,hermes_skill,llm_skill",
                        help="Comma-separated export formats (default: all). "
                             "Options: markdown, obsidian, csv, excel, context_card, hermes_skill, llm_skill")

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

    # --- OmniFile generation ---
    if args.omni:
        from nuxtube.omni import write_omni
        path = write_omni(args.omni)
        if path:
            print(f"[+] OmniFile written: {path}")
        else:
            print(f"[X] Failed — folder not found: {args.omni}")
            sys.exit(1)
        sys.exit(0)

    if args.omni_all:
        from nuxtube.omni import write_omni
        config = load_or_setup(args.config)
        count = 0
        for meta_path in sorted(config.output_path.glob("*/*/metadata.json")):
            folder = str(meta_path.parent)
            path = write_omni(folder)
            if path:
                print(f"  [+] {os.path.relpath(path, config.output_dir)}")
                count += 1
        print(f"\nGenerated {count} OmniFile(s).")
        sys.exit(0)

    # --- Viewer generation ---
    if args.viewer:
        from nuxtube.viewer import generate_viewer
        path = generate_viewer(args.viewer, bake=args.bake)
        if path:
            mode = "baked" if args.bake else "live"
            print(f"[+] Viewer ({mode}) written: {path}")
            if not args.bake:
                print(f"    Serve: cd '{args.viewer}' && python3 -m http.server 9000")
                print(f"    Then open: http://localhost:9000/viewer.html")
        else:
            print(f"[X] Failed — folder not found: {args.viewer}")
            sys.exit(1)
        sys.exit(0)

    if args.viewer_all:
        from nuxtube.viewer import generate_viewer
        config = load_or_setup(args.config)
        count = 0
        for meta_path in sorted(config.output_path.glob("*/*/metadata.json")):
            folder = str(meta_path.parent)
            path = generate_viewer(folder, bake=args.bake)
            if path:
                mode = "baked" if args.bake else "live"
                rel = os.path.relpath(path, config.output_dir)
                print(f"  [{mode[0].upper()}] {rel}")
                count += 1
        print(f"\nGenerated {count} viewer(s).")
        sys.exit(0)

    # --- Export mode ---
    if args.export:
        from nuxtube.exporters import export_from_folder, FORMATS
        fmt_list = [f.strip() for f in args.formats.split(",")]
        print(f"Exporting: {args.export}")
        print(f"Formats:   {', '.join(fmt_list)}")
        results = export_from_folder(args.export, fmt_list)
        for fmt, path in results.items():
            status = "[X]" if str(path).startswith("ERROR") else "[+]"
            print(f"  {status} {fmt:<14} {path}")
        sys.exit(0)

    if args.export_all:
        from nuxtube.exporters import export_from_folder, FORMATS
        config = load_or_setup(args.config)
        fmt_list = [f.strip() for f in args.formats.split(",")]
        print(f"Exporting all videos — formats: {', '.join(fmt_list)}")
        count = 0
        for meta_path in sorted(config.output_path.glob("*/*/metadata.json")):
            folder = str(meta_path.parent)
            rel = os.path.relpath(folder, config.output_dir)
            print(f"\n  {rel}")
            results = export_from_folder(folder, fmt_list)
            for fmt, path in results.items():
                status = "[X]" if str(path).startswith("ERROR") else "[+]"
                print(f"    {status} {fmt:<14} {os.path.basename(str(path))}")
            count += 1
        print(f"\nExported {count} video(s).")
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

    # --- Status query mode (talk to running daemon via HTTP) ---
    if args.status:
        import urllib.request
        port = args.web or 8080
        try:
            r = urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=5)
            status = json.loads(r.read())
            print(f"NuxTube Daemon Status")
            print(f"  Running:   {status['running']}")
            print(f"  Paused:    {status['paused']}")
            print(f"  Uptime:    {status['stats']['uptime']}")
            print(f"  Archived:  {status['stats']['total_archived']}")
            print(f"  Failed:    {status['stats']['total_failed']}")
            print(f"  Queue:     {status['queue']['count']}")
            active = sum(1 for w in status['workers'] if w['busy'])
            print(f"  Workers:   {active}/{len(status['workers'])} active")
            if status['log']:
                print(f"\n  Recent log:")
                for line in status['log'][-5:]:
                    print(f"    {line}")
        except Exception as e:
            print(f"Cannot reach daemon on port {port}: {e}")
            print("Is the daemon running? Start with: python3 nuxtube.py --daemon --web PORT")
        sys.exit(0)

    # --- Daemon mode (headless, optional web dashboard) ---
    if args.daemon:
        config = load_or_setup(args.config)
        from nuxtube.middleware import NuxTubeDaemon, write_pid, clear_pid
        daemon = NuxTubeDaemon(config)
        daemon.start()
        write_pid()
        print(f"\n  NuxTube daemon running (PID {os.getpid()})")
        print(f"  Workers: {config.watch.max_workers}")
        print(f"  Sources: {len(config.sources)}")
        print(f"  Poll:    {config.watch.poll_interval}s")
        print(f"  Output:  {config.output_dir}")
        print()

        web_server = None
        if args.web:
            from nuxtube.dashboard import DashboardServer
            web_server = DashboardServer(daemon, port=args.web)
            web_server.start()

        # Keep main thread alive
        try:
            while daemon.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            daemon.stop()
            if web_server:
                web_server.stop()
            clear_pid()
            print("Daemon shut down.")
        sys.exit(0)

    # --- Web dashboard alongside TUI ---
    if args.web and not args.daemon:
        config = load_or_setup(args.config)
        from nuxtube.middleware import NuxTubeDaemon
        from nuxtube.dashboard import DashboardServer
        daemon = NuxTubeDaemon(config)
        daemon.start()
        web_server = DashboardServer(daemon, port=args.web)
        web_server.start()
        # Now launch TUI connected to the daemon
        from nuxtube.tui import NuxTubeTUI
        tui = NuxTubeTUI(config)
        tui.daemon = daemon  # Wire TUI to use the daemon
        tui.run()
        daemon.stop()
        web_server.stop()
        sys.exit(0)

    # --- Default: launch TUI ---
    config = load_or_setup(args.config)

    from nuxtube.tui import NuxTubeTUI
    tui = NuxTubeTUI(config)
    tui.run()


if __name__ == "__main__":
    main()
