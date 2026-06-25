# 🎬 NuxTube — YouTube Archive Pipeline & Playlist Watcher

> **Self-hosted YouTube video archiver with transcripts, screenshots, clips, LLM key-point extraction, and a live TUI dashboard.**

[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue?logo=python)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Rich TUI](https://img.shields.io/badge/TUI-Rich-yellow)](https://github.com/Textualize/rich)

---

## 📋 Table of Contents

- [What It Does](#-what-it-does)
- [Quick Start](#-quick-start)
- [TUI Dashboard](#-tui-dashboard)
- [CLI Usage](#-cli-usage)
- [Configuration](#-configuration)
- [Pipeline Stages](#-pipeline-stages)
- [Transcript SSL Fix](#-transcript-ssl-fix-3-tier-fallback)
- [Channel Watching — Disclaimer](#-channel-watching--disclaimer)
- [Middleware API](#-middleware-api)
- [Bug Fixes from v1](#-bug-fixes-from-v1)
- [Project Structure](#-project-structure)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🔥 What It Does

Give it a YouTube URL or playlist — it archives the **entire video experience**:

| Stage | Output | Description |
|-------|--------|-------------|
| 📝 **Transcript** | `transcript.md` | Timestamped + plain text, fetched via 3-tier fallback |
| 🏷️ **Metadata** | `metadata.json` | Title, channel, duration, thumbnail (via oEmbed) |
| 📹 **Video Download** | temp MP4 | 720p via yt-dlp, cycles clients on 403 |
| 📸 **Screenshots** | `screenshots/*.jpg` | Captured at every "as you can see…" visual cue |
| 🎞️ **Clips** | `clips/*.mp4` | Short clips of high-value demo moments |
| 🔑 **Key Points** | `key-points.md` + `.json` | LLM-extracted lessons, structured + human-readable |
| 📊 **Tracker** | `master_tracker.csv` | Google Sheets-ready CSV with formulas |

### 🆕 What's New in v2

- ✅ **Full TUI dashboard** — htop-style multi-panel live display with keyboard controls
- ✅ **Interactive first-run setup** — wizard asks questions, saves to `config.yaml`
- ✅ **3-tier SSL fallback** — transcript fetching bypasses Python's broken TLS stack
- ✅ **Thread-safe CSV** — no more corruption in parallel mode
- ✅ **Configurable pipeline stages** — pick which stages to run per video
- ✅ **Multiple playlist + channel support** — watch many sources simultaneously
- ✅ **1hr+ video fix** — timestamp parsing now handles `H:MM:SS` format
- ✅ **Broader clip context** — 5-segment window instead of 1 (fixes 44% zero-clip rate)
- ✅ **Middleware-ready** — importable as a Python library for automation

---

## 🚀 Quick Start

```bash
# Install dependencies
pip install rich pyyaml youtube-transcript-api yt-dlp

# Run (first launch = interactive setup wizard)
python3 nuxtube.py

# Or archive a single video quickly
python3 nuxtube.py --archive "https://www.youtube.com/watch?v=VIDEO_ID"
```

### 🐍 Python Version

| Version | Status | Notes |
|---------|--------|-------|
| 3.11+ | ✅ **Recommended** | No SSL issues, all features work |
| 3.9 | ⚠️ Works with fallback | curl tier handles LibreSSL/urllib3 issue |
| 3.8 | ❌ Not supported | Missing features |

---

## 🖥️ TUI Dashboard

Launch with `python3 nuxtube.py` — the dashboard stays on screen while the watcher runs:

```
┌─────────────────────────┬─────────────────────────┐
│  📺 Watch Status         │  ⚙️ Active Workers       │
│  ───────────────         │  ───────────────         │
│  PL  AI Agents playlist  │  W0 [=======   ] 58% [S] │
│  CH  Some Channel        │     Claude Code Agentic..│
│  Last check: 12:34       │  W1 [===       ] 25% [T] │
│  Queue: 3 waiting         │     How to build an OS..│
│  Archived: 27            │  W2 [idle]               │
├─────────────────────────┴─────────────────────────┤
│  ✅ Recently Completed                              │
│  Title                    Category   SS  Clips  OK  │
│  Claude Code Agentic OS   ai-agents  14     3   ✅ │
│  Every Level of Hermes... ai-agents  13     3   ✅ │
├────────────────────────────────────────────────────┤
│  📋 Live Log                                        │
│  [12:34:56] INFO  W0: Fetching transcript...       │
│  [12:34:58] OK    W0: Got 502 segments             │
│  [12:35:01] INFO  W0: Downloading video (720p)... │
├────────────────────────────────────────────────────┤
│  p:pause  r:retry  s:skip  n:now  q:quit  ?:help   │
└────────────────────────────────────────────────────┘
```

### ⌨️ Keyboard Controls

| Key | Action | Description |
|-----|--------|-------------|
| `p` | ⏸️ Pause/Resume | Pause or resume the playlist watcher |
| `r` | 🔄 Retry | Re-queue all failed videos |
| `s` | ⏭️ Skip | Skip the current video in the first active worker |
| `n` | 🔍 Check Now | Force an immediate playlist check (don't wait for poll) |
| `q` | 🚪 Quit | Gracefully shut down all workers and exit |
| `?` | ❓ Help | Show/hide the help overlay |
| `Tab` | ↔️ Focus | Switch focus between panels |
| `↑↓` | 🔄 Navigate | Move within the focused panel |

---

## 📐 CLI Usage

```bash
# Launch TUI (default)
python3 nuxtube.py

# Quick archive without TUI
python3 nuxtube.py --archive "https://youtube.com/watch?v=VIDEO_ID"

# Force a category
python3 nuxtube.py --archive URL --category coding

# Use a custom config file
python3 nuxtube.py --config /path/to/my-config.yaml

# Re-run interactive setup
python3 nuxtube.py --setup

# List videos in a playlist
python3 nuxtube.py --check-playlist "https://youtube.com/playlist?list=..."

# List videos in a channel (shows disclaimer first)
python3 nuxtube.py --check-channel "https://youtube.com/@SomeChannel"

# Show version
python3 nuxtube.py --version
```

---

## ⚙️ Configuration

On first run, the interactive setup wizard asks:

1. 📁 **Output directory** — where to save archives
2. 📺 **Playlist URLs** — YouTube playlists to watch
3. 📱 **Channel URLs** — with ⚠️ disclaimer (see below)
4. 🔧 **Pipeline stages** — which steps to run per video
5. 🧪 **Worker count** — parallel archive threads
6. 🔄 **Poll interval** — seconds between playlist checks
7. 🎨 **Keep source video** — retain MP4 after archiving

Settings are saved to `config.yaml`:

```yaml
output_dir: ./youtube_videos
sources:
  - url: "https://youtube.com/playlist?list=..."
    name: "AI Agents"
    type: playlist
    enabled: true
pipeline:
  stages: [transcript, metadata, download, screenshots, clips, keypoints, tracker]
  screenshot_offset: 3
  clip_duration: 16
  max_clips: 8
  max_height: 720
  keep_video: false
watch:
  poll_interval: 300
  max_workers: 3
  auto_archive: true
categories: [ai-agents, coding, productivity, business, seo, marketing, design]
```

---

## 🔧 Pipeline Stages

Each stage can be toggled in config. Stages run in order:

```
URL → [transcript] → [metadata] → [download] → [screenshots] → [clips] → [keypoints] → [tracker]
```

| Stage | Required | Description |
|-------|----------|-------------|
| `transcript` | ✅ Yes | Fetch transcript (3-tier SSL fallback) |
| `metadata` | Optional | Fetch title/channel/thumbnail via oEmbed |
| `download` | Optional | Download 720p MP4 via yt-dlp |
| `screenshots` | Needs download | ffmpeg screenshot at each visual cue |
| `clips` | Needs download | ffmpeg clip extraction for demo moments |
| `keypoints` | Optional | LLM extraction via `hermes -z` |
| `tracker` | Optional | Append to master_tracker.csv |

---

## 🔒 Transcript SSL Fix (3-Tier Fallback)

The original tool failed on 26/31 batch runs due to `urllib3 v2 + LibreSSL 2.8.3` on Python 3.9/macOS. NuxTube v2 uses a 3-tier fallback:

```
Tier 1: youtube-transcript-api    (Python requests → urllib3 → TLS)
         ↓ fails
Tier 2: yt-dlp subtitle extract   (yt-dlp → its own HTTP stack)
         ↓ fails
Tier 3: curl timedtext API         (bypasses Python TLS entirely!)
```

**Tier 3** uses `curl` to fetch YouTube's timedtext XML API directly, completely bypassing Python's broken TLS stack. This works even when Python's `ssl` module is compiled against LibreSSL.

---

## ⚠️ Channel Watching — Disclaimer

> **🚨 WARNING: Channel watching can be dangerous!**

| Risk | Details |
|------|---------|
| ⚡ **Overload** | Channels can have hundreds/thousands of videos. Each download = 50-200MB. A 500-video channel = **25-100GB** storage + bandwidth. |
| 💰 **Cost** | VPS bandwidth costs. API rate limits. Potential billing for excessive requests. |
| ⚠️ **Harm to YouTuber** | Mass-downloading can trigger YouTube anti-scraping protections, potentially getting the channel **restricted or flagged**. |
| ⚖️ **Legal/ToS** | Downloading may violate YouTube ToS. Re-distributing copyrighted content is **illegal**. |

**Only use channel watching for:**
- ✅ Your own channels
- ✅ Public domain content
- ✅ Content you have explicit permission to archive

The interactive setup shows this disclaimer before allowing channel sources.

---

## 🔌 Middleware API

NuxTube is designed to be importable as a library for automation:

```python
from nuxtube.config import Config
from nuxtube.archiver import ArchivePipeline

# Load config
config = Config.load("config.yaml")

# Create pipeline
pipeline = ArchivePipeline(config)

# Archive a single video
result = pipeline.archive("https://youtube.com/watch?v=...")

# Check result
print(result.status)        # "success" | "failed" | "partial" | "skipped"
print(result.folder)        # Path to the archived video folder
print(result.screenshot_count)
print(result.clip_count)

# With callbacks
def on_log(level, msg):
    print(f"[{level}] {msg}")

def on_progress(stage, cur, total, msg):
    print(f"  {stage}: {cur}/{total} — {msg}")

result = pipeline.archive(url, on_log=on_log, on_progress=on_progress)
```

> 🚧 **Middleware mode is designed but not yet fully built.** The core API is importable and functional. A full automation/middleware layer will be added in a future stage.

---

## 🐛 Bug Fixes from v1

The original `archive_video.py` had several critical bugs. All fixed in v2:

| # | Bug | Impact | Fix |
|---|-----|--------|-----|
| 1 | `parse_ts` regex broke on videos ≥ 1hr | Zero screenshots/clips for long videos | New regex handles `H:MM:SS` format |
| 2 | `batch_extract_keypoints.py` wrong BASE path | Script crashes immediately | Fixed path to use relative resolution |
| 3 | Parallel workers race on `master_tracker.csv` | CSV corruption | Thread-safe `TrackerCSV` with `threading.Lock` |
| 4 | Temp file collision in parallel mode | Workers clobber each other | `tempfile.mkstemp()` for unique paths |
| 5 | urllib3 v2 + LibreSSL = batch failures | 26/31 videos failed | 3-tier SSL fallback (curl bypasses Python TLS) |
| 6 | Temp MP4 not cleaned up on download failure | Disk leak | `finally` block always cleans up |
| 7 | yt-dlp fallback missing duration/segment_count | Metadata incomplete | Fallback now returns all fields |
| 8 | Status always "Done" in CSV | Misleading | Status reflects actual result |
| 9 | 44% of videos got 0 clips | Missing demo moments | Broader context window (5 segments, not 1) |
| 10 | Redundant double API call for transcript | Wasted requests | Single fetch, parse once |
| 11 | ffmpeg no timeout | Hung processes | 30-60s timeouts on all ffmpeg calls |
| 12 | `esc()` didn't escape backslashes | CSV formula errors | Proper escaping |
| 13 | Duplicate key-point IDs | JSON validation fails | Deduplication in extraction |
| 14 | `find_video_folder` missed categories | Can't find videos in custom categories | Dynamic category discovery |

---

## 📁 Project Structure

```
NeuroD-NuxTube/
├── nuxtube.py              # Entry point (CLI + TUI launcher)
├── config.yaml             # Generated by first-run setup (gitignored)
├── requirements.txt
├── LICENSE
├── README.md               # You are here
├── brainstorm_init          # Original PRD for parent AgenticOS project
├── nuxtube/                 # Python package
│   ├── __init__.py
│   ├── config.py            # Config dataclass + interactive setup wizard
│   ├── transcript.py        # 3-tier transcript fetching (SSL fix)
│   ├── media.py             # Video download, screenshots, clips
│   ├── keypoints.py         # LLM key-point extraction
│   ├── tracker.py           # Thread-safe CSV tracker
│   ├── archiver.py          # Pipeline orchestration + stage selection
│   ├── watcher.py           # Playlist/channel monitoring + disclaimers
│   └── tui.py               # Rich multi-panel dashboard
├── youtube_videos/          # Output directory (gitignored)
│   ├── _tools/              # Legacy v1 scripts (kept for reference)
│   ├── _test_data/          # 27 test videos from v1 (gitignored)
│   └── README.md            # Library README
```

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b my-feature`
3. Test with: `python3 nuxtube.py --archive "https://youtube.com/watch?v=dQw4w9WgXcQ"`
4. Submit a PR

### Running Tests

```bash
# Syntax check all modules
python3 -m py_compile nuxtube/*.py

# Test transcript fetching
python3 -c "from nuxtube.transcript import fetch_transcript; print(fetch_transcript('dQw4w9WgXcQ'))"

# Test single archive
python3 nuxtube.py --archive "https://youtube.com/watch?v=dQw4w9WgXcQ" --no-media
```

---

## 📜 License

[MIT](LICENSE) — © 2026 [p4ulypops](https://github.com/p4ulypops)

---

> ⚠️ **Disclaimer**: This tool is for archiving content you own or have permission to archive. Downloading YouTube videos may violate YouTube's Terms of Service. Always respect copyright and creator rights.
