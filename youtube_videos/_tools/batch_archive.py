#!/usr/bin/env python3
"""
batch_archive.py — Run archive_video.py on a list of URLs in parallel batches.

Usage:
  python3 _tools/batch_archive.py --batch-size 5 --url-file urls.txt
  python3 _tools/batch_archive.py --batch-size 5 URL1 URL2 URL3 ...
  python3 _tools/batch_archive.py --batch-size 5 --playlist PLAYLIST_URL
"""
import argparse, subprocess, sys, os, time, signal
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_video.py")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# All 30 videos from playlist PLnz01APIg0zVluzyomfB9b1tbzRzGvzJV
PLAYLIST_URLS = [
    "https://www.youtube.com/watch?v=LIf0AzB1610",
    "https://www.youtube.com/watch?v=47oi3Q9apK0",
    "https://www.youtube.com/watch?v=1SZT6P7Yr0k",
    "https://www.youtube.com/watch?v=Ot582-E61ac",
    "https://www.youtube.com/watch?v=ELavuz3btaE",
    "https://www.youtube.com/watch?v=HAPApZYuzsk",
    "https://www.youtube.com/watch?v=PzLcdIpjYCM",
    "https://www.youtube.com/watch?v=Q6Z7InUtnEY",
    "https://www.youtube.com/watch?v=m7nRpLfGKg4",
    "https://www.youtube.com/watch?v=zp28cRGwnJM",
    "https://www.youtube.com/watch?v=DBd9HFPlwfU",
    "https://www.youtube.com/watch?v=TNl5Gpbcq5g",
    "https://www.youtube.com/watch?v=T5nxmOzFSxQ",
    "https://www.youtube.com/watch?v=37cHuUx0gX0",
    "https://www.youtube.com/watch?v=kOTXWKCJklY",
    "https://www.youtube.com/watch?v=jWyb488osEY",
    "https://www.youtube.com/watch?v=-J_a74qFIkk",
    "https://www.youtube.com/watch?v=iYG5tiFfK3E",
    "https://www.youtube.com/watch?v=TP73qyFWDcY",
    "https://www.youtube.com/watch?v=3UWxMPUko1k",
    "https://www.youtube.com/watch?v=8tOfGkddPNM",
    "https://www.youtube.com/watch?v=pWZh37iRnDA",
    "https://www.youtube.com/watch?v=thfXHrAZVJI",
    "https://www.youtube.com/watch?v=QNnW1-rbxxo",
    "https://www.youtube.com/watch?v=25Bc6-4qti8",
    "https://www.youtube.com/watch?v=uQGjVlHBzqo",
    "https://www.youtube.com/watch?v=IbLnUIgmlUY",
    "https://www.youtube.com/watch?v=Odnv_jDQ-vI",
    "https://www.youtube.com/watch?v=PKDgWq5midA",
    "https://www.youtube.com/watch?v=2cs9HbFRMrA",
    "https://www.youtube.com/watch?v=L-7qtC4O4RU",
]


def archive_one(url, idx, total):
    """Archive a single video. Returns (url, success, output_snippet)."""
    vid = url.split("v=")[-1][:11]
    print(f"[{idx}/{total}] START  {vid}", flush=True)
    try:
        result = subprocess.run(
            ["python3", SCRIPT, url, "--csv-append"],
            capture_output=True, text=True, timeout=600,
            cwd=BASE
        )
        if result.returncode == 0:
            print(f"[{idx}/{total}] DONE   {vid}", flush=True)
            return (url, True, "")
        else:
            # Extract last few lines of stderr for context
            err_lines = result.stderr.strip().split("\n")[-5:]
            print(f"[{idx}/{total}] FAIL   {vid}: {'; '.join(err_lines)}", flush=True)
            return (url, False, "\n".join(err_lines))
    except subprocess.TimeoutExpired:
        print(f"[{idx}/{total}] TIMEOUT {vid}", flush=True)
        return (url, False, "TIMEOUT after 600s")
    except Exception as e:
        print(f"[{idx}/{total}] ERROR  {vid}: {e}", flush=True)
        return (url, False, str(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=5, help="Parallel workers")
    ap.add_argument("--url-file", help="File with one URL per line")
    ap.add_argument("--start", type=int, default=0, help="Start index (0-based)")
    ap.add_argument("--end", type=int, default=0, help="End index (0-based, exclusive). 0=all")
    ap.add_argument("urls", nargs="*", help="URLs to archive")
    args = ap.parse_args()

    if args.url_file:
        with open(args.url_file) as f:
            urls = [l.strip() for l in f if l.strip()]
    elif args.urls:
        urls = args.urls
    else:
        urls = PLAYLIST_URLS

    # Slice if requested
    if args.end > 0:
        urls = urls[args.start:args.end]
    elif args.start > 0:
        urls = urls[args.start:]

    total = len(urls)
    print(f"=== BATCH ARCHIVE: {total} videos, {args.batch_size} parallel ===\n", flush=True)

    results = []
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=args.batch_size) as pool:
        futures = {}
        for i, url in enumerate(urls, 1):
            f = pool.submit(archive_one, url, i, total)
            futures[f] = url

        for f in as_completed(futures):
            url, success, err = f.result()
            completed += 1
            if not success:
                failed += 1
            results.append((url, success, err))
            print(f"\n--- Progress: {completed}/{total} done, {failed} failed ---\n", flush=True)

    # Summary
    print("\n" + "=" * 60)
    print(f"BATCH COMPLETE: {total} videos, {total - failed} succeeded, {failed} failed")
    print("=" * 60)
    if failed:
        print("\nFailed videos:")
        for url, success, err in results:
            if not success:
                vid = url.split("v=")[-1][:11]
                print(f"  {vid}: {err[:120]}")

    # Write summary file
    summary_path = os.path.join(BASE, "_batch_summary.txt")
    with open(summary_path, "w") as sf:
        sf.write(f"Batch Archive Summary — {time.strftime('%Y-%m-%d %H:%M')}\n")
        sf.write(f"Total: {total}, Succeeded: {total-failed}, Failed: {failed}\n\n")
        for url, success, err in results:
            vid = url.split("v=")[-1][:11]
            status = "OK" if success else f"FAIL: {err[:200]}"
            sf.write(f"{vid}\t{status}\n")
    print(f"\nSummary written to {summary_path}")


if __name__ == "__main__":
    main()
