#!/usr/bin/env python3
"""Rich multi-panel TUI dashboard for NuxTube.

Layout (htop/btop style):
  +--------------------------+--------------------------+
  |  Watch Status            |  Active Workers          |
  |  (playlist, poll, new)   |  (per-worker progress)   |
  +--------------------------+--------------------------+
  |  Recently Completed                                 |
  |  (table of archived videos)                        |
  +-----------------------------------------------------+
  |  Live Log                                           |
  |  (scrolling log output)                             |
  +-----------------------------------------------------+
  |  [p]ause [r]etry [s]kip [n]ow [q]uit [?]help       |
  +-----------------------------------------------------+

Keyboard controls:
  p     - Pause/resume watcher
  r     - Retry failed videos
  s     - Skip current video in active worker
  n     - Check playlist now (force immediate poll)
  q     - Quit gracefully
  ?     - Show/hide help overlay
  Tab   - Switch panel focus
  Up/Dn - Navigate within focused panel
  Enter - Drill into focused item
"""
import os
import sys
import time
import select
import tty
import termios
import threading
from collections import deque
from datetime import datetime
from queue import Queue
from typing import Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.align import Align
from rich.columns import Columns

from .config import Config
from .archiver import ArchivePipeline, ArchiveResult
from .watcher import PlaylistWatcher
from .tracker import TrackerCSV


# ─── Worker state ───

class WorkerState:
    """Tracks the state of a single archive worker thread."""
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.busy = False
        self.current_url = ""
        self.current_title = ""
        self.current_stage = ""
        self.stage_progress = 0
        self.stage_total = 1
        self.stage_msg = ""
        self.start_time = 0.0
        self.result: Optional[ArchiveResult] = None


# ─── TUI Application ───

class NuxTubeTUI:
    """Main TUI application with Rich live display."""

    def __init__(self, config: Config):
        self.config = config
        self.console = Console()
        self.running = False

        # State
        self.workers = [WorkerState(i) for i in range(config.watch.max_workers)]
        self.completed: deque = deque(maxlen=50)
        self.log_lines: deque = deque(maxlen=200)
        self.queue: deque = deque()  # URLs to archive
        self.failed: deque = deque(maxlen=50)
        self.focus_panel = 0  # 0=watch, 1=workers, 2=completed, 3=log
        self.show_help = False
        self.total_archived = 0
        self.total_failed = 0

        # Pipeline
        self.pipeline = ArchivePipeline(config)
        self.tracker = self.pipeline.tracker

        # Watcher
        self.watcher = PlaylistWatcher(
            config.sources,
            poll_interval=config.watch.poll_interval,
            on_new_videos=self._on_new_videos,
            on_log=self._on_log,
        )
        # Seed with already-archived IDs
        self.watcher.set_archived_ids(self.tracker.get_archived_video_ids())

        # Thread coordination
        self._lock = threading.Lock()
        self._skip_flags = [False] * config.watch.max_workers

    # ─── Logging ───

    def _on_log(self, level: str, msg: str):
        """Called by watcher and workers to add log lines."""
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.log_lines.append(f"[{ts}] {level.upper():5s} {msg}")

    def log(self, level: str, msg: str):
        self._on_log(level, msg)

    # ─── Watcher callback ───

    def _on_new_videos(self, videos):
        """Called when watcher finds new videos."""
        for vid, title, source_url in videos:
            url = f"https://www.youtube.com/watch?v={vid}"
            with self._lock:
                self.queue.append((url, title))
            self.log("info", f"Queued: {title} ({vid})")

    # ─── Worker thread ───

    def _worker_loop(self, worker: WorkerState):
        """Background worker thread — archives videos from the queue."""
        while self.running:
            url = None
            title = ""
            with self._lock:
                if self.queue:
                    url, title = self.queue.popleft()

            if not url:
                time.sleep(0.5)
                continue

            # Check skip flag
            if self._skip_flags[worker.worker_id]:
                self._skip_flags[worker.worker_id] = False
                self.log("warn", f"Worker {worker.worker_id}: Skipped {title}")
                continue

            worker.busy = True
            worker.current_url = url
            worker.current_title = title
            worker.start_time = time.time()
            worker.result = None

            self.log("info", f"Worker {worker.worker_id}: Starting {title}")

            def on_log(level, msg):
                self._on_log(level, f"W{worker.worker_id}: {msg}")

            def on_progress(stage, cur, total, msg):
                worker.current_stage = stage
                worker.stage_progress = cur
                worker.stage_total = total
                worker.stage_msg = msg

            try:
                result = self.pipeline.archive(
                    url, on_log=on_log, on_progress=on_progress
                )
                worker.result = result

                if result.status == "success":
                    self.total_archived += 1
                    self.log("ok", f"Worker {worker.worker_id}: DONE {result.title}")
                elif result.status == "skipped":
                    self.log("info", f"Worker {worker.worker_id}: Skipped (already archived)")
                elif result.status == "partial":
                    self.total_archived += 1
                    self.log("warn", f"Worker {worker.worker_id}: PARTIAL {result.title} ({len(result.errors)} errors)")
                else:
                    self.total_failed += 1
                    self.log("error", f"Worker {worker.worker_id}: FAILED {title}")
                    with self._lock:
                        self.failed.append((url, title, result.errors))

                with self._lock:
                    self.completed.appendleft(result)

                # Mark as archived
                self.watcher.add_archived(result.video_id)

            except Exception as e:
                self.total_failed += 1
                self.log("error", f"Worker {worker.worker_id}: EXCEPTION {title}: {e}")
                with self._lock:
                    self.failed.append((url, title, [str(e)]))

            worker.busy = False
            worker.current_url = ""
            worker.current_title = ""
            worker.current_stage = ""
            worker.stage_progress = 0
            worker.stage_total = 1
            worker.stage_msg = ""

            # Delay between archives
            delay = self.config.watch.archive_delay
            if delay > 0:
                time.sleep(min(delay, 5))  # Cap at 5s for responsiveness

    # ─── Keyboard input ───

    def _get_key(self, timeout: float = 0.1) -> Optional[str]:
        """Non-blocking keyboard input. Returns key string or None."""
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                ch = sys.stdin.read(1)
                if ch == "\x1b":  # Escape sequence (arrow keys etc)
                    rlist2, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if rlist2:
                        ch += sys.stdin.read(2)
                return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return None

    def _handle_key(self, key: str) -> bool:
        """Handle a keypress. Returns False to quit."""
        if key == "q":
            return False
        elif key == "p":
            if self.watcher.paused:
                self.watcher.resume()
            else:
                self.watcher.pause()
        elif key == "n":
            threading.Thread(target=self.watcher.check_for_new, daemon=True).start()
            self.log("info", "Manual playlist check triggered")
        elif key == "r":
            with self._lock:
                while self.failed:
                    url, title, errors = self.failed.popleft()
                    self.queue.append((url, title))
                    self.log("info", f"Re-queued failed: {title}")
            if not self.failed:
                self.log("info", "No failed videos to retry")
        elif key == "s":
            # Skip the first busy worker
            for w in self.workers:
                if w.busy:
                    self._skip_flags[w.worker_id] = True
                    self.log("warn", f"Skip requested for worker {w.worker_id}")
                    break
        elif key == "?":
            self.show_help = not self.show_help
        elif key == "\t":  # Tab
            self.focus_panel = (self.focus_panel + 1) % 4
        elif key == "\r":  # Enter
            pass  # Future: drill into item
        elif key == "\x1b[A":  # Up arrow
            pass  # Future: navigate
        elif key == "\x1b[B":  # Down arrow
            pass
        return True

    # ─── Rendering ───

    def _render_watch_panel(self) -> Panel:
        """Render the watch status panel (top-left)."""
        lines = []
        for src in self.config.sources[:5]:
            icon = {"playlist": "[blue]PL[/]", "channel": "[red]CH[/]", "video": "[green]VID[/]"}.get(src.type, "[dim]??[/]")
            lines.append(f"  {icon} {src.name[:30]}")

        if not self.config.sources:
            lines.append("  [dim]No sources configured[/]")
            lines.append("  [dim]Edit config.yaml to add playlists[/]")

        lines.append("")
        lines.append(f"  Last check: [cyan]{self.watcher.last_check or 'never'}[/]")
        lines.append(f"  Checks:     {self.watcher.check_count}")
        lines.append(f"  Paused:     {'[yellow]YES[/]' if self.watcher.paused else '[green]no[/]'}")
        lines.append("")
        lines.append(f"  Queue:      [yellow]{len(self.queue)}[/] waiting")
        lines.append(f"  Archived:   [green]{self.total_archived}[/]")
        lines.append(f"  Failed:     [red]{self.total_failed}[/]")

        border = "cyan" if self.focus_panel == 0 else "dim"
        return Panel(
            "\n".join(lines),
            title="[bold cyan]Watch Status[/]",
            border_style=border,
            height=16,
        )

    def _render_workers_panel(self) -> Panel:
        """Render the active workers panel (top-right)."""
        lines = []
        active = sum(1 for w in self.workers if w.busy)
        lines.append(f"  Active: [bold]{active}[/]/{len(self.workers)}")
        lines.append("")

        for w in self.workers:
            if w.busy:
                pct = int(w.stage_progress / max(w.stage_total, 1) * 100)
                bar_len = 12
                filled = int(pct / 100 * bar_len)
                bar = "[" + "=" * filled + " " * (bar_len - filled) + "]"
                stage_icons = {
                    "transcript": "T", "metadata": "M", "download": "D",
                    "screenshots": "S", "clips": "C", "keypoints": "K", "tracker": "X",
                }
                icon = stage_icons.get(w.current_stage, "?")
                lines.append(f"  [bold]W{w.worker_id}[/] {bar} {pct:3d}% [{icon}]")
                title = w.current_title[:32] if w.current_title else "..."
                lines.append(f"       [dim]{title}[/]")
                if w.stage_msg:
                    lines.append(f"       [dim]{w.stage_msg[:40]}[/]")
                lines.append("")
            else:
                lines.append(f"  [dim]W{w.worker_id} [idle][/]")
                lines.append("")

        border = "yellow" if self.focus_panel == 1 else "dim"
        return Panel(
            "\n".join(lines),
            title=f"[bold yellow]Workers ({active}/{len(self.workers)})[/]",
            border_style=border,
            height=16,
        )

    def _render_completed_panel(self) -> Panel:
        """Render the recently completed table (middle)."""
        table = Table(expand=True, show_header=True, header_style="bold")
        table.add_column("Title", ratio=3, no_wrap=True)
        table.add_column("Cat", ratio=1, no_wrap=True)
        table.add_column("SS", justify="right", ratio=1)
        table.add_column("Clips", justify="right", ratio=1)
        table.add_column("Status", ratio=1)

        shown = list(self.completed)[:10]
        for r in shown:
            status_str = {
                "success": "[green]OK[/]",
                "partial": "[yellow]PART[/]",
                "failed": "[red]FAIL[/]",
                "skipped": "[dim]SKIP[/]",
            }.get(r.status, "[dim]?[/]")
            title = (r.title or r.video_id or "?")[:40]
            table.add_row(
                title,
                r.category[:10],
                str(r.screenshot_count),
                str(r.clip_count),
                status_str,
            )

        if not shown:
            table.add_row("[dim]No videos archived yet...[/]", "", "", "", "")

        border = "green" if self.focus_panel == 2 else "dim"
        return Panel(
            table,
            title="[bold green]Recently Completed[/]",
            border_style=border,
            height=12,
        )

    def _render_log_panel(self) -> Panel:
        """Render the live log (bottom)."""
        shown = list(self.log_lines)[-15:]
        lines = []
        for line in shown:
            if "ERROR" in line:
                lines.append(f"[red]{line}[/]")
            elif "WARN" in line:
                lines.append(f"[yellow]{line}[/]")
            elif "OK" in line:
                lines.append(f"[green]{line}[/]")
            else:
                lines.append(f"[dim]{line}[/]")

        border = "blue" if self.focus_panel == 3 else "dim"
        return Panel(
            "\n".join(lines) if lines else "[dim]Waiting for log output...[/]",
            title="[bold blue]Live Log[/]",
            border_style=border,
            height=10,
        )

    def _render_help_overlay(self) -> Panel:
        """Render help overlay."""
        help_text = """
[bold]Keyboard Controls[/]
  [cyan]p[/]     Pause/resume watcher
  [cyan]r[/]     Retry failed videos
  [cyan]s[/]     Skip current video in active worker
  [cyan]n[/]     Check playlist now (force immediate poll)
  [cyan]q[/]     Quit gracefully
  [cyan]?[/]     Show/hide this help
  [cyan]Tab[/]   Switch panel focus
  [cyan]Up/Dn[/] Navigate within focused panel

Press [bold]?[/] to close this help.
"""
        return Panel(Align.center(help_text), title="[bold]Help[/]", border_style="magenta")

    def _render_footer(self) -> Text:
        """Render the keyboard shortcuts footer."""
        active = sum(1 for w in self.workers if w.busy)
        return Text.from_markup(
            f"  [bold cyan]p[/]ause  [bold cyan]r[/]etry  [bold cyan]s[/]kip  "
            f"[bold cyan]n[/]ow  [bold cyan]q[/]uit  [bold cyan]?[/]help  "
            f"  Workers: [bold]{active}[/]/{len(self.workers)}  "
            f"Queue: [bold yellow]{len(self.queue)}[/]  "
            f"Done: [bold green]{self.total_archived}[/]  "
            f"Fail: [bold red]{self.total_failed}[/]"
        )

    def _render(self) -> Layout:
        """Render the full dashboard layout."""
        layout = Layout()

        layout.split_column(
            Layout(name="top", size=18),
            Layout(name="completed", size=13),
            Layout(name="log", size=11),
            Layout(name="footer", size=1),
        )

        layout["top"].split_row(
            Layout(name="watch"),
            Layout(name="workers"),
        )

        layout["watch"].update(self._render_watch_panel())
        layout["workers"].update(self._render_workers_panel())
        layout["completed"].update(self._render_completed_panel())
        layout["log"].update(self._render_log_panel())
        layout["footer"].update(self._render_footer())

        if self.show_help:
            layout = Layout(self._render_help_overlay())

        return layout

    # ─── Main loop ───

    def run(self):
        """Main TUI loop. Blocks until quit."""
        self.running = True
        self.log("info", "NuxTube TUI starting up...")
        self.log("info", f"Watching {len(self.config.sources)} source(s)")
        self.log("info", f"Workers: {self.config.watch.max_workers}, Poll: {self.config.watch.poll_interval}s")

        # Start watcher
        self.watcher.start()

        # Start worker threads
        worker_threads = []
        for w in self.workers:
            t = threading.Thread(target=self._worker_loop, args=(w,), daemon=True)
            t.start()
            worker_threads.append(t)

        # Restore terminal on exit
        def cleanup():
            self.running = False
            self.watcher.stop()
            # Reset terminal
            fd = sys.stdin.fileno()
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, termios.tcgetattr(fd))
            except Exception:
                pass

        # Main render + keyboard loop
        with Live(self._render(), console=self.console, refresh_per_second=4, screen=True) as live:
            try:
                while self.running:
                    live.update(self._render())
                    key = self._get_key(timeout=0.25)
                    if key:
                        if not self._handle_key(key):
                            break
            except KeyboardInterrupt:
                pass
            finally:
                cleanup()
                live.update(self._render())

        # Print final summary
        print()
        print("=" * 50)
        print("  NuxTube session summary")
        print("=" * 50)
        print(f"  Archived:  {self.total_archived}")
        print(f"  Failed:    {self.total_failed}")
        print(f"  Still in queue: {len(self.queue)}")
        print(f"  Watcher checks: {self.watcher.check_count}")
        print()



