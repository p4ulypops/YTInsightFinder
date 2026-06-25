#!/usr/bin/env python3
"""NuxTube HTML Viewer — generates a self-contained viewer.html for a video archive.

Two modes:
  live  — HTML loads ./omni.json dynamically (requires a local file server or drag-drop)
  baked — OmniFile data is embedded directly in the HTML (fully self-contained, no server)

Usage:
    from nuxtube.viewer import generate_viewer
    path = generate_viewer("/path/to/video/folder")          # live mode
    path = generate_viewer("/path/to/video/folder", bake=True)  # baked mode
"""
import json
from pathlib import Path
from typing import Optional

from .omni import build_omni, write_omni


# ─── HTML Template ──────────────────────────────────────────────────────────

VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NuxTube Viewer</title>
<style>
:root{
  --bg:#0d1117;--surface:#161b22;--surface2:#1c2128;--border:#30363d;
  --text:#c9d1d9;--dim:#8b949e;--accent:#58a6ff;--green:#3fb950;
  --yellow:#d29922;--red:#f85149;--purple:#bc8cff;--orange:#e3b341;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;}
a{color:var(--accent);text-decoration:none;}
a:hover{text-decoration:underline;}

/* Layout */
#app{display:flex;flex-direction:column;height:100vh;overflow:hidden;}
#header{background:var(--surface);border-bottom:1px solid var(--border);padding:10px 20px;display:flex;align-items:center;gap:12px;flex-shrink:0;z-index:100;}
#header h1{font-size:17px;font-weight:700;color:var(--accent);}
#header .spacer{flex:1;}
#main{display:grid;grid-template-columns:270px 1fr;flex:1;overflow:hidden;}

/* Sidebar */
#sidebar{background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow-y:auto;}
#video-card{padding:14px;border-bottom:1px solid var(--border);}
#thumbnail{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:6px;background:var(--surface2);margin-bottom:10px;display:block;}
#thumbnail[src=""]{display:none;}
#video-title{font-size:14px;font-weight:600;line-height:1.4;margin-bottom:8px;}
#video-meta{font-size:12px;color:var(--dim);}
.meta-row{margin-top:5px;display:flex;flex-wrap:wrap;gap:5px;align-items:center;}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent);}
.badge-green{background:rgba(63,185,80,.15);color:var(--green);}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple);}
.badge-orange{background:rgba(227,179,65,.15);color:var(--orange);}

/* Sidebar sections */
.sb-section{padding:12px 14px;border-bottom:1px solid var(--border);}
.sb-section h3{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);margin-bottom:8px;}
.chapter-item{display:flex;gap:8px;padding:4px 6px;border-radius:4px;cursor:pointer;font-size:12px;align-items:flex-start;}
.chapter-item:hover{background:var(--surface2);}
.chapter-time{font-family:'SF Mono','Fira Code',monospace;font-size:11px;color:var(--accent);flex-shrink:0;padding-top:1px;}
.kp-sb-item{padding:5px 6px;border-radius:4px;cursor:pointer;border-left:2px solid var(--border);margin-bottom:5px;font-size:12px;transition:border-color .2s;}
.kp-sb-item:hover{border-color:var(--accent);background:var(--surface2);}
.kp-sb-title{font-weight:600;font-size:12px;}
.kp-sb-ts{font-family:monospace;color:var(--accent);font-size:11px;}

/* Content area */
#content{display:flex;flex-direction:column;overflow:hidden;}
#tabs{background:var(--surface);border-bottom:1px solid var(--border);padding:0 16px;display:flex;flex-shrink:0;}
.tab-btn{padding:10px 14px;background:none;border:none;color:var(--dim);cursor:pointer;font-size:13px;border-bottom:2px solid transparent;margin-bottom:-1px;transition:color .15s;}
.tab-btn:hover{color:var(--text);}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent);}
.tab-panel{display:none;flex:1;overflow-y:auto;padding:20px;}
.tab-panel.active{display:block;}

/* Overview */
#stats-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-bottom:18px;}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px;}
.stat-num{font-size:22px;font-weight:700;}
.stat-label{font-size:11px;color:var(--dim);text-transform:uppercase;margin-top:2px;}
.section-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:16px;}
.section-card h3{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--dim);margin-bottom:12px;}

/* Heatmap */
#heatmap-canvas{width:100%;height:56px;border-radius:6px;display:block;}
.heatmap-labels{display:flex;justify-content:space-between;font-size:11px;color:var(--dim);margin-top:5px;font-family:monospace;}

/* Gallery */
#gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;}
.gallery-item{position:relative;border-radius:6px;overflow:hidden;background:var(--surface2);cursor:pointer;aspect-ratio:16/9;}
.gallery-item img{width:100%;height:100%;object-fit:cover;transition:opacity .2s;}
.gallery-item:hover img{opacity:.8;}
.gallery-ts{position:absolute;bottom:5px;right:6px;background:rgba(0,0,0,.8);color:#fff;font-size:11px;font-family:monospace;padding:2px 6px;border-radius:4px;}

/* Transcript */
#search-bar{position:sticky;top:0;background:var(--bg);padding-bottom:12px;z-index:10;}
#search-input{width:100%;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:14px;outline:none;}
#search-input:focus{border-color:var(--accent);}
#transcript-text{font-size:13px;line-height:1.9;white-space:pre-wrap;word-break:break-word;}
.ts-ts{display:inline-block;font-family:'SF Mono','Fira Code',monospace;font-size:11px;color:var(--accent);background:rgba(88,166,255,.1);padding:1px 5px;border-radius:4px;margin-right:4px;cursor:default;vertical-align:middle;}
mark{background:rgba(210,153,34,.3);color:inherit;border-radius:2px;}

/* Key Points */
#summary-box{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--purple);border-radius:8px;padding:14px;margin-bottom:16px;}
#summary-box h3{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--purple);margin-bottom:8px;}
#summary-text{font-size:13px;line-height:1.7;}
.kp-card{background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:8px;padding:14px;margin-bottom:10px;}
.kp-card-header{display:flex;gap:10px;align-items:flex-start;margin-bottom:8px;}
.kp-num{background:var(--accent);color:#000;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;flex-shrink:0;margin-top:2px;}
.kp-card-title{font-weight:600;font-size:14px;}
.kp-meta{font-size:11px;color:var(--dim);margin-top:3px;}
.kp-lesson{color:var(--text);margin-top:8px;font-size:13px;line-height:1.6;}
.kp-tags{margin-top:8px;display:flex;flex-wrap:wrap;gap:5px;}
.kp-tag{font-size:11px;padding:2px 8px;border-radius:12px;background:var(--surface2);color:var(--dim);border:1px solid var(--border);}
.imp-high{color:var(--red);}
.imp-medium{color:var(--yellow);}
.imp-low{color:var(--green);}

/* Clips */
.clip-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:12px;}
.clip-header{display:flex;align-items:center;gap:10px;margin-bottom:10px;}
.clip-ts{font-family:monospace;color:var(--accent);font-size:13px;}
video{width:100%;border-radius:6px;background:#000;max-height:280px;}

/* Lightbox */
#lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:9999;align-items:center;justify-content:center;cursor:zoom-out;}
#lightbox.open{display:flex;}
#lightbox img{max-width:92vw;max-height:92vh;object-fit:contain;border-radius:8px;}

/* Load screen */
#load-screen{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;gap:16px;}
#drop-zone{border:2px dashed var(--border);border-radius:14px;padding:48px 72px;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;}
#drop-zone:hover,.drag-over{border-color:var(--accent)!important;background:rgba(88,166,255,.04);}
#drop-zone .icon{font-size:52px;margin-bottom:14px;}
#drop-zone h2{font-size:22px;margin-bottom:8px;}
#drop-zone p{color:var(--dim);font-size:14px;margin-bottom:16px;}
#file-input{display:none;}
#load-status{color:var(--dim);font-size:12px;}
.btn{background:var(--accent);color:#000;border:none;border-radius:6px;padding:8px 18px;font-size:13px;font-weight:600;cursor:pointer;transition:opacity .15s;}
.btn:hover{opacity:.88;}
.btn-ghost{background:none;color:var(--text);border:1px solid var(--border);}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent);}

/* Scrollbar */
::-webkit-scrollbar{width:5px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px;}
::-webkit-scrollbar-thumb:hover{background:var(--dim);}
/* Keyboard hint bar */
#key-hint{position:fixed;bottom:0;left:0;right:0;background:var(--surface);border-top:1px solid var(--border);padding:4px 16px;font-size:11px;color:var(--dim);display:none;}
#key-hint kbd{background:var(--surface2);border:1px solid var(--border);border-radius:3px;padding:0 4px;font-size:10px;color:var(--text);}
</style>
</head>
<body>
<div id="app">

<!-- Load screen -->
<div id="load-screen">
  <div id="drop-zone"
    onclick="document.getElementById('file-input').click()"
    ondragover="event.preventDefault();this.classList.add('drag-over')"
    ondragleave="this.classList.remove('drag-over')"
    ondrop="handleDrop(event)">
    <div class="icon">🎬</div>
    <h2>NuxTube Viewer</h2>
    <p>Drop an <code>omni.json</code> file here, or click to browse</p>
    <button class="btn" onclick="event.stopPropagation();document.getElementById('file-input').click()">Browse omni.json</button>
  </div>
  <input type="file" id="file-input" accept=".json" onchange="handleFileInput(event)">
  <div id="load-status">Trying to auto-load omni.json...</div>
</div>

<!-- Main viewer (hidden until loaded) -->
<div id="viewer-ui" style="display:none;height:100vh;flex-direction:column;overflow:hidden;">

  <div id="header">
    <div style="font-size:22px;">🎬</div>
    <h1>NuxTube Viewer</h1>
    <div class="spacer"></div>
    <span id="header-title" style="color:var(--dim);font-size:12px;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></span>
    <button class="btn btn-ghost" onclick="resetViewer()" style="font-size:12px;padding:4px 12px;margin-left:12px;">Load Another</button>
  </div>

  <div id="main">
    <!-- Sidebar -->
    <div id="sidebar">
      <div id="video-card">
        <img id="thumbnail" src="" alt="Thumbnail" onerror="this.style.display='none'">
        <div id="video-title"></div>
        <div id="video-meta"></div>
      </div>
      <div id="chapters-section" class="sb-section" style="display:none;">
        <h3>Chapters</h3>
        <div id="chapters-list"></div>
      </div>
      <div id="kp-sidebar-section" class="sb-section" style="display:none;">
        <h3>Key Moments</h3>
        <div id="kp-sidebar-list"></div>
      </div>
    </div>

    <!-- Content -->
    <div id="content">
      <div id="tabs">
        <button class="tab-btn active" onclick="switchTab('overview')">Overview</button>
        <button class="tab-btn" onclick="switchTab('transcript')">Transcript</button>
        <button class="tab-btn" onclick="switchTab('keypoints')">Key Points</button>
        <button class="tab-btn" onclick="switchTab('clips')">Clips</button>
      </div>

      <!-- Overview -->
      <div class="tab-panel active" id="tab-overview">
        <div id="stats-grid"></div>
        <div id="heatmap-wrap" class="section-card" style="display:none;">
          <h3>Viewer Engagement Heatmap</h3>
          <canvas id="heatmap-canvas"></canvas>
          <div class="heatmap-labels">
            <span>0:00</span><span id="hmap-mid"></span><span id="hmap-end"></span>
          </div>
        </div>
        <div id="gallery-wrap" class="section-card" style="display:none;">
          <h3>Screenshots &mdash; <span id="gallery-count">0</span></h3>
          <div id="gallery"></div>
        </div>
      </div>

      <!-- Transcript -->
      <div class="tab-panel" id="tab-transcript">
        <div id="search-bar">
          <input type="text" id="search-input" placeholder="🔍  Search transcript..."
            oninput="searchTranscript(this.value)">
        </div>
        <div id="transcript-text"><span style="color:var(--dim)">No transcript available.</span></div>
      </div>

      <!-- Key Points -->
      <div class="tab-panel" id="tab-keypoints">
        <div id="summary-box" style="display:none;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
            <h3 style="margin:0;">Summary</h3>
            <button class="btn btn-ghost" style="font-size:11px;padding:3px 10px;" onclick="copyText(document.getElementById('summary-text').textContent)">Copy</button>
          </div>
          <div id="summary-text"></div>
        </div>
        <div id="keypoints-list"><span style="color:var(--dim)">No key points extracted.</span></div>
      </div>

      <!-- Clips -->
      <div class="tab-panel" id="tab-clips">
        <div id="clips-list"><span style="color:var(--dim)">No clips available.</span></div>
      </div>
    </div>
  </div>
</div>

<!-- Lightbox -->
<div id="lightbox" onclick="closeLightbox()">
  <img id="lightbox-img" src="" alt="">
</div>

<script>
// ─── Baked data (replaced at generation time) ───
const __BAKED__ = null; // NUXTUBE_BAKED_DATA

let DATA = null;

// ─── Init ───
window.addEventListener('DOMContentLoaded', async () => {
  if (__BAKED__) {
    loadData(__BAKED__);
    return;
  }
  document.getElementById('load-status').textContent = 'Trying to auto-load omni.json…';
  try {
    const r = await fetch('./omni.json');
    if (r.ok) {
      loadData(await r.json());
    } else {
      document.getElementById('load-status').textContent = 'Drop an omni.json file to view, or click Browse.';
    }
  } catch(e) {
    document.getElementById('load-status').textContent = 'Drop an omni.json file to view, or click Browse.';
  }
});

// ─── File loading ───
function handleDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) readFile(file);
}
function handleFileInput(e) {
  const file = e.target.files[0];
  if (file) readFile(file);
}
function readFile(file) {
  const reader = new FileReader();
  reader.onload = ev => {
    try { loadData(JSON.parse(ev.target.result)); }
    catch(err) { alert('Invalid JSON: ' + err.message); }
  };
  reader.readAsText(file);
}
function resetViewer() {
  DATA = null;
  document.getElementById('load-screen').style.display = 'flex';
  document.getElementById('viewer-ui').style.display = 'none';
  document.getElementById('file-input').value = '';
}

// ─── Load & render ───
function loadData(data) {
  DATA = data;
  document.getElementById('load-screen').style.display = 'none';
  const v = document.getElementById('viewer-ui');
  v.style.display = 'flex';
  renderAll(data);
}
function renderAll(d) {
  renderHeader(d);
  renderChapters(d);
  renderSidebarKP(d);
  renderStats(d);
  renderHeatmap(d);
  renderGallery(d);
  renderTranscript(d);
  renderKeyPoints(d);
  renderClips(d);
}

// ─── Helpers ───
function fmt(ts) {
  if (ts == null || ts === '') return '?';
  const t = Math.round(Number(ts));
  if (isNaN(t)) return String(ts);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─── Render functions ───
function renderHeader(d) {
  const m = d.metadata || {};
  const thumb = m.thumbnail_url || '';
  const img = document.getElementById('thumbnail');
  if (thumb) { img.src = thumb; img.style.display = 'block'; }
  else img.style.display = 'none';

  document.title = (m.title || 'NuxTube Viewer') + ' — NuxTube';
  document.getElementById('header-title').textContent = m.title || '';
  document.getElementById('video-title').textContent = m.title || 'Unknown Title';

  const ch = m.channel || 'Unknown';
  const chUrl = m.channel_url || '#';
  const ytUrl = m.url || (m.video_id ? `https://youtube.com/watch?v=${m.video_id}` : '#');
  const dur = m.duration || '';
  const cat = m.category || '';
  const fetched = m.fetched_at ? new Date(m.fetched_at).toLocaleDateString() : '';

  document.getElementById('video-meta').innerHTML = `
    <div><a href="${esc(chUrl)}" target="_blank" rel="noopener">${esc(ch)}</a></div>
    <div class="meta-row">
      ${dur ? `<span class="badge badge-blue">${esc(dur)}</span>` : ''}
      ${cat ? `<span class="badge badge-purple">${esc(cat)}</span>` : ''}
      ${m.video_id ? `<a href="${esc(ytUrl)}" target="_blank" rel="noopener" class="badge badge-green">YouTube ↗</a>` : ''}
    </div>
    ${fetched ? `<div style="margin-top:6px;font-size:11px;">Archived ${fetched}</div>` : ''}
  `;
}

function renderChapters(d) {
  const chapters = (d.player_data && d.player_data.chapters) || (d.metadata && d.metadata.player_data && d.metadata.player_data.chapters) || [];
  if (!chapters.length) return;
  document.getElementById('chapters-section').style.display = 'block';
  document.getElementById('chapters-list').innerHTML = chapters.map(ch => `
    <div class="chapter-item">
      <span class="chapter-time">${fmt(ch.start_time != null ? ch.start_time : ch.timestamp)}</span>
      <span>${esc(ch.title || 'Chapter')}</span>
    </div>
  `).join('');
}

function renderSidebarKP(d) {
  const kps = (d.key_points && d.key_points.key_points) || [];
  if (!kps.length) return;
  document.getElementById('kp-sidebar-section').style.display = 'block';
  document.getElementById('kp-sidebar-list').innerHTML = kps.slice(0,10).map((kp,i) => `
    <div class="kp-sb-item" onclick="switchTab('keypoints')">
      <div class="kp-sb-ts">${fmt(kp.timestamp)}</div>
      <div class="kp-sb-title">${esc(kp.title || `Point ${i+1}`)}</div>
    </div>
  `).join('');
}

function renderStats(d) {
  const m = d.metadata || {};
  const ss = (d.screenshots||[]).filter(s=>s.ok!==false).length;
  const cl = (d.clips||[]).filter(c=>c.ok!==false).length;
  const chapters = ((d.player_data&&d.player_data.chapters)||(m.player_data&&m.player_data.chapters)||[]).length;
  const kps = (d.key_points&&d.key_points.key_points||[]).length;
  const segs = m.segment_count||0;

  document.getElementById('stats-grid').innerHTML = `
    <div class="stat-card"><div class="stat-num" style="color:var(--green)">${ss}</div><div class="stat-label">Screenshots</div></div>
    <div class="stat-card"><div class="stat-num" style="color:var(--accent)">${cl}</div><div class="stat-label">Clips</div></div>
    <div class="stat-card"><div class="stat-num" style="color:var(--purple)">${kps}</div><div class="stat-label">Key Points</div></div>
    <div class="stat-card"><div class="stat-num" style="color:var(--orange)">${chapters}</div><div class="stat-label">Chapters</div></div>
    <div class="stat-card"><div class="stat-num" style="color:var(--dim)">${segs}</div><div class="stat-label">Tr. Segments</div></div>
  `;
}

function _drawHeatmap(canvas, hm) {
  const W = Math.max(canvas.parentElement.clientWidth - 28, 200);
  canvas.width = W; canvas.height = 60;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, 60);
  const vals = hm.map(p => p.heat_value != null ? p.heat_value : (p.value||0));
  const maxV = Math.max(...vals, 0.001);
  const barW = W / vals.length;
  // Draw bars with gradient fill
  vals.forEach((v,i) => {
    const n = v / maxV;
    const bh = Math.max(2, n * 60);
    const grad = ctx.createLinearGradient(0, 60-bh, 0, 60);
    grad.addColorStop(0, `hsla(${200+n*60},80%,${40+n*25}%,0.9)`);
    grad.addColorStop(1, `hsla(${200+n*60},80%,${30+n*20}%,0.6)`);
    ctx.fillStyle = grad;
    ctx.fillRect(i*barW, 60-bh, barW+0.5, bh);
  });
  // Overlay subtle grid
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  [0.25, 0.5, 0.75].forEach(x => {
    ctx.beginPath(); ctx.moveTo(x*W, 0); ctx.lineTo(x*W, 60); ctx.stroke();
  });
}

function renderHeatmap(d) {
  const hm = (d.player_data&&d.player_data.heatmap)||(d.metadata&&d.metadata.player_data&&d.metadata.player_data.heatmap)||[];
  if (!hm.length) return;
  document.getElementById('heatmap-wrap').style.display = 'block';
  const canvas = document.getElementById('heatmap-canvas');
  // Draw after layout is settled
  requestAnimationFrame(() => {
    _drawHeatmap(canvas, hm);
    const dur = d.metadata&&d.metadata.duration;
    if (dur) document.getElementById('hmap-end').textContent = dur;
    const mid = hm[Math.floor(hm.length/2)];
    if (mid&&mid.start_millis!=null) document.getElementById('hmap-mid').textContent = fmt(mid.start_millis/1000);
  });
  // Re-render on window resize
  if (!canvas._resizeHandlerSet) {
    canvas._resizeHandlerSet = true;
    window.addEventListener('resize', () => { if(DATA) requestAnimationFrame(()=>_drawHeatmap(canvas, hm)); });
  }
}

function renderGallery(d) {
  const ss = (d.screenshots||[]).filter(s=>s.ok!==false);
  if (!ss.length) return;
  document.getElementById('gallery-wrap').style.display = 'block';
  document.getElementById('gallery-count').textContent = ss.length;
  document.getElementById('gallery').innerHTML = ss.map((s,i) => {
    const p = s.screenshot||s.path||'';
    const ts = s.timestamp!=null ? fmt(s.timestamp) : '';
    return `<div class="gallery-item" onclick="openLightbox('${esc(p)}')">
      <img src="${esc(p)}" alt="Screenshot ${i+1}" loading="lazy" onerror="this.closest('.gallery-item').style.display='none'">
      ${ts?`<span class="gallery-ts">${ts}</span>`:''}
    </div>`;
  }).join('');
}

function renderTranscript(d) {
  const text = (d.transcript&&(d.transcript.timestamped_text||d.transcript.full_text))||'';
  if (!text) return;
  _setTranscript(text);
}

function _setTranscript(text) {
  const html = text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\[(\d+:\d+(?::\d+)?)\]/g,'<span class="ts-ts">$1</span>');
  document.getElementById('transcript-text').innerHTML = html;
}

function searchTranscript(q) {
  if (!DATA) return;
  const text = (DATA.transcript&&(DATA.transcript.timestamped_text||DATA.transcript.full_text))||'';
  if (!text) return;
  if (!q.trim()) { _setTranscript(text); return; }
  const safe = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\[(\d+:\d+(?::\d+)?)\]/g,'<span class="ts-ts">$1</span>');
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'), 'gi');
  document.getElementById('transcript-text').innerHTML = safe.replace(re, m=>`<mark>${m}</mark>`);
}

function renderKeyPoints(d) {
  const kp = d.key_points||{};
  if (kp.summary) {
    document.getElementById('summary-box').style.display = 'block';
    document.getElementById('summary-text').textContent = kp.summary;
  }
  const pts = kp.key_points||[];
  if (!pts.length) { document.getElementById('keypoints-list').innerHTML='<span style="color:var(--dim)">No key points extracted.</span>'; return; }
  document.getElementById('keypoints-list').innerHTML = pts.map((p,i) => {
    const ic = {high:'imp-high',medium:'imp-medium',low:'imp-low'}[p.importance]||'imp-medium';
    return `<div class="kp-card">
      <div class="kp-card-header">
        <div class="kp-num">${i+1}</div>
        <div>
          <div class="kp-card-title">${esc(p.title||`Point ${i+1}`)}</div>
          <div class="kp-meta">
            ${p.timestamp!=null?`<span>${fmt(p.timestamp)}</span> · `:''}
            <span class="${ic}">${esc(p.importance||'medium')}</span>
            ${p.category?` · ${esc(p.category)}`:''}
          </div>
        </div>
      </div>
      ${p.lesson?`<div class="kp-lesson">${esc(p.lesson)}</div>`:''}
      ${(p.tags&&p.tags.length)?`<div class="kp-tags">${p.tags.map(t=>`<span class="kp-tag">${esc(t)}</span>`).join('')}</div>`:''}
    </div>`;
  }).join('');
}

function renderClips(d) {
  const clips = (d.clips||[]).filter(c=>c.ok!==false);
  if (!clips.length) { document.getElementById('clips-list').innerHTML='<span style="color:var(--dim)">No clips available.</span>'; return; }
  document.getElementById('clips-list').innerHTML = clips.map((c,i)=>{
    const p = c.clip||c.path||'';
    const ts = c.timestamp!=null?fmt(c.timestamp):`Clip ${i+1}`;
    return `<div class="clip-card">
      <div class="clip-header">
        <span class="clip-ts">${ts}</span>
        ${c.score!=null?`<span style="font-size:11px;color:var(--dim)">score: ${c.score}</span>`:''}
      </div>
      <video controls preload="none" src="${esc(p)}"></video>
    </div>`;
  }).join('');
}

// ─── Tab switching ───
function switchTab(name) {
  const names = ['overview','transcript','keypoints','clips'];
  document.querySelectorAll('.tab-btn').forEach((b,i)=>b.classList.toggle('active',names[i]===name));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active',p.id===`tab-${name}`));
  if (name==='overview'&&DATA) requestAnimationFrame(()=>renderHeatmap(DATA));
}

// ─── Copy to clipboard ───
function copyText(text) {
  navigator.clipboard.writeText(text).then(() => {
    // Brief visual feedback via a toast-style message
    const el = document.createElement('div');
    el.textContent = '✓ Copied';
    Object.assign(el.style, {
      position:'fixed',bottom:'20px',left:'50%',transform:'translateX(-50%)',
      background:'var(--green)',color:'#000',padding:'6px 16px',borderRadius:'20px',
      fontSize:'13px',fontWeight:'600',zIndex:'9999',transition:'opacity .3s'
    });
    document.body.appendChild(el);
    setTimeout(()=>{ el.style.opacity='0'; setTimeout(()=>el.remove(),300); },1500);
  }).catch(()=>{});
}

// ─── Lightbox ───
function openLightbox(src) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  document.getElementById('lightbox-img').src = '';
}
document.addEventListener('keydown', e=>{
  if(e.key==='Escape') closeLightbox();
  if(e.key==='1') switchTab('overview');
  if(e.key==='2') switchTab('transcript');
  if(e.key==='3') switchTab('keypoints');
  if(e.key==='4') switchTab('clips');
});
</script>
</body>
</html>"""


# ─── Generator ─────────────────────────────────────────────────────────────


def generate_viewer(folder: str, bake: bool = False) -> Optional[str]:
    """Generate viewer.html in the archive folder.

    Args:
        folder: Path to the video archive folder.
        bake:   If True, embed omni.json data directly (no server needed).

    Returns:
        Path to the generated viewer.html, or None on error.
    """
    folder_path = Path(folder)
    if not folder_path.exists():
        return None

    # Ensure omni.json exists
    omni_path = folder_path / "omni.json"
    if not omni_path.exists():
        write_omni(folder)

    if bake:
        omni_data = build_omni(folder)
        if not omni_data:
            return None
        baked_json = json.dumps(omni_data, ensure_ascii=False, default=str)
        html = VIEWER_HTML.replace(
            "const __BAKED__ = null; // NUXTUBE_BAKED_DATA",
            f"const __BAKED__ = {baked_json}; // NUXTUBE_BAKED_DATA",
        )
    else:
        html = VIEWER_HTML

    viewer_path = folder_path / "viewer.html"
    viewer_path.write_text(html, encoding="utf-8")
    return str(viewer_path)
