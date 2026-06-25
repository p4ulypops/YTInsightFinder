#!/usr/bin/env python3
"""
archive_video.py — One-command YouTube archiver for the youtube_videos library.

Does exactly what was done by hand for the first video:
  1. Fetches transcript (timestamped + plain) via the youtube-content skill.
  2. Fetches title/channel/thumbnail via YouTube oEmbed.
  3. Creates  <category>/<title-slug>/  (category auto-suggested, override with --category).
  4. Writes transcript.md (header + timestamped + plain text + Visual References) and metadata.json.
  5. Downloads the video to a temp file (android client, 720p; cycles clients on 403).
  6. Scans the transcript for visual-cue phrases, collapses them, and grabs a screenshot
     ~3s after each cue into screenshots/.
  7. Extracts short clips for the highest-value demo moments into clips/ (heuristic; review after).
  8. Writes _screenshots_manifest.json + _clips_manifest.json.
  9. Deletes the temp MP4 unless --keep-video is passed.

Usage:
  python3 _tools/archive_video.py "https://www.youtube.com/watch?v=VIDEO_ID"
  python3 _tools/archive_video.py "<URL>" --category seo
  python3 _tools/archive_video.py "<URL>" --keep-video        # keep source.mp4 in the video folder
  python3 _tools/archive_video.py "<URL>" --no-media          # transcript + metadata only (no download)

Dependencies (already installed on this machine): youtube-transcript-api, yt-dlp, ffmpeg.
"""
import argparse, json, os, re, subprocess, sys, tempfile
from datetime import datetime

SKILL = "/Users/user/.hermes/skills/media/youtube-content/scripts/fetch_transcript.py"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # youtube_videos/

VISUAL_CUES = [
    "as you can see", "you can see", "if you look", "look at this", "look here",
    "over here", "right here", "this section", "this is the", "you'll see",
    "if you have a look", "have a look", "on the screen", "shown here",
    "this dashboard", "this is what", "for example, if i", "let's go over",
    "you can preview", "we can see", "this website", "let me show you",
    "i'll show you", "as you saw", "this whole system", "this setup",
    "look at the", "this chart", "this graph", "according to", "as shown",
    "down here", "up here", "this part", "this page", "this screen",
]

# Keywords that suggest a high-value demo moment worth a clip (matched against cue context).
CLIP_KEYWORDS = [
    "dashboard", "diagram", "blueprint", "layer", "graph", "workspace",
    "system", "walk you through", "walkthrough", "show you", "demo",
    "preview", "mission control", "knowledge", "chart", "results", "example",
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def slugify(s):
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")[:80]


def fmt(sec):
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_file(sec):
    return f"{sec // 60:02d}m{sec % 60:02d}s"


def suggest_category(title, text):
    t = (title + " " + text[:2000]).lower()
    table = [
        ("ai-agents", ["agent", "operating system", "orchestrat", "autonomous", "llm", "claude", "hermes"]),
        ("seo", ["seo", "keyword", "rank", "backlink", "search console", "serp"]),
        ("coding", ["code", "python", "javascript", "react", "api", "function", "debug"]),
        ("productivity", ["productivity", "workflow", "notion", "obsidian", "note-taking", "second brain"]),
        ("marketing", ["marketing", "ads", "funnel", "audience", "campaign", "brand"]),
        ("business", ["business", "revenue", "startup", "client", "agency", "profit"]),
    ]
    best, score = "uncategorized", 0
    for cat, kws in table:
        s = sum(t.count(k) for k in kws)
        if s > score:
            best, score = cat, s
    return best


def fetch_transcript_ytdlp(url, vid):
    """Fallback: fetch auto-subs via yt-dlp and parse VTT into transcript format."""
    import glob
    tmpdir = tempfile.mkdtemp(prefix="ytdlp_sub_")
    prefix = os.path.join(tmpdir, "sub")
    r = run(["yt-dlp", "--write-auto-sub", "--write-sub", "--sub-lang", "en,en-US,en-GB",
             "--skip-download", "--sub-format", "vtt", "-o", prefix, url],
            timeout=60)
    vtt_files = glob.glob(prefix + "*.vtt")
    if not vtt_files:
        raise RuntimeError(f"yt-dlp subtitle fetch failed: {r.stderr[:300]}")
    segments = []
    seen_texts = set()
    with open(vtt_files[0], "r", encoding="utf-8") as f:
        buf_text, buf_start, buf_dur = [], None, 0.0
        for line in f:
            line = line.strip()
            ts_match = re.match(r"(\d+):(\d{2}):(\d{2})\.(\d+)\s*-->\s*(\d+):(\d{2}):(\d{2})\.(\d+)", line)
            if ts_match:
                if buf_text and buf_start is not None:
                    text = " ".join(buf_text).strip()
                    if text and text not in seen_texts:
                        seen_texts.add(text)
                        segments.append({"text": text, "start": buf_start, "duration": buf_dur})
                h, m, s, ms = int(ts_match.group(1)), int(ts_match.group(2)), int(ts_match.group(3)), int(ts_match.group(4))
                eh, em, es, ems = int(ts_match.group(5)), int(ts_match.group(6)), int(ts_match.group(7)), int(ts_match.group(8))
                buf_start = h * 3600 + m * 60 + s + ms / 1000.0
                end = eh * 3600 + em * 60 + es + ems / 1000.0
                buf_dur = end - buf_start
                buf_text = []
            elif line and not line.startswith("WEBVTT") and not line.startswith("NOTE") and not re.match(r"^[a-zA-Z-]+:", line) and not line.isdigit():
                clean = re.sub(r"<[^>]+>", "", line)
                if clean.strip():
                    buf_text.append(clean.strip())
        if buf_text and buf_start is not None:
            text = " ".join(buf_text).strip()
            if text and text not in seen_texts:
                segments.append({"text": text, "start": buf_start, "duration": buf_dur})
    for f in glob.glob(tmpdir + "/*"):
        os.remove(f)
    os.rmdir(tmpdir)
    if not segments:
        raise RuntimeError("yt-dlp fallback produced no segments")
    full_text = " ".join(s["text"] for s in segments)
    ts_lines = []
    for s in segments:
        total = int(s["start"])
        mm, ss = divmod(total, 60)
        ts_lines.append(f"{mm}:{ss:02d} {s['text']}")
    ts_text = "\n".join(ts_lines)
    core_json = {
        "video_id": vid, "language": "en", "segments": segments,
        "full_text": full_text, "timestamped_text": ts_text,
    }
    return core_json, ts_text


def fetch_transcript(url):
    vid = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    vid = vid.group(1) if vid else url[:11]
    core = run(["python3", SKILL, url])
    if core.returncode != 0 or not core.stdout.strip():
        try:
            return fetch_transcript_ytdlp(url, vid)
        except Exception as e2:
            sys.exit(f"Transcript fetch failed (API + yt-dlp): {core.stderr[:200]} | {e2}")
    core_json = json.loads(core.stdout)
    if "error" in core_json:
        try:
            return fetch_transcript_ytdlp(url, vid)
        except Exception as e2:
            sys.exit(f"Transcript fetch failed: {core_json['error'][:200]} | yt-dlp: {e2}")
    ts = run(["python3", SKILL, url, "--text-only", "--timestamps"])
    return core_json, ts.stdout.strip()


def fetch_oembed(url):
    r = run(["curl", "-s", f"https://www.youtube.com/oembed?url={url}&format=json"])
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}


def parse_ts(ts_text):
    out = []
    for ln in ts_text.splitlines():
        m = re.match(r"^(\d+):(\d{2})\s+(.*)$", ln)
        if m:
            out.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3)))
    return out


def find_cue_moments(lines):
    hits = []
    for sec, txt in lines:
        low = txt.lower()
        for c in VISUAL_CUES:
            if c in low:
                hits.append((sec, c, txt))
                break
    collapsed = []
    for sec, c, txt in hits:
        if collapsed and sec - collapsed[-1][0] < 8:
            continue
        collapsed.append((sec, c, txt))
    return collapsed


def download_video(url, dest):
    for client in ["android", "ios", "tv", "web_safari", "mweb"]:
        run(["rm", "-f", dest])
        r = run(["python3", "-m", "yt_dlp", "--extractor-args",
                 f"youtube:player_client={client}",
                 "-f", "best[height<=720]/best", "--merge-output-format", "mp4",
                 "-o", dest, url])
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return client
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--category", help="Override auto-detected category folder")
    ap.add_argument("--keep-video", action="store_true", help="Keep source.mp4 in the video folder")
    ap.add_argument("--no-media", action="store_true", help="Transcript + metadata only (skip download/screenshots/clips)")
    ap.add_argument("--max-clips", type=int, default=8)
    ap.add_argument("--csv-append", action="store_true",
                    help="Append/refresh this video's row in master_tracker.csv after archiving")
    ap.add_argument("--key-points", action="store_true", default=True,
                    help="Extract key-points.md + key-points.json via LLM (default: on)")
    ap.add_argument("--no-key-points", action="store_false", dest="key_points",
                    help="Skip key-point extraction")
    args = ap.parse_args()

    core, ts_text = fetch_transcript(args.url)
    oembed = fetch_oembed(args.url)
    vid = core["video_id"]
    full_text = core["full_text"]
    title = oembed.get("title", vid)
    channel = oembed.get("author_name", "unknown")
    channel_url = oembed.get("author_url", "")
    thumb = oembed.get("thumbnail_url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
    duration = core.get("duration", "?")
    seg_count = core.get("segment_count", 0)

    category = args.category or suggest_category(title, full_text)
    slug = slugify(title)
    vdir = os.path.join(BASE, category, slug)
    os.makedirs(vdir, exist_ok=True)

    now = datetime.now().astimezone()
    iso = now.isoformat()
    nice = now.strftime("%Y-%m-%d %H:%M %Z")

    lines = parse_ts(ts_text)
    moments = find_cue_moments(lines)

    shots_manifest, clips_manifest = [], []

    if not args.no_media:
        tmp = os.path.join(tempfile.gettempdir(), f"yt_{vid}.mp4")
        client = download_video(args.url, tmp)
        if not client:
            print("WARNING: video download failed (403 on all clients?) — skipping media.", file=sys.stderr)
        else:
            shots_dir = os.path.join(vdir, "screenshots")
            clips_dir = os.path.join(vdir, "clips")
            os.makedirs(shots_dir, exist_ok=True)
            os.makedirs(clips_dir, exist_ok=True)

            # Screenshots at every cue moment (+3s settle)
            for i, (sec, cue, txt) in enumerate(moments, 1):
                name = f"{i:02d}_{fmt_file(sec)}.jpg"
                p = os.path.join(shots_dir, name)
                run(["ffmpeg", "-y", "-ss", str(sec + 3), "-i", tmp,
                     "-frames:v", "1", "-q:v", "3", p])
                shots_manifest.append({
                    "index": i, "timestamp_sec": sec, "timestamp": fmt(sec),
                    "shot_at_sec": sec + 3, "cue": cue, "context": txt,
                    "screenshot": f"screenshots/{name}",
                    "ok": os.path.exists(p) and os.path.getsize(p) > 0,
                })

            # Clips for highest-value demo moments (score by clip keywords in context)
            scored = []
            for sec, cue, txt in moments:
                low = txt.lower()
                score = sum(1 for k in CLIP_KEYWORDS if k in low)
                if score > 0:
                    scored.append((score, sec, txt))
            scored.sort(key=lambda x: (-x[0], x[1]))
            chosen, used = [], []
            for score, sec, txt in scored:
                if any(abs(sec - u) < 25 for u in used):
                    continue
                chosen.append((sec, txt))
                used.append(sec)
                if len(chosen) >= args.max_clips:
                    break
            chosen.sort()
            for sec, txt in chosen:
                start = max(0, sec - 4)
                dur = 16
                kslug = slugify(" ".join(txt.split()[:5])) or "clip"
                name = f"{fmt_file(start)}_{kslug}.mp4"
                p = os.path.join(clips_dir, name)
                run(["ffmpeg", "-y", "-ss", str(start), "-i", tmp, "-t", str(dur),
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                     "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", p])
                clips_manifest.append({
                    "start": fmt(start), "start_sec": start, "duration_sec": dur,
                    "clip": f"clips/{name}", "description": txt[:120],
                    "ok": os.path.exists(p) and os.path.getsize(p) > 0,
                })

            if args.keep_video:
                run(["cp", tmp, os.path.join(vdir, "source.mp4")])
            run(["rm", "-f", tmp])

    # metadata.json
    meta = {
        "title": title, "video_id": vid, "url": args.url,
        "channel": channel, "channel_url": channel_url, "category": category,
        "duration": duration, "segment_count": seg_count, "thumbnail_url": thumb,
        "fetched_at": iso, "source": "youtube-transcript-api",
        "files": {"transcript": "transcript.md", "metadata": "metadata.json"},
    }
    if shots_manifest or clips_manifest:
        meta["media"] = {
            "screenshots_dir": "screenshots/", "screenshot_count": len(shots_manifest),
            "clips_dir": "clips/", "clip_count": len(clips_manifest),
            "screenshots_manifest": "_screenshots_manifest.json",
            "clips_manifest": "_clips_manifest.json",
        }
    json.dump(meta, open(os.path.join(vdir, "metadata.json"), "w"), indent=2, ensure_ascii=False)
    if shots_manifest:
        json.dump(shots_manifest, open(os.path.join(vdir, "_screenshots_manifest.json"), "w"), indent=2, ensure_ascii=False)
    if clips_manifest:
        json.dump(clips_manifest, open(os.path.join(vdir, "_clips_manifest.json"), "w"), indent=2, ensure_ascii=False)

    # transcript.md
    def ytlink(sec):
        return f"{args.url}&t={sec}s"

    md = [f"# {title}", "",
          f"> **Channel:** [{channel}]({channel_url})",
          f"> **Video:** [{args.url}]({args.url})",
          f"> **Duration:** {duration} · **Segments:** {seg_count}",
          f"> **Category:** {category}",
          f"> **Fetched:** {nice}", "",
          f"![thumbnail]({thumb})", "", "---", "",
          "## Transcript (timestamped)", "", ts_text, "", "---", "",
          "## Transcript (plain text)", "", full_text]

    if shots_manifest or clips_manifest:
        md += ["", "---", "", "## Visual References", "",
               "Moments where the speaker points at something on screen. Screenshots captured ~3s "
               "after the cue so the visual has settled. Click a timestamp to jump to that moment on YouTube.", ""]
        if clips_manifest:
            md += ["### Key clips (high-value demos)", ""]
            for c in clips_manifest:
                md.append(f"- **[{c['start']}]({ytlink(c['start_sec'])})** — {c['description']}  ·  `{c['clip']}`")
            md.append("")
        if shots_manifest:
            md += ["### Screenshots (all visual-cue moments)", "",
                   "| # | Time | Cue | What the speaker said | File |",
                   "|---|------|-----|-----------------------|------|"]
            for s in shots_manifest:
                ctx = s["context"].replace("|", "\\|")
                md.append(f"| {s['index']} | [{s['timestamp']}]({ytlink(s['timestamp_sec'])}) | _{s['cue']}_ | {ctx} | `{s['screenshot']}` |")

    open(os.path.join(vdir, "transcript.md"), "w").write("\n".join(md) + "\n")

    if args.csv_append:
        try:
            tools_dir = os.path.dirname(os.path.abspath(__file__))
            subprocess.run(["python3", os.path.join(tools_dir, "build_tracker_csv.py")], check=False)
            print("Refreshed master_tracker.csv")
        except Exception as e:
            print(f"WARNING: could not refresh master_tracker.csv: {e}", file=sys.stderr)

    if args.key_points:
        try:
            tools_dir = os.path.dirname(os.path.abspath(__file__))
            subprocess.run(["python3", os.path.join(tools_dir, "extract_keypoints.py"), vdir],
                           check=False, timeout=180)
        except Exception as e:
            print(f"WARNING: key-point extraction failed: {e}", file=sys.stderr)

    print(f"Done: {vdir}")
    print(f"  category:    {category}")
    print(f"  screenshots: {len(shots_manifest)}")
    print(f"  clips:       {len(clips_manifest)}")
    print("Review the auto-selected clips — the heuristic is decent but a human eye is better.")


if __name__ == "__main__":
    main()
