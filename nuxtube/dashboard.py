#!/usr/bin/env python3
"""NuxTube Web Dashboard — lightweight HTTP server for monitoring + control.

Serves a single-page dashboard and a JSON REST API. No external dependencies
beyond the Python stdlib http.server — designed to be zero-friction.

API Endpoints:
  GET  /                  — HTML dashboard (auto-refreshing)
  GET  /api/status        — Full daemon status as JSON
  GET  /api/results       — Recent archive results
  GET  /api/log           — Recent log lines
  POST /api/queue         — Add video to queue     {"url": "...", "title": "..."}
  POST /api/pause         — Pause watcher
  POST /api/resume        — Resume watcher
  POST /api/retry         — Retry failed videos
  POST /api/skip          — Skip worker             {"worker": 0}
  POST /api/check         — Force playlist check now
  GET  /api/health        — Health check

Usage:
  python3 nuxtube.py --web 8080              # Web dashboard on port 8080
  python3 nuxtube.py --daemon --web 8080     # Headless daemon + web dashboard
  curl http://localhost:8080/api/status      # Query status from scripts
"""
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from typing import Optional

from .middleware import NuxTubeDaemon


# ─── HTML Dashboard (single-page, auto-refreshing) ───

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NuxTube Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #c9d1d9; --dim: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --purple: #bc8cff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--bg); color: var(--text);
    font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14px; padding: 16px;
  }
  h1 { font-size: 20px; margin-bottom: 12px; }
  h1 span { color: var(--accent); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
  .panel {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px;
  }
  .panel h2 { font-size: 14px; color: var(--dim); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
  .full { grid-column: 1 / -1; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--dim); font-weight: 600; padding: 4px 8px; border-bottom: 1px solid var(--border); font-size: 12px; }
  td { padding: 4px 8px; border-bottom: 1px solid var(--border); }
  .stat { display: inline-block; margin-right: 16px; }
  .stat .num { font-size: 24px; font-weight: 700; }
  .stat .label { font-size: 11px; color: var(--dim); text-transform: uppercase; }
  .ok { color: var(--green); } .warn { color: var(--yellow); } .err { color: var(--red); }
  .dim { color: var(--dim); }
  .log { max-height: 300px; overflow-y: auto; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
  .log-line { padding: 2px 0; }
  .bar { display: inline-block; width: 100px; height: 10px; background: var(--border); border-radius: 5px; overflow: hidden; vertical-align: middle; }
  .bar-fill { height: 100%; background: var(--accent); transition: width 0.3s; }
  .controls { margin: 12px 0; display: flex; gap: 8px; }
  button {
    background: var(--surface); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px;
  }
  button:hover { border-color: var(--accent); }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .badge-ok { background: rgba(63,185,80,0.2); color: var(--green); }
  .badge-warn { background: rgba(210,153,34,0.2); color: var(--yellow); }
  .badge-err { background: rgba(248,81,73,0.2); color: var(--red); }
  .badge-dim { background: rgba(139,148,158,0.2); color: var(--dim); }
  .badge-blue { background: rgba(88,166,255,0.2); color: var(--accent); }
  #queue-input { flex: 1; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; font-size: 13px; }
</style>
</head>
<body>
<h1>🎬 <span>NuxTube</span> Dashboard</h1>

<div class="controls">
  <input id="queue-input" placeholder="Paste YouTube URL to queue...">
  <button onclick="queueUrl()">Queue</button>
  <button onclick="api('pause')">⏸ Pause</button>
  <button onclick="api('resume')">▶ Resume</button>
  <button onclick="api('retry')">🔄 Retry</button>
  <button onclick="api('check')">🔍 Check Now</button>
</div>

<div class="grid">
  <div class="panel">
    <h2>📊 Stats</h2>
    <div id="stats"></div>
  </div>
  <div class="panel">
    <h2>👀 Watcher</h2>
    <div id="watcher"></div>
  </div>
</div>

<div class="grid">
  <div class="panel">
    <h2>⚙️ Workers</h2>
    <div id="workers"></div>
  </div>
  <div class="panel">
    <h2>📋 Queue</h2>
    <div id="queue"></div>
  </div>
</div>

<div class="panel" style="margin-bottom: 12px;">
  <h2>✅ Recently Completed</h2>
  <div id="completed"></div>
</div>

<div class="panel" style="margin-bottom: 12px;">
  <h2 style="display:flex;justify-content:space-between;align-items:center;">
    📂 Archive Browser
    <span style="font-size:11px;color:var(--dim);font-weight:400;display:flex;gap:10px;align-items:center;">
      <button onclick="api('generate-omni');this.textContent='Generating…'" style="background:var(--surface2);color:var(--accent);border:1px solid var(--accent);border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;">⚙ Generate All OmniFiles</button>
      <a href="/viewer" target="_blank" style="color:var(--accent);">Open viewer ↗</a>
    </span>
  </h2>
  <div style="margin-bottom:8px;">
    <input id="search-archive" placeholder="Filter by title or category..."
      style="width:100%;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:13px;"
      oninput="filterArchive(this.value)">
  </div>
  <div id="archive-list" style="max-height:400px;overflow-y:auto;"></div>
</div>

<div class="panel">
  <h2>📝 Live Log</h2>
  <div class="log" id="log"></div>
</div>

<script>
async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    renderStats(d.stats);
    renderWatcher(d.watcher, d.paused);
    renderWorkers(d.workers);
    renderQueue(d.queue);
    renderCompleted(d.completed);
    renderLog(d.log);
  } catch(e) { console.error(e); }
}

function renderStats(s) {
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="num ok">${s.total_archived}</div><div class="label">Archived</div></div>
    <div class="stat"><div class="num err">${s.total_failed}</div><div class="label">Failed</div></div>
    <div class="stat"><div class="num">${s.total_queued}</div><div class="label">Total Queued</div></div>
    <div class="stat"><div class="num dim">${s.uptime}</div><div class="label">Uptime</div></div>
  `;
}

function renderWatcher(w, paused) {
  const sources = w.sources.map(s => `<span class="badge badge-${s.type==='channel'?'warn':'blue'}">${s.type==='playlist'?'PL':s.type==='channel'?'CH':'VID'}</span> ${s.name}`).join('<br>');
  document.getElementById('watcher').innerHTML = `
    <div>State: ${paused?'<span class="badge badge-warn">PAUSED</span>':'<span class="badge badge-ok">ACTIVE</span>'}</div>
    <div class="dim">Last check: ${w.last_check||'never'} | Checks: ${w.check_count}</div>
    <div style="margin-top:8px;">${sources||'<span class=dim>No sources</span>'}</div>
  `;
}

function renderWorkers(workers) {
  const html = workers.map(w => {
    if (!w.busy) return `<div class="dim">W${w.id}: idle</div>`;
    const pct = Math.round(w.progress / Math.max(w.total,1) * 100);
    return `<div><strong>W${w.id}</strong> <span class="bar"><span class="bar-fill" style="width:${pct}%"></span></span> ${pct}% <span class="badge badge-blue">${w.stage}</span><br><span class="dim">${(w.title||'').substring(0,40)}</span></div>`;
  }).join('<br>');
  document.getElementById('workers').innerHTML = html;
}

function renderQueue(q) {
  if (!q.count) { document.getElementById('queue').innerHTML = '<span class="dim">Queue empty</span>'; return; }
  const items = q.items.map(i => `<div>${i.title.substring(0,50)}</div>`).join('');
  document.getElementById('queue').innerHTML = `<div class="warn">${q.count} waiting</div>${items}`;
}

function renderCompleted(items) {
  if (!items.length) { document.getElementById('completed').innerHTML = '<span class="dim">Nothing yet...</span>'; return; }
  const rows = items.map(r => {
    const badge = r.status==='success'?'ok':r.status==='partial'?'warn':'err';
    const vid = r.video_id||'';
    const viewLink = vid ? `<a href="/viewer?video_id=${encodeURIComponent(vid)}" target="_blank" style="font-size:11px;color:var(--accent);">View ↗</a>` : '';
    return `<tr>
      <td>${(r.title||'').substring(0,45)}</td>
      <td class="dim">${r.category}</td>
      <td>${r.screenshot_count}</td>
      <td>${r.clip_count}</td>
      <td><span class="badge badge-${badge}">${r.status.toUpperCase()}</span></td>
      <td>${viewLink}</td>
    </tr>`;
  }).join('');
  document.getElementById('completed').innerHTML = `<table>
    <tr><th>Title</th><th>Cat</th><th>SS</th><th>Clips</th><th>Status</th><th>Viewer</th></tr>
    ${rows}
  </table>`;
}

function renderLog(lines) {
  const html = lines.slice(-30).map(l => {
    const cls = l.includes('ERROR')?'err':l.includes('WARN')?'warn':l.includes('OK')?'ok':'dim';
    return `<div class="log-line ${cls}">${l}</div>`;
  }).join('');
  document.getElementById('log').innerHTML = html;
}

async function queueUrl() {
  const url = document.getElementById('queue-input').value;
  if (!url) return;
  await fetch('/api/queue', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url})});
  document.getElementById('queue-input').value = '';
}

async function api(action) {
  await fetch('/api/' + action, {method:'POST'});
}

// ── Archive browser ──
let _allVideos = [];
async function loadArchive() {
  try {
    const r = await fetch('/api/videos');
    const d = await r.json();
    _allVideos = d.videos || [];
    renderArchive(_allVideos);
  } catch(e) {}
}
function filterArchive(q) {
  if (!q.trim()) { renderArchive(_allVideos); return; }
  const lq = q.toLowerCase();
  renderArchive(_allVideos.filter(v =>
    (v.title||'').toLowerCase().includes(lq) ||
    (v.category||'').toLowerCase().includes(lq) ||
    (v.channel||'').toLowerCase().includes(lq)
  ));
}
function renderArchive(videos) {
  const el = document.getElementById('archive-list');
  if (!videos.length) { el.innerHTML='<span class="dim">No archived videos found.</span>'; return; }
  el.innerHTML = videos.map(v => {
    const thumb = v.thumbnail_url
      ? `<img src="${v.thumbnail_url}" style="width:80px;height:45px;object-fit:cover;border-radius:4px;flex-shrink:0;" onerror="this.style.display='none'">`
      : `<div style="width:80px;height:45px;background:var(--border);border-radius:4px;flex-shrink:0;"></div>`;
    const catBadge = v.category ? `<span class="badge badge-blue" style="font-size:10px;">${v.category}</span>` : '';
    const dur = v.duration ? `<span class="dim" style="font-size:11px;">${v.duration}</span>` : '';
    const ssInfo = v.screenshot_count ? `${v.screenshot_count} SS` : '';
    const clInfo = v.clip_count ? `${v.clip_count} clips` : '';
    const omniBadge = v.has_omni ? '<span style="color:var(--green);font-size:10px;">●omni</span>' : '';
    return `
      <div style="display:flex;gap:10px;align-items:center;padding:8px;border-bottom:1px solid var(--border);">
        <a href="/viewer?video_id=${encodeURIComponent(v.video_id)}" target="_blank">${thumb}</a>
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
            <a href="/viewer?video_id=${encodeURIComponent(v.video_id)}" target="_blank"
               style="color:var(--text);text-decoration:none;"
               onmouseover="this.style.color='var(--accent)'" onmouseout="this.style.color='var(--text)'">
              ${(v.title||'Unknown').substring(0,60)}
            </a>
          </div>
          <div style="display:flex;gap:8px;align-items:center;margin-top:3px;flex-wrap:wrap;">
            ${catBadge} ${dur}
            ${ssInfo?`<span class="dim" style="font-size:11px;">${ssInfo}</span>`:''}
            ${clInfo?`<span class="dim" style="font-size:11px;">${clInfo}</span>`:''}
            ${omniBadge}
          </div>
        </div>
        <div style="flex-shrink:0;">
          <a href="/viewer?video_id=${encodeURIComponent(v.video_id)}" target="_blank"
             style="font-size:11px;color:var(--accent);white-space:nowrap;">View ↗</a>
        </div>
      </div>`;
  }).join('');
}

fetchStatus();
setInterval(fetchStatus, 2000);
loadArchive();
setInterval(loadArchive, 30000);
</script>
</body>
</html>"""


# ─── HTTP Handler ───

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the web dashboard."""

    daemon: NuxTubeDaemon = None  # Set by DashboardServer

    def log_message(self, format, *args):
        pass  # Suppress default logging

    def _json(self, data, code=200):
        body = json.dumps(data, indent=2, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html, code=200):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, abs_path: str):
        """Serve a static file with correct MIME type."""
        mime, _ = mimetypes.guess_type(abs_path)
        mime = mime or "application/octet-stream"
        try:
            size = os.path.getsize(abs_path)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "max-age=3600")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(abs_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _resolve_static(self, rel: str) -> Optional[str]:
        """Resolve a /files/ relative path to absolute, checking it's within output_dir."""
        if not self.daemon:
            return None
        out_dir = os.path.realpath(self.daemon.config.output_dir)
        # Strip leading slashes and sanitize
        parts = [p for p in rel.split("/") if p and p not in ("..", ".")]
        if not parts:
            return None
        abs_path = os.path.realpath(os.path.join(out_dir, *parts))
        if not abs_path.startswith(out_dir + os.sep) and abs_path != out_dir:
            return None
        if not os.path.isfile(abs_path):
            return None
        return abs_path

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._html(DASHBOARD_HTML)

        elif path == "/viewer":
            from .viewer import VIEWER_HTML
            self._html(VIEWER_HTML)

        elif path == "/api/status":
            self._json(self.daemon.status() if self.daemon else {"error": "no daemon"})

        elif path == "/api/results":
            limit = int(qs.get("limit", ["50"])[0])
            self._json({"results": self.daemon.results(limit)} if self.daemon else {"results": []})

        elif path == "/api/log":
            status = self.daemon.status() if self.daemon else {"log": []}
            self._json({"log": status.get("log", [])})

        elif path == "/api/health":
            self._json({"status": "ok", "running": self.daemon.running if self.daemon else False})

        elif path == "/api/videos":
            limit = int(qs.get("limit", ["200"])[0])
            if self.daemon:
                self._json({"videos": self.daemon.list_all_videos(limit)})
            else:
                self._json({"videos": []})

        elif path == "/api/omni":
            video_id = qs.get("video_id", [None])[0]
            if not video_id or not self.daemon:
                self._json({"error": "missing video_id or no daemon"}, 400)
                return
            omni = self.daemon.get_video_omni(video_id)
            if not omni:
                self._json({"error": "video not found", "video_id": video_id}, 404)
                return
            # Inject _web_base_url so the viewer can resolve relative file paths
            folder = self.daemon.find_video_folder(video_id)
            if folder:
                out_dir = self.daemon.config.output_dir
                try:
                    import pathlib
                    rel = pathlib.Path(folder).relative_to(pathlib.Path(out_dir))
                    omni["_web_base_url"] = f"/files/{rel.as_posix()}/"
                except ValueError:
                    pass
            self._json(omni)

        elif path.startswith("/files/"):
            rel = path[7:]  # strip /files/
            abs_path = self._resolve_static(rel)
            if abs_path:
                self._serve_file(abs_path)
            else:
                self._json({"error": "not found"}, 404)

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length else "{}"
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        if not self.daemon:
            self._json({"error": "no daemon"}, 500)
            return

        if path == "/api/queue":
            url = data.get("url", "")
            title = data.get("title", "")
            if url:
                self.daemon.queue_url(url, title)
                self._json({"ok": True, "message": f"Queued: {url}"})
            else:
                self._json({"error": "missing url"}, 400)

        elif path == "/api/pause":
            self.daemon.pause()
            self._json({"ok": True, "message": "Paused"})

        elif path == "/api/resume":
            self.daemon.resume()
            self._json({"ok": True, "message": "Resumed"})

        elif path == "/api/retry":
            count = self.daemon.retry_failed()
            self._json({"ok": True, "requeued": count})

        elif path == "/api/skip":
            worker = data.get("worker", -1)
            ok = self.daemon.skip_worker(worker)
            self._json({"ok": ok} if ok else {"error": "no busy worker"})

        elif path == "/api/check":
            self.daemon.check_now()
            self._json({"ok": True, "message": "Check triggered"})

        elif path == "/api/generate-omni":
            # Batch-generate omni.json for all archived videos in background
            def _gen_all():
                from .omni import write_omni
                from pathlib import Path
                out_dir = Path(self.daemon.config.output_dir)
                count = 0
                for meta_path in out_dir.glob("*/*/metadata.json"):
                    try:
                        write_omni(str(meta_path.parent))
                        count += 1
                    except Exception:
                        pass
                self.daemon._log_callback("ok", f"Generated {count} OmniFile(s)")
            threading.Thread(target=_gen_all, daemon=True).start()
            self._json({"ok": True, "message": "OmniFile generation started in background"})

        else:
            self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server."""
    daemon_threads = True


class DashboardServer:
    """Web dashboard server. Wraps HTTPServer with daemon reference."""

    def __init__(self, daemon: NuxTubeDaemon, port: int = 8080, host: str = "0.0.0.0"):
        self.daemon = daemon
        self.port = port
        self.host = host
        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the web server in a background thread."""
        DashboardHandler.daemon = self.daemon
        self.server = ThreadingHTTPServer((self.host, self.port), DashboardHandler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        url = f"http://localhost:{self.port}"
        print(f"\n  Dashboard:   {url}")
        print(f"  Viewer:      {url}/viewer")
        print(f"  Videos API:  {url}/api/videos")
        print(f"  OmniFile:    {url}/api/omni?video_id=<id>")
        print(f"  Static:      {url}/files/<category>/<slug>/screenshots/")
        print(f"  Status API:  {url}/api/status")
        print()

    def stop(self):
        """Stop the web server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
