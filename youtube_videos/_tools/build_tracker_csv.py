#!/usr/bin/env python3
"""
build_tracker_csv.py — Build a Google-Sheets-ready tracker CSV for the youtube_videos library.

Scans every <category>/<slug>/metadata.json and emits master_tracker.csv with one row per video.
The CSV uses Google Sheets formulas (=IMAGE, =HYPERLINK) so thumbnails render inline and Title /
Channel / Folder are clickable when you paste into Sheets.

Usage:
  python3 _tools/build_tracker_csv.py                 # rebuild master_tracker.csv from scratch
  python3 _tools/build_tracker_csv.py --print         # also print to stdout (copy-paste)

The archiver (archive_video.py --csv-append) appends a single row incrementally; this script
rebuilds the whole file from the current folder contents (use it to regenerate / repair).

NOTE ON IMAGES IN SHEETS:
  =IMAGE() needs a PUBLIC http(s) URL. The YouTube thumbnail URL is public, so it renders.
  Your own screenshots are local files and will NOT render via =IMAGE until hosted publicly
  (e.g. GitHub raw URL or a public Drive link). The "Top Screenshot" column is therefore left as a
  local path by default; swap in a public base URL with --screenshot-base to make it render.
"""
import argparse, csv, json, os, sys, glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # youtube_videos/

HEADERS = [
    "Thumbnail", "Title", "Channel", "Category", "Duration", "Video ID",
    "Status", "Date Processed", "Processed By",
    "Transcript?", "Segments", "Screenshots", "Clips",
    "Top Screenshot", "Folder", "Reviewed?", "Rating", "Key Takeaway", "Notes",
]


def esc(s):
    """Escape a string for safe embedding inside a Sheets formula string literal."""
    return str(s).replace('"', '""')


def find_videos():
    rows = []
    for meta_path in sorted(glob.glob(os.path.join(BASE, "*", "*", "metadata.json"))):
        try:
            m = json.load(open(meta_path))
        except Exception:
            continue
        vdir = os.path.dirname(meta_path)
        rel = os.path.relpath(vdir, BASE)
        media = m.get("media", {})

        thumb = m.get("thumbnail_url", "")
        url = m.get("url", "")
        title = m.get("title", "")
        channel = m.get("channel", "")
        channel_url = m.get("channel_url", "")

        # top screenshot: first ok entry in screenshots manifest, else blank
        top_shot = ""
        sm = os.path.join(vdir, "_screenshots_manifest.json")
        if os.path.exists(sm):
            try:
                shots = json.load(open(sm))
                ok = [s for s in shots if s.get("ok")]
                if ok:
                    top_shot = os.path.join(rel, ok[len(ok) // 2]["screenshot"])  # middle-ish
            except Exception:
                pass

        date = (m.get("fetched_at", "") or "")[:10]
        scount = media.get("screenshot_count", 0)
        ccount = media.get("clip_count", 0)
        has_transcript = "Yes" if m.get("segment_count", 0) else "No"

        rows.append({
            "Thumbnail": f'=IMAGE("{esc(thumb)}")' if thumb else "",
            "Title": f'=HYPERLINK("{esc(url)}","{esc(title)}")' if url else title,
            "Channel": f'=HYPERLINK("{esc(channel_url)}","{esc(channel)}")' if channel_url else channel,
            "Category": m.get("category", ""),
            "Duration": m.get("duration", ""),
            "Video ID": m.get("video_id", ""),
            "Status": "Done",
            "Date Processed": date,
            "Processed By": "Hermes",
            "Transcript?": has_transcript,
            "Segments": m.get("segment_count", 0),
            "Screenshots": scount,
            "Clips": ccount,
            "Top Screenshot": top_shot,  # local path; see --screenshot-base
            "Folder": f'=HYPERLINK("{esc(rel)}","open")',
            "Reviewed?": "",
            "Rating": "",
            "Key Takeaway": "",
            "Notes": "",
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", dest="do_print")
    ap.add_argument("--screenshot-base", default="",
                    help="Public base URL to make Top Screenshot render via =IMAGE "
                         "(e.g. https://raw.githubusercontent.com/you/repo/main/youtube_videos)")
    args = ap.parse_args()

    rows = find_videos()
    if args.screenshot_base:
        base = args.screenshot_base.rstrip("/")
        for r in rows:
            ts = r["Top Screenshot"]
            if ts:
                r["Top Screenshot"] = f'=IMAGE("{base}/{esc(ts)}")'

    out = os.path.join(BASE, "master_tracker.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {out} ({len(rows)} video rows).")
    if args.do_print:
        print("\n----- COPY BELOW INTO GOOGLE SHEETS -----\n")
        print(open(out).read())


if __name__ == "__main__":
    main()
