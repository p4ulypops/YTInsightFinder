#!/usr/bin/env python3
"""Retry failed videos sequentially with delays."""
import subprocess, sys, os, time

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_video.py")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 26 failed videos from the first batch
FAILED = [
    "HAPApZYuzsk", "Q6Z7InUtnEY", "PzLcdIpjYCM", "m7nRpLfGKg4",
    "zp28cRGwnJM", "DBd9HFPlwfU", "TNl5Gpbcq5g", "T5nxmOzFSxQ",
    "kOTXWKCJklY", "jWyb488osEY", "-J_a74qFIkk", "iYG5tiFfK3E",
    "TP73qyFWDcY", "3UWxMPUko1k", "8tOfGkddPNM", "pWZh37iRnDA",
    "thfXHrAZVJI", "QNnW1-rbxxo", "25Bc6-4qti8", "uQGjVlHBzqo",
    "IbLnUIgmlUY", "Odnv_jDQ-vI", "PKDgWq5midA", "L-7qtC4O4RU",
    "2cs9HbFRMrA", "37cHuUx0gX0",
]

DELAY = 15  # seconds between each video
TIMEOUT = 600  # 10 min per video

total = len(FAILED)
succeeded = []
failed = []

for i, vid in enumerate(FAILED, 1):
    url = f"https://www.youtube.com/watch?v={vid}"
    print(f"\n[{i}/{total}] Processing {vid}...", flush=True)
    try:
        result = subprocess.run(
            ["python3", SCRIPT, url, "--csv-append"],
            capture_output=True, text=True, timeout=TIMEOUT, cwd=BASE
        )
        if result.returncode == 0:
            print(f"[{i}/{total}] DONE {vid}", flush=True)
            succeeded.append(vid)
        else:
            err = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown"
            print(f"[{i}/{total}] FAIL {vid}: {err[:150]}", flush=True)
            failed.append((vid, err[:200]))
    except subprocess.TimeoutExpired:
        print(f"[{i}/{total}] TIMEOUT {vid}", flush=True)
        failed.append((vid, "TIMEOUT"))
    except Exception as e:
        print(f"[{i}/{total}] ERROR {vid}: {e}", flush=True)
        failed.append((vid, str(e)[:200]))

    if i < total:
        print(f"  waiting {DELAY}s...", flush=True)
        time.sleep(DELAY)

print("\n" + "=" * 60)
print(f"SEQUENTIAL BATCH COMPLETE: {total} videos")
print(f"  Succeeded: {len(succeeded)}")
print(f"  Failed:    {len(failed)}")
print("=" * 60)
if failed:
    print("\nStill failed:")
    for vid, err in failed:
        print(f"  {vid}: {err[:120]}")

# Write summary
with open(os.path.join(BASE, "_retry_summary.txt"), "w") as f:
    f.write(f"Retry Summary — {time.strftime('%Y-%m-%d %H:%M')}\n")
    f.write(f"Total: {total}, Succeeded: {len(succeeded)}, Failed: {len(failed)}\n\n")
    f.write("Succeeded:\n")
    for v in succeeded:
        f.write(f"  {v}\n")
    f.write("\nFailed:\n")
    for v, e in failed:
        f.write(f"  {v}: {e}\n")
print(f"\nSummary: {os.path.join(BASE, '_retry_summary.txt')}")
