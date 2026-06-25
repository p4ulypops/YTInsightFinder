# 🎬 NuxTube — YouTube Archive Pipeline

> **Give it a playlist. Get back transcripts, screenshots, clips, LLM key points, an OmniFile, and a beautiful HTML viewer. All automated. All self-hosted.**

[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue?logo=python)](https://python.org)
[![Rich TUI](https://img.shields.io/badge/TUI-Rich-yellow)](https://github.com/Textualize/rich)
[![v2.3](https://img.shields.io/badge/version-2.3-brightgreen)](https://github.com/p4ulypops/YTInsightFinder)

---

## What it actually does

Point NuxTube at a YouTube playlist (or drop in a single URL) — it captures the **entire video experience** and archives it locally. No cloud. No subscriptions. Just your data, your way.

| Stage | Output | What's happening |
|-------|--------|-----------------|
| 📝 **Transcript** | `transcript.md` | Timestamped + plain text via 3-tier SSL-safe fetch |
| 🏷️ **Metadata** | `metadata.json` | Title, channel, duration, thumbnail, chapters, heatmap |
| 🎯 **Smart moments** | (in metadata) | YouTube chapters + viewer heatmap + visual cues merged |
| 📥 **Segment download** | temp files | Only the key moments — not the full video (~25x less data) |
| 📸 **Screenshots** | `screenshots/*.jpg` | ffmpeg grab at every key moment |
| 🎞️ **Clips** | `clips/*.mp4` | Short videos of the high-value moments |
| 🧠 **Key Points** | `key-points.json` + `.md` | LLM-extracted lessons via `hermes -z` |
| 📊 **Tracker** | `master_tracker.csv` | Google Sheets-ready with image formulas |
| 🗂️ **OmniFile** | `omni.json` | Everything merged into one portable JSON |
| 🌐 **Viewer** | `viewer.html` | Beautiful HTML viewer for the whole archive |

---

## 🚀 Quick Start

```bash
# Install deps
pip install rich pyyaml youtube-transcript-api yt-dlp

# First launch = interactive setup wizard (asks questions, saves config.yaml)
python3 nuxtube.py

# Or just archive one video right now
python3 nuxtube.py --archive "https://www.youtube.com/watch?v=VIDEO_ID"

# See the TUI immediately with the test config
python3 nuxtube.py --config test_config.yaml
```

---

## 🖥️ TUI Dashboard

The whole thing runs in a live terminal dashboard — htop-style, 4 Hz refresh, full keyboard control.

```
┌─────────────────────────┬─────────────────────────┐
│  📺 Watch Status         │  ⚙️  Active Workers       │
│  ─────────────           │  ─────────────           │
│  PL  AI Agents           │  W0 [=======   ] 58% [S] │
│  CH  Tech Channel        │     Claude Code Agentic..│
│  Last check: 14:22       │  W1 [===       ] 25% [T] │
│  Queue:    3 waiting     │     How to build an OS.. │
│  Archived: 27            │  W2 [idle]               │
├─────────────────────────┴─────────────────────────┤
│  ✅ Recently Completed                              │
│  ▶ Claude Code Agentic OS   ai-agents  14   3  OK  │
│    Every Level of Hermes..  ai-agents  13   3  OK  │
├────────────────────────────────────────────────────┤
│  📋 Live Log                                        │
│  [14:22:01] OK    W0: OmniFile written: omni.json  │
│  [14:22:03] INFO  W1: Smart moments: 9 found       │
│  [14:22:05] OK    W1: Viewer: /ai-agents/viewer.ht │
├────────────────────────────────────────────────────┤
│  p:pause r:retry s:skip n:now a:add o:options      │
│  v:viewer g:omni G:all ?:help q:quit               │
└────────────────────────────────────────────────────┘
```

### ⌨️ Keyboard Controls

| Key | What it does |
|-----|-------------|
| `p` | Pause / resume the playlist watcher |
| `r` | Retry all failed videos |
| `s` | Skip current video in active worker |
| `n` | Force immediate playlist check |
| `a` | Add a YouTube URL to queue manually |
| `o` | **Open full options screen** (edit all settings live) |
| `g` | Generate OmniFile for selected completed video |
| `v` | Generate HTML viewer for selected video |
| `G` | Batch-generate viewers for **all** completed videos |
| `Tab` | Cycle panel focus (Watch → Workers → Completed → Log) |
| `↑↓` | Navigate completed list / scroll log |
| `Enter` | Open detail view for selected video |
| `?` | Help overlay |
| `q` | Quit gracefully |

---

## ⚙️ Options Screen (`o` key)

Full live settings editor — no need to touch config.yaml manually.

### Pipeline tab
Edit everything: capture mode, quality, key moment mode, max clips, durations, offsets, max height, keep video, segment download. Toggle each pipeline stage on/off individually.

### Watch tab
Poll interval, max workers, archive delay, archive timeout, auto-archive toggle.

### Sources tab
Add (`a`), delete (`d`), toggle enable/disable (`Enter`) for playlists and channels. URL type auto-detected.

### Categories tab
Add custom categories (`a`), delete (`d`). Used for auto-classification and folder structure.

**`s`** saves config.yaml. Tab switches between sections. Esc closes.

---

## 🗂️ OmniFile — One JSON to Rule Them All

Every archived video gets an `omni.json` — a single portable document containing everything:

```json
{
  "omni_version": "1.0",
  "metadata": { "title": "...", "channel": "...", "duration": "12:34", "thumbnail_url": "..." },
  "transcript": { "full_text": "...", "timestamped_text": "[0:01] Hello..." },
  "key_points": { "summary": "...", "key_points": [...] },
  "player_data": { "chapters": [...], "heatmap": [...] },
  "screenshots": [{ "timestamp": 123, "screenshot": "screenshots/02m03s.jpg", "ok": true }],
  "clips": [{ "timestamp": 123, "clip": "clips/seg_00.mp4", "ok": true }],
  "files": { "metadata.json": { "size": 2048 }, ... }
}
```

Auto-generated as the final pipeline stage. Also manual:

```bash
python3 nuxtube.py --omni ./youtube_videos/ai-agents/my-video
python3 nuxtube.py --omni-all
```

---

## 🌐 HTML Viewer

Each video gets a beautiful self-contained `viewer.html` — dark theme, fully interactive.

**Tabs:** Overview (stats, heatmap, screenshot gallery) · Transcript (searchable) · Key Points (summary + cards) · Clips (inline video player)

**Sidebar:** thumbnail, chapter list, key moments index.

**Three modes:**

| Mode | How | Use when |
|------|-----|----------|
| **Live** | Fetches `./omni.json` on load | Serve via `python3 -m http.server` from folder |
| **Baked** | omni.json embedded in HTML | Works from `file://`, send to anyone |
| **API** | Loads from `/api/omni?video_id=X` | Served via the web dashboard |

```bash
# Live viewer (fetches omni.json at runtime)
python3 nuxtube.py --viewer ./youtube_videos/ai-agents/my-video
# Serve: cd ./youtube_videos/ai-agents/my-video && python3 -m http.server 9000
# Open:  http://localhost:9000/viewer.html

# Baked viewer (self-contained, works from file://)
python3 nuxtube.py --viewer ./youtube_videos/ai-agents/my-video --bake

# Everything
python3 nuxtube.py --viewer-all
python3 nuxtube.py --viewer-all --bake

# From TUI: select a video in completed list → v (live) or b (baked)
```

---

## 📐 Full CLI Reference

```bash
# TUI (default)
python3 nuxtube.py
python3 nuxtube.py --config test_config.yaml

# Single video archive (no TUI)
python3 nuxtube.py --archive URL
python3 nuxtube.py --archive URL --category coding

# Headless daemon
python3 nuxtube.py --daemon
python3 nuxtube.py --daemon --web 8080

# TUI + web dashboard
python3 nuxtube.py --web 8080

# Query running daemon
python3 nuxtube.py --status

# Re-run setup wizard
python3 nuxtube.py --setup

# Inspect a playlist or channel
python3 nuxtube.py --check-playlist "https://youtube.com/playlist?list=..."
python3 nuxtube.py --check-channel "https://youtube.com/@Channel"

# OmniFile
python3 nuxtube.py --omni FOLDER
python3 nuxtube.py --omni-all

# Viewer
python3 nuxtube.py --viewer FOLDER
python3 nuxtube.py --viewer FOLDER --bake
python3 nuxtube.py --viewer-all
python3 nuxtube.py --viewer-all --bake

python3 nuxtube.py --version
```

---

## ⚙️ config.yaml

Generated on first run. Edit live via `o` in TUI or directly in the file.

```yaml
output_dir: ./youtube_videos

sources:
  - url: "https://youtube.com/playlist?list=PLxxx"
    name: "AI Agents"
    type: playlist   # playlist | channel | video
    enabled: true

pipeline:
  stages: [transcript, metadata, player_data, download, screenshots, clips, keypoints, tracker]
  capture_mode: full          # full | audio | transcript
  quality: 720p               # 480p | 720p | 1080p | best
  key_moment_mode: smart      # smart | cues
  screenshot_offset: 3
  clip_duration: 16
  clip_start_offset: -4
  max_clips: 8
  max_height: 720
  keep_video: false
  segment_download: true
  client_cycle: [android, ios, tv, web_safari, mweb]

watch:
  poll_interval: 300
  max_workers: 3
  archive_delay: 20
  archive_timeout: 600
  auto_archive: true

categories: [ai-agents, coding, productivity, business, seo, marketing, design, uncategorized]
```

---

## 🔧 Pipeline Stages

```
URL → transcript → metadata → player_data → download → screenshots → clips → keypoints → tracker → omni
```

| Stage | Required | Output |
|-------|----------|--------|
| `transcript` | ✅ | `transcript.md` — timestamped + plain text |
| `metadata` | Optional | `metadata.json` — title, channel, thumbnail |
| `player_data` | Optional | chapters + heatmap merged into metadata |
| `download` | Optional | segments around key moments via yt-dlp |
| `screenshots` | Needs download | `screenshots/*.jpg` via ffmpeg |
| `clips` | Needs download | `clips/*.mp4` via ffmpeg |
| `keypoints` | Optional | `key-points.json` + `.md` via LLM |
| `tracker` | Optional | row in `master_tracker.csv` |
| `omni` | Auto | `omni.json` — always runs after tracker |

---

## 🔒 Transcript SSL Fix — 3-Tier Fallback

Python 3.9 + macOS LibreSSL 2.8.3 + urllib3 v2 = transcript failures. Fixed with a waterfall:

```
Tier 1: youtube-transcript-api   (Python → urllib3 → TLS)
              ↓ fails
Tier 2: yt-dlp subtitle extract  (yt-dlp's own HTTP stack)
              ↓ fails
Tier 3: curl timedtext API        (bypasses Python TLS entirely)
```

Tier 3 hits YouTube's timedtext XML API directly via `curl`. Works everywhere.

---

## 🧠 Smart Key Moments

Three signals combined — not just "as you can see" phrases:

```
📑 YouTube chapters  +  🔥 Viewer heatmap peaks  +  📝 Transcript visual cues
         ↓                        ↓                           ↓
         └────────────────────────┴───────────────────────────┘
                                  ↓
                Score → deduplicate (10s window) → top N moments
```

Then `segment_download: true` downloads only those segments — typically ~25x less data than the full video.

---

## 🌐 Web Dashboard

Stdlib HTTP server. No bundler, no React, no external deps.

```bash
python3 nuxtube.py --daemon --web 8080
# Dashboard:   http://localhost:8080
# Viewer:      http://localhost:8080/viewer
# Videos API:  http://localhost:8080/api/videos
# OmniFile:    http://localhost:8080/api/omni?video_id=<id>
```

Features: live stats, worker progress, archive browser with thumbnails and filter, viewer links in completed table, "Generate All OmniFiles" button, queue from browser, color-coded log.

**REST API:**

| Method | Endpoint | Does |
|--------|----------|------|
| GET | `/viewer` | HTML viewer (API mode, loads by `?video_id=`) |
| GET | `/api/status` | Full daemon state |
| GET | `/api/videos` | All archived videos, newest first |
| GET | `/api/omni?video_id=X` | OmniFile JSON + `_web_base_url` for media |
| GET | `/files/<path>` | Static screenshots/clips from output dir |
| GET | `/api/results` | Recent archive results |
| GET | `/api/health` | Health check |
| POST | `/api/queue` | Add URL `{"url": "..."}` |
| POST | `/api/pause` | Pause watcher |
| POST | `/api/resume` | Resume watcher |
| POST | `/api/retry` | Retry all failed |
| POST | `/api/skip` | Skip worker `{"worker": 0}` |
| POST | `/api/check` | Force playlist check |
| POST | `/api/generate-omni` | Batch generate OmniFiles (background) |

---

## 🎥 Capture Modes

| Mode | Downloads | Output | Use for |
|------|-----------|--------|---------|
| `full` | Video segments | Screenshots + clips + transcript + keypoints | Default |
| `audio` | Audio only | MP3 + transcript + keypoints | Podcasts, talks |
| `transcript` | Nothing | Transcript + metadata + keypoints | Fast research |

---

## 🔌 Middleware API

Fully importable as a Python library.

```python
from nuxtube.config import Config
from nuxtube.archiver import ArchivePipeline

pipeline = ArchivePipeline(Config.load("config.yaml"))
result = pipeline.archive("https://youtube.com/watch?v=...")

print(result.status)           # success | failed | partial | skipped
print(result.folder)           # /path/to/archive/folder
print(result.screenshot_count)
```

```python
from nuxtube.middleware import NuxTubeDaemon

daemon = NuxTubeDaemon(Config.load("config.yaml"))
daemon.start()

daemon.queue_url("https://youtube.com/watch?v=...")
status = daemon.status()

daemon.subscribe(lambda event, data: print(event, data["title"]))
daemon.stop()
```

```python
from nuxtube.omni import write_omni
from nuxtube.viewer import generate_viewer

write_omni("/path/to/folder")
generate_viewer("/path/to/folder")               # live
generate_viewer("/path/to/folder", bake=True)    # baked
```

---

## ⚠️ Channel Watching — Read This First

| Risk | What actually happens |
|------|-----------------------|
| ⚡ **Overload** | 500-video channels = 25–100GB of storage |
| 💰 **Cost** | VPS bandwidth costs real money |
| ⚠️ **YouTuber harm** | Mass downloading can trigger protections on the channel |
| ⚖️ **Legal** | Downloading may violate YouTube ToS. Redistribution is illegal |

Only use channel watching for: your own channels, public domain content, or content you have explicit permission to archive. Setup wizard requires confirmation before adding channel sources.

---

## 📁 Project Structure

```
NeuroD-NuxTube/
├── nuxtube.py              # Entry point — CLI, TUI, daemon
├── config.yaml             # Your settings (gitignored)
├── test_config.yaml        # Minimal config — try the TUI now
├── nuxtube/
│   ├── __init__.py         # Version (2.3.0)
│   ├── config.py           # Config dataclass + setup wizard
│   ├── transcript.py       # 3-tier transcript fetch + SSL fix
│   ├── player_data.py      # YouTube chapters + heatmap + segment download
│   ├── media.py            # yt-dlp download, ffmpeg screenshots + clips
│   ├── keypoints.py        # LLM key-point extraction via hermes
│   ├── tracker.py          # Thread-safe CSV tracker
│   ├── archiver.py         # Pipeline orchestration — 9 stages
│   ├── watcher.py          # Playlist/channel monitoring
│   ├── middleware.py       # Headless daemon — workers + status API
│   ├── dashboard.py        # Web dashboard + REST API (stdlib only)
│   ├── tui.py              # Rich TUI — options, navigation, detail view
│   ├── omni.py             # OmniFile generator
│   └── viewer.py           # HTML viewer generator
└── youtube_videos/         # Archive output (gitignored)
    └── <category>/<slug>/
        ├── metadata.json
        ├── transcript.md
        ├── key-points.json + .md
        ├── omni.json           ← auto-generated
        ├── viewer.html         ← auto-generated or via --viewer
        ├── screenshots/*.jpg
        ├── clips/*.mp4
        ├── _screenshots_manifest.json
        └── _clips_manifest.json
```

---

## 🐛 Bugs Fixed from v1

| Bug | Impact | Fix |
|-----|--------|-----|
| `parse_ts` broke on videos ≥ 1hr | Zero screenshots for long videos | Handles `H:MM:SS` |
| Wrong BASE path in batch script | Crash on startup | Fixed path resolution |
| Parallel workers race on CSV | File corruption | `threading.Lock` on all writes |
| Temp file collision in parallel | Workers clobber each other | `tempfile.mkstemp()` |
| urllib3 v2 + LibreSSL failures | 26/31 videos failed | 3-tier SSL fallback |
| Temp MP4 not cleaned on failure | Disk leak | `finally` block always runs |
| Status always "Done" in CSV | Misleading tracking | Reflects actual result |
| 44% of videos got 0 clips | Missing demo moments | 5-segment context window |
| `subprocess` missing in archiver | Crash in segment+screenshot mode | Top-level import |
| Redundant double transcript fetch | Wasted API calls | Single fetch |
| ffmpeg no timeout | Hung processes | 30–60s timeouts everywhere |

---

## 🤝 Contributing

```bash
# Syntax check
python3 -m py_compile nuxtube/*.py

# Test a single archive
python3 nuxtube.py --archive "https://youtube.com/watch?v=dQw4w9WgXcQ"

# Try the TUI
python3 nuxtube.py --config test_config.yaml
```

PRs welcome. No new dependencies unless they genuinely earn their place.

---

## 📜 License

[MIT](LICENSE) — © 2026 [p4ulypops](https://github.com/p4ulypops)

---

> ⚠️ **Disclaimer:** For archiving content you own or have permission to archive. Respect creator rights and YouTube ToS.
