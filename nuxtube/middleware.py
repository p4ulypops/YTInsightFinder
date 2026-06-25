#!/usr/bin/env python3
"""NuxTube Middleware — headless daemon core for automation.

The middleware layer sits between the pipeline (archiver + watcher) and any
frontend (TUI, web dashboard, external script, cron job). It runs the watcher
and worker threads in the background and exposes a programmatic API for:

  - Querying status (queue, workers, completed, log)
  - Queueing videos manually
  - Pausing/resuming the watcher
  - Retrying failed videos
  - Getting results as structured data
  - Subscribing to events via callbacks

Architecture:

  ┌──────────────────────────────────────────────────┐
  │                  NuxTubeDaemon                    │
  │                                                   │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
  │  │ Watcher  │  │ Worker 0 │  │ Worker 1 │  ...   │
  │  │ (thread) │  │ (thread) │  │ (thread) │       │
  │  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
  │       │              │              │              │
  │       v              v              v              │
  │  ┌─────────────────────────────────────────┐     │
  │  │            Shared State                 │     │
  │  │  queue, workers, completed, log, stats  │     │
  │  └─────────────────────────────────────────┘     │
  │       ^                                           │
  │       │                                           │
  │  ┌────┴─────────────────────────────────────┐    │
  │  │              API Layer                    │    │
  │  │  status(), queue(), pause(), resume(),    │    │
  │  │  retry(), results(), subscribe()          │    │
  │  └───────────────────────────────────────────┘    │
  └───────────────────────────────────────────────────┘
          ^                    ^
          │                    │
   ┌──────┴──────┐     ┌──────┴──────┐
   │  TUI (Rich)  │     │  Web Dash   │
   │  (terminal)  │     │  (HTTP API) │
   └─────────────┘     └─────────────┘

Usage as middleware:

    from nuxtube.middleware import NuxTubeDaemon
    from nuxtube.config import Config

    daemon = NuxTubeDaemon(Config.load("config.yaml"))
    daemon.start()        # Start watcher + workers in background
    daemon.queue_url("https://youtube.com/watch?v=...")  # Manual add
    status = daemon.status()  # Get full status dict
    daemon.stop()         # Graceful shutdown

Usage as CLI daemon:

    python3 nuxtube.py --daemon              # Run headless
    python3 nuxtube.py --daemon --web 8080   # Headless + web dashboard
    python3 nuxtube.py --status              # Query running daemon
"""
import json
import os
import sys
import time
import threading
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Dict, Any
from queue import Queue

from .config import Config
from .archiver import ArchivePipeline, ArchiveResult
from .watcher import PlaylistWatcher
from .tracker import TrackerCSV


class NuxTubeDaemon:
    """Headless daemon that runs watcher + workers and exposes a status API.

    Thread-safe. All public methods are safe to call from any thread.
    """

    def __init__(self, config: Config):
        self.config = config
        self.pipeline = ArchivePipeline(config)
        self.tracker = self.pipeline.tracker

        # Shared state (protected by _lock)
        self._lock = threading.Lock()
        self._queue: deque = deque()       # (url, title, source) tuples
        self._workers: List[dict] = []     # Worker status dicts
        self._completed: deque = deque(maxlen=100)
        self._failed: deque = deque(maxlen=100)
        self._log: deque = deque(maxlen=500)
        self._stats = {
            "total_archived": 0,
            "total_failed": 0,
            "total_queued": 0,
            "started_at": None,
        }
        self._skip_flags: List[bool] = []
        self._running = False
        self._paused = False

        # Event subscribers
        self._subscribers: List[Callable] = []

        # Watcher
        self.watcher = PlaylistWatcher(
            config.sources,
            poll_interval=config.watch.poll_interval,
            on_new_videos=self._on_new_videos,
            on_log=self._log_callback,
        )
        self.watcher.set_archived_ids(self.tracker.get_archived_video_ids())

        # Init worker states
        for i in range(config.watch.max_workers):
            self._workers.append({
                "id": i,
                "busy": False,
                "url": "",
                "title": "",
                "stage": "",
                "progress": 0,
                "total": 1,
                "msg": "",
                "start_time": 0.0,
            })
            self._skip_flags.append(False)

        # Worker threads
        self._worker_threads: List[threading.Thread] = []

    # ─── Lifecycle ───

    def start(self):
        """Start the daemon: watcher + all worker threads in background."""
        if self._running:
            return
        self._running = True
        self._stats["started_at"] = datetime.now().isoformat()
        self._log_callback("info", f"Daemon starting — {len(self._workers)} workers, poll={self.config.watch.poll_interval}s")

        # Start watcher
        self.watcher.start()

        # Start workers
        for i in range(len(self._workers)):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            t.start()
            self._worker_threads.append(t)

    def stop(self):
        """Graceful shutdown: stop watcher, let workers finish current video."""
        self._running = False
        self.watcher.stop()
        self._log_callback("info", "Daemon stopping — waiting for workers...")
        for t in self._worker_threads:
            t.join(timeout=10)
        self._log_callback("info", "Daemon stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ─── Public API ───

    def status(self) -> dict:
        """Return full daemon status as a JSON-serializable dict."""
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "watcher": {
                    "last_check": self.watcher.last_check,
                    "check_count": self.watcher.check_count,
                    "paused": self.watcher.paused,
                    "sources": [
                        {"name": s.name, "type": s.type, "url": s.url, "enabled": s.enabled}
                        for s in self.config.sources
                    ],
                },
                "queue": {
                    "count": len(self._queue),
                    "items": [
                        {"url": u, "title": t, "source": s}
                        for u, t, s in list(self._queue)[:20]
                    ],
                },
                "workers": [
                    {**w, "elapsed": time.time() - w["start_time"] if w["busy"] else 0}
                    for w in self._workers
                ],
                "completed": [
                    self._result_to_dict(r) for r in list(self._completed)[:20]
                ],
                "failed": [
                    {"url": u, "title": t, "errors": e}
                    for u, t, e in list(self._failed)[:20]
                ],
                "stats": {**self._stats, "uptime": self._uptime()},
                "log": list(self._log)[-50:],
            }

    def queue_url(self, url: str, title: str = "", source: str = "manual"):
        """Manually add a video URL to the queue."""
        with self._lock:
            self._queue.append((url, title or url, source))
            self._stats["total_queued"] += 1
        self._log_callback("info", f"Queued manually: {title or url}")
        self._emit_event("queued", {"url": url, "title": title})

    def pause(self):
        """Pause the watcher (workers continue processing the queue)."""
        self._paused = True
        self.watcher.pause()
        self._emit_event("paused", {})

    def resume(self):
        """Resume the watcher."""
        self._paused = False
        self.watcher.resume()
        self._emit_event("resumed", {})

    def retry_failed(self) -> int:
        """Re-queue all failed videos. Returns count re-queued."""
        count = 0
        with self._lock:
            while self._failed:
                url, title, errors = self._failed.popleft()
                self._queue.append((url, title, "retry"))
                count += 1
        if count:
            self._log_callback("info", f"Re-queued {count} failed video(s)")
            self._emit_event("retry", {"count": count})
        return count

    def skip_worker(self, worker_id: int = -1) -> bool:
        """Skip current video in a worker. -1 = first busy worker."""
        if worker_id == -1:
            for i, w in enumerate(self._workers):
                if w["busy"]:
                    worker_id = i
                    break
        if worker_id < 0 or worker_id >= len(self._skip_flags):
            return False
        self._skip_flags[worker_id] = True
        self._log_callback("warn", f"Skip requested for worker {worker_id}")
        return True

    def check_now(self):
        """Force an immediate playlist check."""
        threading.Thread(target=self.watcher.check_for_new, daemon=True).start()
        self._log_callback("info", "Manual playlist check triggered")

    def results(self, limit: int = 50) -> List[dict]:
        """Return recent archive results as dicts."""
        with self._lock:
            return [self._result_to_dict(r) for r in list(self._completed)[:limit]]

    def subscribe(self, callback: Callable[[str, dict], None]):
        """Subscribe to events. Callback receives (event_type, data).

        Event types: queued, started, completed, failed, paused, resumed, retry, log
        """
        self._subscribers.append(callback)

    # ─── Internal ───

    def _uptime(self) -> str:
        if not self._stats["started_at"]:
            return "0s"
        start = datetime.fromisoformat(self._stats["started_at"])
        delta = datetime.now() - start
        h, m, s = int(delta.total_seconds() // 3600), int(delta.total_seconds() % 3600 // 60), int(delta.total_seconds() % 60)
        if h:
            return f"{h}h{m}m"
        if m:
            return f"{m}m{s}s"
        return f"{s}s"

    def _result_to_dict(self, r: ArchiveResult) -> dict:
        return {
            "video_id": r.video_id,
            "title": r.title,
            "url": r.url,
            "category": r.category,
            "status": r.status,
            "stages_completed": r.stages_completed,
            "errors": r.errors,
            "folder": r.folder,
            "screenshot_count": r.screenshot_count,
            "clip_count": r.clip_count,
            "duration": r.duration,
            "segment_count": r.segment_count,
            "timestamp": r.timestamp,
        }

    def _log_callback(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {level.upper():5s} {msg}"
        with self._lock:
            self._log.append(line)
        self._emit_event("log", {"level": level, "message": msg, "timestamp": ts})

    def _emit_event(self, event_type: str, data: dict):
        for cb in self._subscribers:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def _on_new_videos(self, videos):
        for vid, title, source_url in videos:
            url = f"https://www.youtube.com/watch?v={vid}"
            with self._lock:
                self._queue.append((url, title, source_url))
                self._stats["total_queued"] += 1
            self._log_callback("info", f"New video queued: {title} ({vid})")
            self._emit_event("queued", {"url": url, "title": title, "video_id": vid})

    def _worker_loop(self, worker_id: int):
        """Worker thread — archives videos from the queue."""
        while self._running:
            url = None
            title = ""
            source = ""
            with self._lock:
                if self._queue:
                    url, title, source = self._queue.popleft()

            if not url:
                time.sleep(0.5)
                continue

            # Check skip flag
            if self._skip_flags[worker_id]:
                self._skip_flags[worker_id] = False
                self._log_callback("warn", f"W{worker_id}: Skipped {title}")
                continue

            # Mark worker busy
            with self._lock:
                self._workers[worker_id].update({
                    "busy": True, "url": url, "title": title,
                    "stage": "starting", "progress": 0, "total": 1,
                    "msg": "", "start_time": time.time(),
                })

            self._log_callback("info", f"W{worker_id}: Starting {title}")
            self._emit_event("started", {"worker": worker_id, "url": url, "title": title})

            def on_log(level, msg):
                self._log_callback(level, f"W{worker_id}: {msg}")

            def on_progress(stage, cur, total, msg):
                with self._lock:
                    self._workers[worker_id].update({
                        "stage": stage, "progress": cur,
                        "total": total, "msg": msg,
                    })

            try:
                result = self.pipeline.archive(
                    url, on_log=on_log, on_progress=on_progress
                )

                if result.status == "success":
                    with self._lock:
                        self._stats["total_archived"] += 1
                        self._completed.appendleft(result)
                    self._log_callback("ok", f"W{worker_id}: DONE {result.title}")
                    self._emit_event("completed", self._result_to_dict(result))

                elif result.status == "skipped":
                    self._log_callback("info", f"W{worker_id}: Skipped (already archived)")

                elif result.status == "partial":
                    with self._lock:
                        self._stats["total_archived"] += 1
                        self._completed.appendleft(result)
                    self._log_callback("warn", f"W{worker_id}: PARTIAL {result.title}")
                    self._emit_event("completed", self._result_to_dict(result))

                else:
                    with self._lock:
                        self._stats["total_failed"] += 1
                        self._failed.append((url, title, result.errors))
                    self._log_callback("error", f"W{worker_id}: FAILED {title}")
                    self._emit_event("failed", {"url": url, "title": title, "errors": result.errors})

                self.watcher.add_archived(result.video_id)

            except Exception as e:
                with self._lock:
                    self._stats["total_failed"] += 1
                    self._failed.append((url, title, [str(e)]))
                self._log_callback("error", f"W{worker_id}: EXCEPTION {title}: {e}")
                self._emit_event("failed", {"url": url, "title": title, "errors": [str(e)]})

            # Mark worker idle
            with self._lock:
                self._workers[worker_id].update({
                    "busy": False, "url": "", "title": "",
                    "stage": "", "progress": 0, "total": 1, "msg": "",
                })

            # Delay between archives
            delay = self.config.watch.archive_delay
            if delay > 0:
                time.sleep(min(delay, 5))


# ─── Singleton for CLI daemon mode ───

_DAEMON: Optional[NuxTubeDaemon] = None
_PID_FILE = os.path.join(os.path.expanduser("~"), ".nuxtube_daemon.pid")


def get_daemon(config: Config = None) -> NuxTubeDaemon:
    """Get or create the singleton daemon instance."""
    global _DAEMON
    if _DAEMON is None and config:
        _DAEMON = NuxTubeDaemon(config)
    return _DAEMON


def write_pid():
    """Write current PID to pid file (for CLI status/stop commands)."""
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def read_pid() -> Optional[int]:
    """Read PID from pid file."""
    try:
        with open(_PID_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def clear_pid():
    """Remove pid file."""
    try:
        os.unlink(_PID_FILE)
    except Exception:
        pass
