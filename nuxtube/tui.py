#!/usr/bin/env python3
"""Rich multi-panel TUI dashboard for NuxTube.

Layout (htop/btop style):
  +--------------------------+--------------------------+
  |  Watch Status            |  Active Workers          |
  +--------------------------+--------------------------+
  |  Recently Completed  (arrow-key selectable)         |
  +-----------------------------------------------------+
  |  Live Log                                           |
  +-----------------------------------------------------+
  |  keyboard shortcuts footer                          |
  +-----------------------------------------------------+

Keyboard controls:
  p     - Pause/resume watcher
  r     - Retry failed videos
  s     - Skip current video in active worker
  n     - Check playlist now (force immediate poll)
  q     - Quit gracefully
  ?     - Show/hide help overlay
  o     - Open options screen (edit all settings)
  a     - Add URL to queue manually
  v     - Generate HTML viewer for selected completed item
  g     - Generate OmniFile for selected completed item
  Tab   - Switch panel focus
  Up/Dn - Navigate within focused panel (completed list)
  Enter - Drill into selected completed item (detail view)
  Esc   - Close any overlay
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
from typing import Optional, List

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.align import Align

from .config import Config
from .archiver import ArchivePipeline, ArchiveResult
from .watcher import PlaylistWatcher
from .tracker import TrackerCSV


# ─── Option definitions ──────────────────────────────────────────────────────

PIPELINE_OPTS = [
    {"key": "capture_mode",      "label": "Capture Mode",        "type": "cycle",
     "choices": ["full", "audio", "transcript"],       "obj": "pipeline"},
    {"key": "quality",           "label": "Quality",             "type": "cycle",
     "choices": ["480p", "720p", "1080p", "best"],    "obj": "pipeline"},
    {"key": "key_moment_mode",   "label": "Key Moment Mode",     "type": "cycle",
     "choices": ["smart", "cues"],                     "obj": "pipeline"},
    {"key": "max_clips",         "label": "Max Clips",           "type": "int",  "obj": "pipeline"},
    {"key": "clip_duration",     "label": "Clip Duration (s)",   "type": "int",  "obj": "pipeline"},
    {"key": "clip_start_offset", "label": "Clip Start Offs (s)", "type": "int",  "obj": "pipeline"},
    {"key": "screenshot_offset", "label": "Screenshot Offs (s)", "type": "int",  "obj": "pipeline"},
    {"key": "max_height",        "label": "Max Height (px)",     "type": "int",  "obj": "pipeline"},
    {"key": "keep_video",        "label": "Keep Source Video",   "type": "bool", "obj": "pipeline"},
    {"key": "segment_download",  "label": "Segment Download",    "type": "bool", "obj": "pipeline"},
    {"key": "_header_stages",    "label": "── Pipeline Stages ──────────────",
     "type": "header", "obj": None},
    {"key": "_stage_transcript", "label": "  transcript",  "type": "stage", "stage": "transcript"},
    {"key": "_stage_metadata",   "label": "  metadata",    "type": "stage", "stage": "metadata"},
    {"key": "_stage_player_data","label": "  player_data", "type": "stage", "stage": "player_data"},
    {"key": "_stage_download",   "label": "  download",    "type": "stage", "stage": "download"},
    {"key": "_stage_screenshots","label": "  screenshots", "type": "stage", "stage": "screenshots"},
    {"key": "_stage_clips",      "label": "  clips",       "type": "stage", "stage": "clips"},
    {"key": "_stage_keypoints",  "label": "  keypoints",   "type": "stage", "stage": "keypoints"},
    {"key": "_stage_tracker",    "label": "  tracker",     "type": "stage", "stage": "tracker"},
]

WATCH_OPTS = [
    {"key": "poll_interval",    "label": "Poll Interval (s)",  "type": "int",  "obj": "watch"},
    {"key": "max_workers",      "label": "Max Workers",        "type": "int",  "obj": "watch"},
    {"key": "archive_delay",    "label": "Archive Delay (s)",  "type": "int",  "obj": "watch"},
    {"key": "archive_timeout",  "label": "Archive Timeout (s)","type": "int",  "obj": "watch"},
    {"key": "auto_archive",     "label": "Auto Archive",       "type": "bool", "obj": "watch"},
]

OPT_TABS = ["Pipeline", "Watch", "Sources", "Info"]


# ─── Worker state ────────────────────────────────────────────────────────────

class WorkerState:
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


# ─── TUI Application ─────────────────────────────────────────────────────────

class NuxTubeTUI:
    """Main TUI application with Rich live display."""

    def __init__(self, config: Config):
        self.config = config
        self.console = Console()
        self.running = False

        # Core state
        self.workers = [WorkerState(i) for i in range(config.watch.max_workers)]
        self.completed: deque = deque(maxlen=50)
        self.log_lines: deque = deque(maxlen=200)
        self.queue: deque = deque()
        self.failed: deque = deque(maxlen=50)
        self.total_archived = 0
        self.total_failed = 0

        # Focus / navigation
        self.focus_panel = 0       # 0=watch, 1=workers, 2=completed, 3=log
        self.completed_cursor = 0  # selected row in completed panel

        # Overlay states (mutually exclusive except input_mode)
        self.show_help = False
        self.show_options = False
        self.show_detail = False
        self.input_mode = False     # URL input bar at footer

        # Options screen state
        self.opt_tab = 0            # which tab (0=Pipeline, 1=Watch, 2=Sources, 3=Info)
        self.opt_cursor = 0         # item cursor within tab
        self.opt_editing = False    # in edit-mode for current field
        self.opt_edit_buf = ""      # accumulated chars in edit mode

        # Input prompt state
        self.input_buf = ""
        self.input_prompt = ""
        self.input_callback = None

        # Detail view state
        self.detail_tab = 0         # 0=overview, 1=transcript, 2=keypoints

        # Pipeline + watcher
        self.pipeline = ArchivePipeline(config)
        self.tracker = self.pipeline.tracker
        self.watcher = PlaylistWatcher(
            config.sources,
            poll_interval=config.watch.poll_interval,
            on_new_videos=self._on_new_videos,
            on_log=self._on_log,
        )
        self.watcher.set_archived_ids(self.tracker.get_archived_video_ids())

        self._lock = threading.Lock()
        self._skip_flags = [False] * config.watch.max_workers

    # ─── Logging ──────────────────────────────────────────────────────────

    def _on_log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.log_lines.append(f"[{ts}] {level.upper():5s} {msg}")

    def log(self, level: str, msg: str):
        self._on_log(level, msg)

    # ─── Watcher callback ─────────────────────────────────────────────────

    def _on_new_videos(self, videos):
        for vid, title, source_url in videos:
            url = f"https://www.youtube.com/watch?v={vid}"
            with self._lock:
                self.queue.append((url, title))
            self.log("info", f"Queued: {title} ({vid})")

    # ─── Worker thread ────────────────────────────────────────────────────

    def _worker_loop(self, worker: WorkerState):
        while self.running:
            url = None
            title = ""
            with self._lock:
                if self.queue:
                    url, title = self.queue.popleft()
            if not url:
                time.sleep(0.5)
                continue

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

            def on_log(level, msg, wid=worker.worker_id):
                self._on_log(level, f"W{wid}: {msg}")

            def on_progress(stage, cur, total, msg, w=worker):
                w.current_stage = stage
                w.stage_progress = cur
                w.stage_total = total
                w.stage_msg = msg

            try:
                result = self.pipeline.archive(url, on_log=on_log, on_progress=on_progress)
                worker.result = result

                if result.status == "success":
                    self.total_archived += 1
                    self.log("ok", f"Worker {worker.worker_id}: DONE {result.title}")
                elif result.status == "skipped":
                    self.log("info", f"Worker {worker.worker_id}: Skipped (already archived)")
                elif result.status == "partial":
                    self.total_archived += 1
                    self.log("warn", f"Worker {worker.worker_id}: PARTIAL {result.title}")
                else:
                    self.total_failed += 1
                    self.log("error", f"Worker {worker.worker_id}: FAILED {title}")
                    with self._lock:
                        self.failed.append((url, title, result.errors))

                with self._lock:
                    self.completed.appendleft(result)
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

            delay = self.config.watch.archive_delay
            if delay > 0:
                time.sleep(min(delay, 5))

    # ─── Keyboard input ───────────────────────────────────────────────────

    def _get_key(self, timeout: float = 0.1) -> Optional[str]:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    rlist2, _, _ = select.select([sys.stdin], [], [], 0.02)
                    if rlist2:
                        rest = sys.stdin.read(2)
                        ch += rest
                return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return None

    def _handle_key(self, key: str) -> bool:
        """Route keypress to appropriate handler. Returns False to quit."""
        # Input prompt mode — capture all keys
        if self.input_mode:
            return self._handle_input_key(key)

        # Overlay escape
        if key in ("\x1b", "\x1b["):
            if self.show_options or self.show_help or self.show_detail:
                self.show_options = False
                self.show_help = False
                self.show_detail = False
                self.opt_editing = False
                return True

        if self.show_options:
            return self._handle_options_key(key)

        if self.show_detail:
            return self._handle_detail_key(key)

        # Normal mode
        return self._handle_normal_key(key)

    def _handle_normal_key(self, key: str) -> bool:
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
                count = 0
                while self.failed:
                    url, title, errors = self.failed.popleft()
                    self.queue.append((url, title))
                    count += 1
            if count:
                self.log("info", f"Re-queued {count} failed video(s)")
            else:
                self.log("info", "No failed videos to retry")
        elif key == "s":
            for w in self.workers:
                if w.busy:
                    self._skip_flags[w.worker_id] = True
                    self.log("warn", f"Skip requested for worker {w.worker_id}")
                    break
        elif key == "?":
            self.show_help = not self.show_help
            self.show_options = False
            self.show_detail = False
        elif key == "o":
            self.show_options = not self.show_options
            self.show_help = False
            self.show_detail = False
            self.opt_tab = 0
            self.opt_cursor = 0
            self.opt_editing = False
        elif key == "a":
            self._start_input("Queue URL: ", self._do_queue_url)
        elif key == "v":
            self._generate_viewer_for_selected(bake=False)
        elif key == "g":
            self._generate_omni_for_selected()
        elif key == "G":
            self._batch_generate_viewers()
        elif key == "\t":
            self.focus_panel = (self.focus_panel + 1) % 4
        elif key == "\r" or key == "\n":
            if self.focus_panel == 2 and self.completed:
                self.show_detail = True
                self.detail_tab = 0
                self.show_help = False
                self.show_options = False
        elif key == "\x1b[A":  # Up
            if self.focus_panel == 2:
                self.completed_cursor = max(0, self.completed_cursor - 1)
            elif self.focus_panel == 3:
                max_scroll = max(0, len(self.log_lines) - 15)
                self.log_scroll = min(max_scroll, self.log_scroll + 3)
        elif key == "\x1b[B":  # Down
            if self.focus_panel == 2:
                max_i = max(0, len(self.completed) - 1)
                self.completed_cursor = min(max_i, self.completed_cursor + 1)
            elif self.focus_panel == 3:
                self.log_scroll = max(0, self.log_scroll - 3)
        return True

    # ─── Options screen ───────────────────────────────────────────────────

    def _current_opts(self) -> list:
        return [PIPELINE_OPTS, WATCH_OPTS, [], []][self.opt_tab]

    def _navigable_opts(self) -> list:
        """Options that can receive cursor focus."""
        return [o for o in self._current_opts() if o["type"] not in ("header",)]

    def _get_opt_value(self, opt: dict):
        t = opt["type"]
        if t == "stage":
            return opt["stage"] in self.config.pipeline.stages
        obj = opt.get("obj")
        if obj == "pipeline":
            return getattr(self.config.pipeline, opt["key"], None)
        elif obj == "watch":
            return getattr(self.config.watch, opt["key"], None)
        return None

    def _set_opt_value(self, opt: dict, value):
        obj = opt.get("obj")
        if obj == "pipeline":
            setattr(self.config.pipeline, opt["key"], value)
        elif obj == "watch":
            setattr(self.config.watch, opt["key"], value)

    def _cycle_opt(self, opt: dict):
        choices = opt.get("choices", [])
        if not choices:
            return
        cur = self._get_opt_value(opt)
        try:
            idx = choices.index(cur)
        except ValueError:
            idx = -1
        self._set_opt_value(opt, choices[(idx + 1) % len(choices)])

    def _toggle_opt(self, opt: dict):
        cur = self._get_opt_value(opt)
        self._set_opt_value(opt, not cur)

    def _toggle_stage(self, opt: dict):
        stage = opt["stage"]
        stages = list(self.config.pipeline.stages)
        if stage in stages:
            stages.remove(stage)
        else:
            # Insert in canonical order
            order = ["transcript","metadata","player_data","download",
                     "screenshots","clips","keypoints","tracker"]
            stages = [s for s in order if s in stages or s == stage]
        self.config.pipeline.stages = stages

    def _opt_nav_max(self) -> int:
        """Max cursor index for current tab."""
        if self.opt_tab == 0:
            return max(0, len(self._navigable_opts()) - 1)
        if self.opt_tab == 1:
            return max(0, len(WATCH_OPTS) - 1)
        if self.opt_tab == 2:
            return max(0, len(self.config.sources) - 1)
        if self.opt_tab == 3:
            return max(0, len(self.config.categories) - 1)
        return 0

    def _handle_options_key(self, key: str) -> bool:
        nav = self._navigable_opts()

        # Global option-screen keys (when not editing)
        if not self.opt_editing:
            if key in ("o", "\x1b"):
                self.show_options = False
                return True
            elif key == "q":
                return False
            elif key == "\t":
                self.opt_tab = (self.opt_tab + 1) % len(OPT_TABS)
                self.opt_cursor = 0
                self.opt_editing = False
                self.opt_edit_buf = ""
                return True
            elif key == "\x1b[A":  # Up
                self.opt_cursor = max(0, self.opt_cursor - 1)
                return True
            elif key == "\x1b[B":  # Down
                self.opt_cursor = min(self._opt_nav_max(), self.opt_cursor + 1)
                return True
            elif key in ("\r", "\n", " "):
                if self.opt_tab == 2:   # Sources tab — toggle enabled
                    self._opt_sources_action()
                    return True
                if not nav:
                    return True
                cur_opt = nav[self.opt_cursor] if self.opt_cursor < len(nav) else None
                if not cur_opt:
                    return True
                t = cur_opt["type"]
                if t == "bool":
                    self._toggle_opt(cur_opt)
                elif t == "cycle":
                    self._cycle_opt(cur_opt)
                elif t == "stage":
                    self._toggle_stage(cur_opt)
                elif t == "int":
                    # Enter edit mode
                    self.opt_editing = True
                    self.opt_edit_buf = str(self._get_opt_value(cur_opt) or "")
                return True
            elif key == "s":
                self.config.save()
                self.log("ok", f"Config saved to {self.config.config_path}")
                return True
            # Tab-specific actions
            elif key == "a" and self.opt_tab == 2:
                self._opt_add_source()
                return True
            elif key == "d" and self.opt_tab == 2:
                self._opt_delete_source()
                return True
            elif key == "a" and self.opt_tab == 3:
                self._opt_add_category()
                return True
            elif key == "d" and self.opt_tab == 3:
                self._opt_delete_category()
                return True
            elif key == "G":
                self._batch_generate_viewers()
                return True
        else:
            # Edit mode for int field
            if key in ("\r", "\n"):
                # Commit
                nav = self._navigable_opts()
                if self.opt_cursor < len(nav):
                    cur_opt = nav[self.opt_cursor]
                    if cur_opt["type"] == "int":
                        try:
                            v = int(self.opt_edit_buf)
                            self._set_opt_value(cur_opt, v)
                        except ValueError:
                            pass  # Discard invalid input
                self.opt_editing = False
                self.opt_edit_buf = ""
            elif key == "\x1b":
                self.opt_editing = False
                self.opt_edit_buf = ""
            elif key == "\x7f" or key == "\x08":  # Backspace
                self.opt_edit_buf = self.opt_edit_buf[:-1]
            elif key and key[0].isdigit() or (key == "-" and not self.opt_edit_buf):
                self.opt_edit_buf += key
        return True

    def _opt_sources_action(self):
        """Toggle enable/disable for source under cursor (Sources tab)."""
        srcs = self.config.sources
        if not srcs:
            return
        idx = min(self.opt_cursor, len(srcs) - 1)
        srcs[idx].enabled = not srcs[idx].enabled

    def _opt_add_source(self):
        """Start input prompt to add a new source."""
        self.show_options = False
        self._start_input("Add Source URL: ", self._do_add_source)

    def _do_add_source(self, url: str):
        if not url.strip():
            return
        from .config import Source
        src_type = "channel" if "/@" in url or "/channel/" in url else "playlist"
        self.config.sources.append(Source(url=url.strip(), name=url.strip()[:40], type=src_type))
        self.log("ok", f"Source added: {url.strip()[:50]}")
        self.config.save()

    def _opt_delete_source(self):
        srcs = self.config.sources
        if not srcs:
            return
        idx = min(self.opt_cursor, len(srcs) - 1)
        removed = srcs.pop(idx)
        self.log("warn", f"Source removed: {removed.name}")
        self.opt_cursor = max(0, self.opt_cursor - 1)

    # ─── Detail view ──────────────────────────────────────────────────────

    def _selected_completed(self) -> Optional[ArchiveResult]:
        items = list(self.completed)
        if not items:
            return None
        return items[min(self.completed_cursor, len(items) - 1)]

    def _handle_detail_key(self, key: str) -> bool:
        if key in ("\x1b", "q", "\r", "\n"):
            self.show_detail = False
            return True if key != "q" else False
        elif key == "\t":
            self.detail_tab = (self.detail_tab + 1) % 3
        elif key == "g":
            self._generate_omni_for_selected()
        elif key == "v":
            self._generate_viewer_for_selected(bake=False)
        elif key == "b":
            self._generate_viewer_for_selected(bake=True)
        return True

    # ─── Input prompt ─────────────────────────────────────────────────────

    def _start_input(self, prompt: str, callback):
        self.input_mode = True
        self.input_prompt = prompt
        self.input_buf = ""
        self.input_callback = callback

    def _handle_input_key(self, key: str) -> bool:
        if key in ("\r", "\n"):
            val = self.input_buf
            self.input_mode = False
            self.input_buf = ""
            if self.input_callback and val.strip():
                self.input_callback(val.strip())
        elif key == "\x1b":
            self.input_mode = False
            self.input_buf = ""
        elif key in ("\x7f", "\x08"):
            self.input_buf = self.input_buf[:-1]
        elif key and len(key) == 1 and key.isprintable():
            self.input_buf += key
        return True

    def _do_queue_url(self, url: str):
        with self._lock:
            self.queue.append((url, url))
        self.log("info", f"Manually queued: {url}")

    # ─── OmniFile / Viewer generation ─────────────────────────────────────

    def _opt_add_category(self):
        self.show_options = False
        self._start_input("New category name: ", self._do_add_category)

    def _do_add_category(self, name: str):
        name = name.strip().lower().replace(" ", "-")
        if not name:
            return
        if name not in self.config.categories:
            self.config.categories.append(name)
            self.log("ok", f"Category added: {name}")
        else:
            self.log("info", f"Category already exists: {name}")

    def _opt_delete_category(self):
        cats = self.config.categories
        if not cats:
            return
        idx = min(self.opt_cursor, len(cats) - 1)
        removed = cats.pop(idx)
        self.log("warn", f"Category removed: {removed}")
        self.opt_cursor = max(0, self.opt_cursor - 1)

    def _batch_generate_viewers(self):
        """Generate OmniFile + viewer.html for all completed items in a background thread."""
        import threading as _threading
        items = list(self.completed)
        if not items:
            self.log("warn", "No completed items to generate viewers for")
            return
        self.log("info", f"Batch generating viewers for {len(items)} items...")
        def _work():
            from .omni import write_omni
            from .viewer import generate_viewer
            ok = 0
            for r in items:
                if not r.folder:
                    continue
                try:
                    write_omni(r.folder)
                    generate_viewer(r.folder, bake=False)
                    ok += 1
                except Exception as e:
                    self.log("warn", f"Viewer gen failed for {r.video_id}: {e}")
            self.log("ok", f"Batch complete: {ok}/{len(items)} viewers generated")
        _threading.Thread(target=_work, daemon=True).start()

    def _generate_omni_for_selected(self):
        r = self._selected_completed()
        if not r or not r.folder:
            self.log("warn", "No completed item selected or no folder path")
            return
        try:
            from .omni import write_omni
            path = write_omni(r.folder)
            if path:
                self.log("ok", f"OmniFile: {path}")
            else:
                self.log("error", f"OmniFile generation failed for {r.folder}")
        except Exception as e:
            self.log("error", f"OmniFile error: {e}")

    def _generate_viewer_for_selected(self, bake: bool = False):
        r = self._selected_completed()
        if not r or not r.folder:
            self.log("warn", "No completed item selected or no folder path")
            return
        try:
            from .viewer import generate_viewer
            path = generate_viewer(r.folder, bake=bake)
            if path:
                mode = "baked" if bake else "live"
                self.log("ok", f"Viewer ({mode}): {path}")
                if not bake:
                    self.log("info", f"Tip: cd '{r.folder}' && python3 -m http.server 9000")
            else:
                self.log("error", f"Viewer generation failed for {r.folder}")
        except Exception as e:
            self.log("error", f"Viewer error: {e}")

    # ─── Rendering ────────────────────────────────────────────────────────

    def _render_watch_panel(self) -> Panel:
        lines = []
        for src in self.config.sources[:5]:
            icon = {"playlist": "[blue]PL[/]", "channel": "[red]CH[/]",
                    "video": "[green]VID[/]"}.get(src.type, "[dim]??[/]")
            enabled = "" if src.enabled else " [dim][off][/]"
            lines.append(f"  {icon} {src.name[:28]}{enabled}")
        if not self.config.sources:
            lines.append("  [dim]No sources — press o > Sources > a to add[/]")
        lines.append("")
        lines.append(f"  Last check: [cyan]{self.watcher.last_check or 'never'}[/]")
        lines.append(f"  Checks:     {self.watcher.check_count}")
        lines.append(f"  Paused:     {'[yellow]YES[/]' if self.watcher.paused else '[green]no[/]'}")
        lines.append("")
        lines.append(f"  Queue:    [yellow]{len(self.queue)}[/] waiting")
        lines.append(f"  Archived: [green]{self.total_archived}[/]")
        lines.append(f"  Failed:   [red]{self.total_failed}[/]")
        border = "cyan" if self.focus_panel == 0 else "dim"
        return Panel("\n".join(lines), title="[bold cyan]Watch Status[/]",
                     border_style=border, height=16)

    def _render_workers_panel(self) -> Panel:
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
                    "transcript": "T", "metadata": "M", "player_data": "P",
                    "download": "D", "screenshots": "S", "clips": "C",
                    "keypoints": "K", "tracker": "X", "omni": "O",
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
        return Panel("\n".join(lines),
                     title=f"[bold yellow]Workers ({active}/{len(self.workers)})[/]",
                     border_style=border, height=16)

    def _render_completed_panel(self) -> Panel:
        table = Table(expand=True, show_header=True, header_style="bold")
        table.add_column("Title", ratio=3, no_wrap=True)
        table.add_column("Cat", ratio=1, no_wrap=True)
        table.add_column("SS", justify="right", ratio=1)
        table.add_column("Clips", justify="right", ratio=1)
        table.add_column("Status", ratio=1)

        items = list(self.completed)[:10]
        focused = self.focus_panel == 2
        if focused and self.completed_cursor >= len(items) and items:
            self.completed_cursor = len(items) - 1

        for i, r in enumerate(items):
            status_str = {
                "success": "[green]OK[/]", "partial": "[yellow]PART[/]",
                "failed": "[red]FAIL[/]", "skipped": "[dim]SKIP[/]",
            }.get(r.status, "[dim]?[/]")
            title = (r.title or r.video_id or "?")[:40]
            selected = focused and i == self.completed_cursor
            prefix = "[bold cyan]▶[/] " if selected else "  "
            table.add_row(
                f"{prefix}{title}",
                r.category[:10],
                str(r.screenshot_count),
                str(r.clip_count),
                status_str,
            )

        if not items:
            table.add_row("[dim]No videos archived yet...[/]", "", "", "", "")

        nav_hint = " [dim](↑↓ navigate, Enter=detail, v=viewer, g=omni)[/]" if focused else ""
        border = "green" if focused else "dim"
        return Panel(table, title=f"[bold green]Recently Completed[/]{nav_hint}",
                     border_style=border, height=12)

    def _render_log_panel(self) -> Panel:
        all_lines = list(self.log_lines)
        # log_scroll: 0 = live tail; positive = scrolled back
        total = len(all_lines)
        window = 15
        if self.log_scroll > 0:
            end = max(0, total - self.log_scroll)
            start = max(0, end - window)
            shown = all_lines[start:end]
            scroll_hint = f" [dim](scroll ↑{self.log_scroll} lines)[/]"
        else:
            shown = all_lines[-window:]
            scroll_hint = ""

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

        focused = self.focus_panel == 3
        border = "blue" if focused else "dim"
        nav_hint = f"{scroll_hint} [dim](↑↓ scroll)[/]" if focused else scroll_hint
        return Panel(
            "\n".join(lines) if lines else "[dim]Waiting for log output...[/]",
            title=f"[bold blue]Live Log[/]{nav_hint}",
            border_style=border, height=10,
        )

    def _render_footer(self) -> Text:
        if self.input_mode:
            return Text.from_markup(
                f"  [bold yellow]{self.input_prompt}[/]{self.input_buf}[bold yellow]█[/]"
                f"  [dim](Enter to submit, Esc to cancel)[/]"
            )
        active = sum(1 for w in self.workers if w.busy)
        return Text.from_markup(
            f"  [bold cyan]p[/]ause [bold cyan]r[/]etry [bold cyan]s[/]kip "
            f"[bold cyan]n[/]ow [bold cyan]a[/]dd [bold cyan]o[/]ptions "
            f"[bold cyan]v[/]iewer [bold cyan]g[/]=omni [bold cyan]G[/]=all "
            f"[bold cyan]?[/]help [bold cyan]q[/]uit  "
            f"W:[bold]{active}[/]/{len(self.workers)} "
            f"Q:[bold yellow]{len(self.queue)}[/] "
            f"Done:[bold green]{self.total_archived}[/] "
            f"Fail:[bold red]{self.total_failed}[/]"
        )

    def _render_help_overlay(self) -> Panel:
        help_text = """
[bold]Keyboard Controls[/]

  [cyan]p[/]      Pause / resume watcher
  [cyan]r[/]      Retry all failed videos
  [cyan]s[/]      Skip current worker
  [cyan]n[/]      Force playlist check now
  [cyan]a[/]      Add URL to queue manually
  [cyan]o[/]      Open options / settings screen
  [cyan]?[/]      Show / hide this help
  [cyan]q[/]      Quit gracefully

  [cyan]Tab[/]    Switch panel focus
  [cyan]↑↓[/]     Navigate completed list
  [cyan]Enter[/]  Open detail view for selected video

  [bold]OmniFile & Viewer[/]
  [cyan]g[/]      Generate OmniFile for selected video
  [cyan]v[/]      Generate live viewer.html for selected video
  [cyan]G[/]      Batch-generate viewers for ALL completed videos

  [bold]Detail view (Enter)[/]
  [cyan]Tab[/]    Switch: Overview / Transcript / Key Points
  [cyan]v[/]      Generate live viewer
  [cyan]b[/]      Generate baked (self-contained) viewer
  [cyan]g[/]      Generate OmniFile

  [bold]Options screen (o)[/]
  [cyan]Tab[/]    Switch tab: Pipeline / Watch / Sources / Categories
  [cyan]↑↓[/]     Navigate items in current tab
  [cyan]Enter[/]  Cycle / toggle / start editing a value
  [cyan]s[/]      Save config.yaml
  [cyan]a/d[/]    Add / Delete (Sources and Categories tabs)

  [bold]Log panel (Tab to focus)[/]
  [cyan]↑↓[/]     Scroll through log history

Press [bold]?[/] to close.
"""
        return Panel(Align.center(help_text, vertical="middle"),
                     title="[bold magenta]Help — NuxTube[/]", border_style="magenta")

    def _render_options_overlay(self) -> Panel:
        """Render the full options/settings screen."""
        # Tab bar
        tab_row = "  "
        for i, name in enumerate(OPT_TABS):
            if i == self.opt_tab:
                tab_row += f"[bold reverse] {name} [/]  "
            else:
                tab_row += f"[dim] {name} [/]  "

        lines = [tab_row, ""]

        if self.opt_tab == 0:
            lines += self._render_opts_pipeline()
        elif self.opt_tab == 1:
            lines += self._render_opts_watch()
        elif self.opt_tab == 2:
            lines += self._render_opts_sources()
        else:
            lines += self._render_opts_info()

        lines += [
            "",
            "[dim]  ↑↓ Navigate  Enter/Space Edit  Tab Next-tab  s Save  Esc Close[/]",
        ]

        return Panel(
            "\n".join(lines),
            title="[bold magenta]⚙  Options[/]",
            border_style="magenta",
        )

    def _render_opts_pipeline(self) -> List[str]:
        nav_idx = 0
        lines = []
        for opt in PIPELINE_OPTS:
            t = opt["type"]
            if t == "header":
                lines.append(f"\n  [bold dim]{opt['label']}[/]")
                continue

            selected = nav_idx == self.opt_cursor
            arrow = "[bold cyan]▶[/]" if selected else " "

            if t == "stage":
                enabled = opt["stage"] in self.config.pipeline.stages
                val_str = "[green]✓ on [/]" if enabled else "[dim]  off[/]"
                lines.append(f"  {arrow} {opt['label']:<22} {val_str}")

            elif t == "bool":
                val = self._get_opt_value(opt)
                val_str = "[green]YES[/]" if val else "[dim]NO [/]"
                lines.append(f"  {arrow} {opt['label']:<22} {val_str}")

            elif t == "cycle":
                val = self._get_opt_value(opt)
                choices = opt.get("choices", [])
                parts = []
                for c in choices:
                    if c == val:
                        parts.append(f"[bold cyan][{c}][/]")
                    else:
                        parts.append(f"[dim]{c}[/]")
                val_str = " ".join(parts)
                lines.append(f"  {arrow} {opt['label']:<22} {val_str}")

            elif t == "int":
                if selected and self.opt_editing:
                    val_str = f"[bold yellow]{self.opt_edit_buf}[bold yellow]█[/][/]"
                else:
                    val = self._get_opt_value(opt)
                    val_str = f"[cyan]{val}[/]"
                lines.append(f"  {arrow} {opt['label']:<22} {val_str}")

            nav_idx += 1
        return lines

    def _render_opts_watch(self) -> List[str]:
        nav = self._navigable_opts()
        lines = ["  [bold]Watch / Scheduler Settings[/]", ""]
        for i, opt in enumerate(WATCH_OPTS):
            selected = i == self.opt_cursor
            arrow = "[bold cyan]▶[/]" if selected else " "
            t = opt["type"]
            if t == "bool":
                val = self._get_opt_value(opt)
                val_str = "[green]YES[/]" if val else "[dim]NO [/]"
            elif t == "int":
                if selected and self.opt_editing:
                    val_str = f"[bold yellow]{self.opt_edit_buf}█[/]"
                else:
                    val_str = f"[cyan]{self._get_opt_value(opt)}[/]"
            else:
                val_str = str(self._get_opt_value(opt))
            lines.append(f"  {arrow} {opt['label']:<24} {val_str}")
        lines += [
            "",
            "  [dim]Changes take effect after saving (s). Workers restart on next run.[/]",
        ]
        return lines

    def _render_opts_sources(self) -> List[str]:
        srcs = self.config.sources
        lines = [
            f"  [bold]Sources ({len(srcs)})[/]",
            f"  [dim]a=Add  d=Delete  Enter=Toggle enabled[/]",
            "",
        ]
        if not srcs:
            lines.append("  [dim]No sources. Press [bold]a[/] to add a playlist or channel.[/]")
        for i, src in enumerate(srcs):
            selected = i == self.opt_cursor
            arrow = "[bold cyan]▶[/]" if selected else " "
            icon = {"playlist": "[blue]PL[/]", "channel": "[red]CH[/]",
                    "video": "[green]VID[/]"}.get(src.type, "[dim]??[/]")
            enabled = "[green]enabled[/]" if src.enabled else "[dim]disabled[/]"
            lines.append(f"  {arrow} {icon} {src.name[:34]:<36} {enabled}")
            lines.append(f"       [dim]{src.url[:60]}[/]")
            lines.append("")
        return lines

    def _render_opts_info(self) -> List[str]:
        import sys as _sys
        from . import __version__
        out_dir = str(self.config.output_path.resolve())
        cfg_path = str(self.config.config_path)
        tracker_path = str(self.pipeline.tracker.csv_path)
        lines = [
            "  [bold]System Info[/]",
            "",
            f"  NuxTube:   [cyan]v{__version__}[/]",
            f"  Python:    [dim]{_sys.version.split()[0]}[/]",
            "",
            f"  Config:    [dim]{cfg_path}[/]",
            f"  Output:    [dim]{out_dir}[/]",
            f"  Tracker:   [dim]{tracker_path}[/]",
            "",
            "  [bold]Pipeline Stages Active:[/]",
        ]
        for s in self.config.pipeline.stages:
            lines.append(f"    [cyan]•[/] {s}")
        lines += [
            "",
            f"  [bold]Categories ({len(self.config.categories)})[/]  [dim]a=Add  d=Delete[/]",
        ]
        for i, cat in enumerate(self.config.categories):
            selected = i == self.opt_cursor
            arrow = "[bold cyan]▶[/]" if selected else " "
            lines.append(f"  {arrow} {cat}")
        lines += [
            "",
            "  [dim]G=Generate viewers for all completed  s=Save config[/]",
        ]
        return lines

    def _render_detail_overlay(self) -> Panel:
        """Detail view for the selected completed item."""
        r = self._selected_completed()
        if not r:
            return Panel("[dim]No item selected[/]", title="Detail", border_style="dim")

        # Tab bar
        tab_names = ["Overview", "Transcript", "Key Points"]
        tab_row = "  "
        for i, name in enumerate(tab_names):
            if i == self.detail_tab:
                tab_row += f"[bold reverse] {name} [/]  "
            else:
                tab_row += f"[dim] {name} [/]  "

        lines = [tab_row, ""]

        if self.detail_tab == 0:
            lines += self._render_detail_overview(r)
        elif self.detail_tab == 1:
            lines += self._render_detail_transcript(r)
        else:
            lines += self._render_detail_keypoints(r)

        lines += [
            "",
            "[dim]  Tab=next-tab  g=OmniFile  v=Viewer  b=Baked-viewer  Esc=close[/]",
        ]
        title = (r.title or r.video_id or "?")[:50]
        return Panel("\n".join(lines), title=f"[bold green]Detail: {title}[/]",
                     border_style="green")

    def _render_detail_overview(self, r: ArchiveResult) -> List[str]:
        status_map = {
            "success": "[green]SUCCESS[/]", "partial": "[yellow]PARTIAL[/]",
            "failed": "[red]FAILED[/]", "skipped": "[dim]SKIPPED[/]",
        }
        lines = [
            f"  Title:      [bold]{r.title or '?'}[/]",
            f"  Video ID:   [cyan]{r.video_id or '?'}[/]",
            f"  URL:        [dim]{r.url or '?'}[/]",
            f"  Category:   [cyan]{r.category or '?'}[/]",
            f"  Duration:   {r.duration or '?'}",
            f"  Status:     {status_map.get(r.status, r.status)}",
            f"  Archived:   {r.timestamp[:19] if r.timestamp else '?'}",
            "",
            f"  Screenshots: [green]{r.screenshot_count}[/]",
            f"  Clips:       [cyan]{r.clip_count}[/]",
            f"  Segments:    {r.segment_count}",
            "",
            f"  Stages done: [dim]{', '.join(r.stages_completed) or 'none'}[/]",
        ]
        if r.errors:
            lines += ["", "  [bold red]Errors:[/]"]
            for e in r.errors[:3]:
                lines.append(f"    [red]• {e[:70]}[/]")
        if r.folder:
            lines += ["", f"  Folder: [dim]{r.folder}[/]"]
            omni_exists = os.path.exists(os.path.join(r.folder, "omni.json"))
            viewer_exists = os.path.exists(os.path.join(r.folder, "viewer.html"))
            lines.append(
                f"  omni.json:   {'[green]exists[/]' if omni_exists else '[dim]not yet[/]'}"
                f"  viewer.html: {'[green]exists[/]' if viewer_exists else '[dim]not yet[/]'}"
            )
        return lines

    def _render_detail_transcript(self, r: ArchiveResult) -> List[str]:
        if not r.folder:
            return ["  [dim]No folder path available[/]"]
        tr_path = os.path.join(r.folder, "transcript.md")
        if not os.path.exists(tr_path):
            return ["  [dim]No transcript.md found[/]"]
        try:
            raw = open(tr_path, encoding="utf-8", errors="replace").read()
            # Show just the timestamped section (first 1500 chars)
            idx = raw.find("## Timestamped")
            if idx >= 0:
                snippet = raw[idx:idx + 1500]
            else:
                snippet = raw[:1500]
            lines = ["  [bold]Transcript preview:[/]", ""]
            for line in snippet.split("\n")[:25]:
                if line.startswith("#"):
                    lines.append(f"  [bold]{line}[/]")
                elif line.startswith("["):
                    lines.append(f"  [cyan]{line}[/]")
                else:
                    lines.append(f"  {line}")
            if len(raw) > 1500:
                lines.append("  [dim]... (open viewer.html for full transcript)[/]")
        except Exception as e:
            lines = [f"  [red]Error reading transcript: {e}[/]"]
        return lines

    def _render_detail_keypoints(self, r: ArchiveResult) -> List[str]:
        if not r.folder:
            return ["  [dim]No folder path available[/]"]
        kp_path = os.path.join(r.folder, "key-points.json")
        if not os.path.exists(kp_path):
            return ["  [dim]No key-points.json found[/]",
                    "  [dim](keypoints stage may not have run)[/]"]
        try:
            import json
            data = json.load(open(kp_path, encoding="utf-8"))
            lines = []
            if data.get("summary"):
                lines += ["  [bold]Summary:[/]", f"  [dim]{data['summary'][:300]}[/]", ""]
            kps = data.get("key_points", [])
            lines.append(f"  [bold]{len(kps)} Key Points:[/]")
            for i, kp in enumerate(kps[:10]):
                ts = kp.get("timestamp", "?")
                if isinstance(ts, (int, float)):
                    m, s = int(ts) // 60, int(ts) % 60
                    ts_fmt = f"{m}:{s:02d}"
                else:
                    ts_fmt = str(ts)
                imp = kp.get("importance", "")
                imp_color = {"high": "red", "medium": "yellow", "low": "green"}.get(imp, "dim")
                title = kp.get("title", f"Point {i+1}")[:50]
                lines.append(f"  [cyan]{ts_fmt:>6}[/]  [{imp_color}]●[/] {title}")
            if len(kps) > 10:
                lines.append(f"  [dim]... and {len(kps)-10} more[/]")
        except Exception as e:
            lines = [f"  [red]Error: {e}[/]"]
        return lines

    def _render(self) -> Layout:
        # Overlay modes
        if self.show_help:
            return Layout(self._render_help_overlay())
        if self.show_options:
            return Layout(self._render_options_overlay())
        if self.show_detail:
            return Layout(self._render_detail_overlay())

        # Normal layout
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
        return layout

    # ─── Main loop ────────────────────────────────────────────────────────

    def run(self):
        self.running = True
        self.log("info", "NuxTube TUI starting up...")
        self.log("info", f"Watching {len(self.config.sources)} source(s)")
        self.log("info", f"Workers: {self.config.watch.max_workers}, Poll: {self.config.watch.poll_interval}s")
        self.log("info", "Keys: o=options  a=add-url  v=viewer  g=omni  ?=help")

        self.watcher.start()

        worker_threads = []
        for w in self.workers:
            t = threading.Thread(target=self._worker_loop, args=(w,), daemon=True)
            t.start()
            worker_threads.append(t)

        def cleanup():
            self.running = False
            self.watcher.stop()
            fd = sys.stdin.fileno()
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, termios.tcgetattr(fd))
            except Exception:
                pass

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

        print()
        print("=" * 50)
        print("  NuxTube session summary")
        print("=" * 50)
        print(f"  Archived:  {self.total_archived}")
        print(f"  Failed:    {self.total_failed}")
        print(f"  In queue:  {len(self.queue)}")
        print(f"  Watcher checks: {self.watcher.check_count}")
        print()
