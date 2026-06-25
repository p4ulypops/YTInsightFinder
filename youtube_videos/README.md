# YouTube Videos — Transcript & Visual Reference Library

This folder archives YouTube videos as **searchable transcripts plus the actual on-screen
visuals** (screenshots + short clips), organised by category. Every video the speaker says
"as you can see…", "look at this chart…", "if you look here…" gets that exact frame captured.

---

## TL;DR — one command does everything

```
python3 _tools/archive_video.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

That single command performs the **entire workflow** described below: fetches the transcript,
pulls metadata, auto-picks a category, downloads the video to a temp file, scans for visual-cue
phrases, grabs a screenshot at each one, extracts short clips of the high-value demo moments,
writes the manifests + `transcript.md` (with a Visual References table), and deletes the temp MP4.

Options:

```
python3 _tools/archive_video.py "<URL>" --category seo     # force a category folder
python3 _tools/archive_video.py "<URL>" --keep-video       # also save source.mp4 in the folder
python3 _tools/archive_video.py "<URL>" --no-media          # transcript + metadata only (no download)
python3 _tools/archive_video.py "<URL>" --max-clips 12      # cap number of clips (default 8)
```

After it runs, **eyeball the auto-selected clips** — the keyword heuristic is decent, but a human
(or a quick vision pass) picks better demo moments. Swap/add clips manually if needed (see step 4).

---

## What "exactly what you did" means (the full procedure)

This is the canonical procedure. The script automates it; this section is the source of truth if
you ever need to do it by hand, debug the script, or extend it.

### 1. Fetch transcript + metadata

Transcript via the **`youtube-content`** Hermes skill:

```
python3 /Users/user/.hermes/skills/media/youtube-content/scripts/fetch_transcript.py "<URL>"                       # JSON: video_id, segment_count, duration, full_text
python3 /Users/user/.hermes/skills/media/youtube-content/scripts/fetch_transcript.py "<URL>" --text-only --timestamps   # "M:SS text" lines
```

Title / channel / thumbnail via YouTube oEmbed:

```
curl -s "https://www.youtube.com/oembed?url=<URL>&format=json"
```

### 2. Decide the category & create the folder

Pick the category from the video's actual subject. **Add new categories freely** — create a new
lowercase, hyphenated folder (`seo`, `productivity`, `coding`, `marketing`, `business`, …) whenever
a video doesn't fit an existing one. Then:

```
<category>/<video-title-slug>/        # slug = lowercase title, punctuation stripped, hyphenated
```

### 3. Download the video (temp only)

```
cd /tmp && python3 -m yt_dlp --extractor-args "youtube:player_client=android" \
  -f "best[height<=720]/best" --merge-output-format mp4 -o "yt_<id>.%(ext)s" "<URL>"
```

If you hit `HTTP 403 Forbidden`, cycle the client: **android → ios → tv → web_safari → mweb**.
720p is plenty for screenshots and keeps the temp file small.

### 4. Find visual-cue moments → screenshots + clips

Scan the timestamped transcript for visual-cue phrases ("as you can see", "you can see",
"if you look", "look at this", "over here", "right here", "on the screen", "this dashboard",
"this chart", "according to", "as shown", …). **Collapse cues within ~8s** of each other into one
moment.

**Screenshots** — grab a frame **~3s after** each cue (lets the on-screen visual settle):

```
ffmpeg -y -ss <sec+3> -i /tmp/yt_<id>.mp4 -frames:v 1 -q:v 3 screenshots/NN_MMmSSs.jpg
```

**Clips** — only for genuinely high-value demos (dashboard reveals, diagrams, live walkthroughs,
charts/graphs). ~10–16s each, re-encoded small:

```
ffmpeg -y -ss <start> -i /tmp/yt_<id>.mp4 -t <dur> -c:v libx264 -preset veryfast -crf 26 \
  -c:a aac -b:a 96k -movflags +faststart clips/MMmSSs_<slug>.mp4
```

**Verify** screenshots actually show demo content (use the vision tool) — skip pure talking-head
frames where it matters.

### 5. Write manifests + transcript.md

- `_screenshots_manifest.json` — every screenshot: index, timestamp, cue, spoken context, path.
- `_clips_manifest.json` — every clip: start, duration, description, path.
- `transcript.md` — header block (channel, URL, duration, category, fetch date, thumbnail), then
  the timestamped transcript, then the plain-text transcript, then a **## Visual References**
  section: a "Key clips" list + a screenshots table linking each row to its YouTube deep-link
  (`<URL>&t=<sec>s`).
- `metadata.json` — sidecar with `title`, `video_id`, `url`, `channel`, `channel_url`, `category`,
  `duration`, `segment_count`, `thumbnail_url`, `fetched_at`, `source`, `files`, and a `media`
  block (screenshot/clip counts + manifest paths).

### 6. Clean up

**Delete the temp MP4** (`rm /tmp/yt_<id>.mp4`) unless `--keep-video` was requested. Only the small
JPGs + clips stay in the repo (a full video is ~1.6 MB screenshots + ~2.5 MB clips vs a 66 MB MP4).

---

## Folder structure

```
youtube_videos/
├── README.md                          ← this file (the convention + instructions)
├── _tools/
│   ├── archive_video.py               ← one-command archiver (runs the whole procedure)
│   ├── extract_keypoints.py           ← LLM-powered key-point extraction (human MD + machine JSON)
│   ├── batch_extract_keypoints.py     ← batch extract for all videos missing key points
│   ├── build_tracker_csv.py           ← builds master_tracker.csv for Google Sheets
│   └── GOOGLE_SHEETS_HANDOFF.md       ← paste-to-Gemini guide to build the tracker dashboard
├── master_tracker.csv                 ← Sheets-ready tracker (1 row per video, inline thumbnails)
└── <category>/                        ← e.g. ai-agents, seo, productivity ...
    └── <video-title-slug>/            ← one folder per video
        ├── transcript.md              ← MD transcript (timestamped + plain text + Visual References)
        ├── key-points.md              ← Human-readable key lessons (rich emoji markdown)
        ├── key-points.json            ← Machine-readable key lessons (structured JSON for AI)
        ├── metadata.json              ← metadata sidecar (title, channel, id, url, media counts)
        ├── screenshots/               ← JPG frame grabs at every visual-cue moment
        │   └── NN_MMmSSs.jpg
        ├── clips/                     ← short MP4 clips of high-value demo moments
        │   └── MMmSSs_<slug>.mp4
        ├── _screenshots_manifest.json ← index of every screenshot (timestamp, cue, context)
        └── _clips_manifest.json       ← index of every clip (start, duration, description)
```

---

## Key-Point Extraction

Every archived video gets two additional files with extracted lessons and key takeaways:

### `key-points.md` — Human-readable (rich emoji markdown)

Scannable, fun, emoji-rich. Organised by category with importance indicators (🔥 high / ⭐ medium / 💡 low).
Includes timestamps, tags, and a one-paragraph summary at the top.

### `key-points.json` — Machine-readable (structured JSON)

Optimised for AI ingestion. Each key point has: id, timestamp, category, title, lesson, tags, importance.
The file includes a `video` block with metadata and a `summary` field.

### Running extraction

```bash
# Extract for a single video
python3 _tools/extract_keypoints.py <category>/<video-slug>

# Batch extract for all videos missing key points
python3 _tools/batch_extract_keypoints.py
```

Key-point extraction is **on by default** when archiving new videos. Use `--no-key-points` to skip it:

```bash
python3 _tools/archive_video.py "<URL>" --no-key-points
```

The extraction uses `hermes -z` (one-shot LLM call) to analyse the transcript and pull out the most
actionable, concrete lessons — prioritising business, coding, and AI-agent insights over fluff.

---

## Batch mode

Multiple URLs? Run the archiver per URL (loop), or hand the list to Hermes and ask it to fan out
parallel agents — one per video — each running `_tools/archive_video.py`.

## Tracker spreadsheet (Google Sheets)

A watchable dashboard over the whole library lives in `master_tracker.csv` (one row per video,
inline thumbnails, clickable titles/channels/folders). Build / refresh it with:

```
python3 _tools/build_tracker_csv.py                    # rebuild from current folders
python3 _tools/archive_video.py "<URL>" --csv-append   # archive a video AND refresh the CSV
```

To build the live dashboard in Google Sheets with Gemini, follow `_tools/GOOGLE_SHEETS_HANDOFF.md`
(paste it into Gemini — it has the import steps, the dropdown/colour/summary prompts, and the note
on making your own screenshots render via a public URL).

---

## Dependencies (already installed on this machine)

- `youtube-transcript-api` — transcript fetch (`python3 -m pip install youtube-transcript-api`)
- `yt-dlp` — video download (`python3 -m pip install yt-dlp`; invoke as `python3 -m yt_dlp`)
- `ffmpeg` — screenshot + clip extraction

---

## Categories so far

- **ai-agents** — building/orchestrating AI agents, agent operating systems, agent tooling.
