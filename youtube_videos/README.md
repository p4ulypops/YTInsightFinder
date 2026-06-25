# 📚 YouTube Videos — Archive Library

> **Searchable transcripts + key-moment screenshots + clips + LLM notes + HTML viewers. One folder per video. Everything in one place.**

---

## Folder structure

Each archived video lives at `<category>/<slug>/`:

```
ai-agents/claude-code-agentic-os/
├── metadata.json            ← title, channel, duration, thumbnail, chapters, heatmap
├── transcript.md            ← timestamped + plain text transcript
├── key-points.json          ← LLM-extracted lessons (structured)
├── key-points.md            ← same, human-readable
├── omni.json                ← everything merged into one portable JSON
├── viewer.html              ← open this in browser to browse the archive
├── screenshots/
│   ├── 01m23s.jpg
│   └── 04m55s.jpg
├── clips/
│   ├── seg_00_01m20s.mp4
│   └── seg_01_04m52s.mp4
├── _screenshots_manifest.json
└── _clips_manifest.json
```

---

## Open a video in the viewer

**Option 1 — local server (recommended):**
```bash
cd ai-agents/claude-code-agentic-os
python3 -m http.server 9000
# Open: http://localhost:9000/viewer.html
```

**Option 2 — baked viewer (works directly from file://):**
```bash
# From project root:
python3 nuxtube.py --viewer ./youtube_videos/ai-agents/claude-code-agentic-os --bake
# Then just double-click viewer.html
```

**Option 3 — web dashboard (all videos in one place):**
```bash
python3 nuxtube.py --daemon --web 8080
# Open: http://localhost:8080
# Archive browser shows all videos with thumbnails + search
```

---

## Generate OmniFiles / Viewers for all existing videos

```bash
# From project root:
python3 nuxtube.py --omni-all            # writes omni.json to every folder
python3 nuxtube.py --viewer-all          # writes live viewer.html to every folder
python3 nuxtube.py --viewer-all --bake   # baked (self-contained) viewers
```

---

## Archive a new video

```bash
# From project root:
python3 nuxtube.py --archive "https://youtube.com/watch?v=VIDEO_ID"
python3 nuxtube.py --archive "https://youtube.com/watch?v=VIDEO_ID" --category coding
```

Or just launch the TUI and queue it from there:
```bash
python3 nuxtube.py
# Press 'a' to add a URL, 'o' for settings
```

---

## Categories

| Category | What goes here |
|----------|---------------|
| `ai-agents` | AI agents, orchestration, LLMs, Claude, Hermes |
| `coding` | Python, JS, APIs, debugging, dev tools |
| `productivity` | Workflows, Notion, Obsidian, PKM |
| `business` | Revenue, startups, clients, agencies |
| `seo` | Search, keywords, ranking, backlinks |
| `marketing` | Ads, funnels, audience, campaigns |
| `design` | UI, UX, Figma, CSS |
| `uncategorized` | Auto-assigned when nothing matches |

Auto-classification uses keyword scoring against the title + transcript. Tune in `config.yaml → categories`.

---

## master_tracker.csv

Every archived video gets a row with: thumbnail (image formula), title (hyperlink), channel, category, duration, screenshots count, clips count, folder path, date, status.

Google Sheets: `File → Import → master_tracker.csv` — thumbnails and links render automatically.
