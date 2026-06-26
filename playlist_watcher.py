#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  👁️  NuxTube Playlist Watcher                                     ║
║  ═══════════════════════════════════                              ║
║  Polls a YouTube playlist every N minutes for new videos.        ║
║  When new videos are found:                                      ║
║    1. Fetches transcript + metadata via yt-dlp + NuxTube          ║
║    2. Runs the Knowledge Grafter to create Obsidian pages         ║
║    3. Updates the INDEX.md and state file                         ║
║                                                                  ║
║  Runs as a background daemon. Outputs a live dashboard.           ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
    python3 playlist_watcher.py              # foreground (dashboard)
    python3 playlist_watcher.py --daemon     # background daemon

Config:
    Edit PLAYLIST_URL, POLL_INTERVAL, and MODEL below.
"""

import asyncio
import aiohttp
import json
import os
import re
import sys
import time
import signal
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLnz01APIg0zVluzyomfB9b1tbzRzGvzJV"
POLL_INTERVAL = 300  # 5 minutes in seconds

VAULT_ROOT = Path("/Volumes/PSILVER-2TB/NuxTubeInsights")
ARCHIVE_ROOT = Path("/Volumes/PSILVER-2TB/YoutubeInsights")
TEST_DATA_ROOT = Path("/Users/user/Projects2026/NeuroD-NuxTube/youtube_videos/_test_data")
STATE_PATH = VAULT_ROOT / "_staging" / "watcher-state.json"
WATCHER_LOG = VAULT_ROOT / "_staging" / "watcher-log.md"
INDEX_PATH = VAULT_ROOT / "INDEX.md"
LESSONS_PATH = VAULT_ROOT / "_staging" / "lessons-learned.md"

MODEL = "google/gemini-2.5-flash"
CONCURRENCY = 5
MAX_TRANSCRIPT_LINES = 2000

NUXTUBE_PROJECT = Path("/Users/user/Projects2026/NeuroD-NuxTube")

# Read API key from Hermes .env
def get_api_key():
    env_path = os.path.expanduser("~/.hermes/.env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENROUTER_API_KEY not found in ~/.hermes/.env")

API_KEY = get_api_key()

# ═══════════════════════════════════════════════════════════════════
# TAG TAXONOMY
# ═══════════════════════════════════════════════════════════════════

VALID_TAGS = {
    "ai-agents", "agentic-os", "multi-agent", "orchestration", "sub-agents",
    "memory", "soul-md", "context-engineering", "cron-jobs", "mcp",
    "hermes-agent", "claude-code", "notebooklm", "cursor", "coding",
    "cli", "terminal", "python", "javascript", "api", "build-tools",
    "debugging", "testing", "deployment", "productivity", "second-brain",
    "obsidian", "pkm", "note-taking", "automation", "gamification",
    "habit-tracking", "business", "monetisation", "freelance", "saas",
    "startup", "design", "ui", "ux", "figma", "prototyping",
    "design-systems", "3d-printing", "web-design", "app-design", "llm",
    "fine-tuning", "inference", "embedding", "image-segmentation", "sam",
    "workflow", "tutorial", "beginner", "advanced", "tips-and-tricks",
    "open-source", "comparison", "tool-review", "seo", "marketing",
    "finance", "trading", "ai-art", "video-generation", "electronics",
    "esp32", "diy", "hardware", "prompt-engineering", "rag",
    "vector-database", "agents", "security", "prompt-injection"
}

# ═══════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════

lessons_learned = []
watcher_stats = {
    "polls": 0,
    "new_videos_found": 0,
    "videos_processed": 0,
    "videos_failed": 0,
    "skills_created": 0,
    "workflows_created": 0,
    "start_time": None,
    "last_poll": None,
    "last_new_video": None,
    "api_errors": 0,
    "json_errors": 0,
}

# ═══════════════════════════════════════════════════════════════════
# TERMINAL DASHBOARD
# ═══════════════════════════════════════════════════════════════════

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    DIM = "\033[2m"

def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")

def print_dashboard(status: str = "", current_video: str = "", next_poll_in: int = 0):
    clear_screen()
    elapsed = time.time() - watcher_stats["start_time"] if watcher_stats["start_time"] else 0
    mins, secs = divmod(int(elapsed), 60)
    hours, mins = divmod(mins, 60)
    
    total_pages = 0
    for subdir in ["insights", "media", "skills", "workflows"]:
        d = VAULT_ROOT / subdir
        if d.exists():
            total_pages += len(list(d.rglob("*.md")))
    
    last_poll_str = "never"
    if watcher_stats["last_poll"]:
        ago = int(time.time() - watcher_stats["last_poll"])
        if ago < 60:
            last_poll_str = f"{ago}s ago"
        else:
            last_poll_str = f"{ago // 60}m {ago % 60}s ago"
    
    last_new_str = "never"
    if watcher_stats["last_new_video"]:
        ago = int(time.time() - watcher_stats["last_new_video"])
        if ago < 60:
            last_new_str = f"{ago}s ago"
        elif ago < 3600:
            last_new_str = f"{ago // 60}m ago"
        else:
            last_new_str = f"{ago // 3600}h {(ago % 3600) // 60}m ago"
    
    print(f"{Colors.CYAN}{Colors.BOLD}")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  👁️  NUXTUBE PLAYLIST WATCHER                                    ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}")
    
    print(f"  {Colors.BLUE}📡 WATCHER STATUS{Colors.RESET}")
    print(f"  Status: {status or 'idle'}")
    print(f"  Uptime: {hours}h {mins}m {secs}s")
    print(f"  Polls: {watcher_stats['polls']}")
    print(f"  Last poll: {last_poll_str}")
    if next_poll_in > 0:
        print(f"  Next poll in: {next_poll_in // 60}m {next_poll_in % 60}s")
    print(f"  Poll interval: {POLL_INTERVAL // 60} minutes")
    print()
    
    print(f"  {Colors.GREEN}📊 LIFETIME STATS{Colors.RESET}")
    print(f"  🆕 New videos found: {watcher_stats['new_videos_found']}")
    print(f"  ✅ Videos processed: {watcher_stats['videos_processed']}")
    print(f"  ❌ Videos failed: {watcher_stats['videos_failed']}")
    print(f"  🔧 Skills created: {watcher_stats['skills_created']}")
    print(f"  🔄 Workflows created: {watcher_stats['workflows_created']}")
    print(f"  📁 Total vault pages: {total_pages}")
    print(f"  Last new video: {last_new_str}")
    print()
    
    print(f"  {Colors.MAGENTA}🧠 SELF-IMPROVEMENT{Colors.RESET}")
    print(f"  📚 Lessons learned: {len(lessons_learned)}")
    print(f"  🩺 API errors: {watcher_stats['api_errors']}  |  📋 JSON errors: {watcher_stats['json_errors']}")
    if lessons_learned:
        for i, lesson in enumerate(lessons_learned[-3:], 1):
            display = lesson[:70] + "..." if len(lesson) > 70 else lesson
            print(f"     {Colors.DIM}{i}. {display}{Colors.RESET}")
    print()
    
    if current_video:
        print(f"  {Colors.YELLOW}▶️  NOW PROCESSING{Colors.RESET}")
        print(f"  {current_video}")
    
    print()
    print(f"  {Colors.DIM}Playlist: {PLAYLIST_URL}{Colors.RESET}")
    print(f"  {Colors.DIM}Vault: {VAULT_ROOT}{Colors.RESET}")
    print(f"  {Colors.DIM}Model: {MODEL}{Colors.RESET}")
    print(f"  {Colors.DIM}State: {STATE_PATH}{Colors.RESET}")
    print()

def log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    emoji = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "error": "❌", "skill": "🔧", 
             "workflow": "🔄", "new": "🆕", "poll": "📡"}.get(level, "ℹ️")
    entry = f"[{ts}] {emoji} {msg}"
    
    try:
        WATCHER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WATCHER_LOG, "a") as f:
            f.write(entry + "\n")
    except:
        pass
    
    print(f"  {entry}")

# ═══════════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """Load known video IDs from state file."""
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except:
            pass
    return {"known_ids": [], "first_run": datetime.now(timezone.utc).isoformat()}

def save_state(state: dict):
    """Save state file."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

def load_lessons():
    """Load lessons from previous runs."""
    if LESSONS_PATH.exists():
        try:
            with open(LESSONS_PATH) as f:
                content = f.read()
            # Extract lessons from numbered list
            for line in content.split("\n"):
                m = re.match(r'^\d+\.\s+(.+)', line)
                if m:
                    lessons_learned.append(m.group(1))
        except:
            pass

# ═══════════════════════════════════════════════════════════════════
# PLAYLIST POLLING
# ═══════════════════════════════════════════════════════════════════

def fetch_playlist_videos() -> list[dict]:
    """Fetch all video IDs+titles from playlist via yt-dlp."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-j", PLAYLIST_URL],
            capture_output=True, text=True, timeout=120,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                vid = entry.get("id", "")
                title = entry.get("title", vid)
                if vid:
                    videos.append({"id": vid, "title": title, "url": f"https://youtube.com/watch?v={vid}"})
            except json.JSONDecodeError:
                continue
        return videos
    except subprocess.TimeoutExpired:
        log("Playlist fetch timed out", "error")
        return []
    except Exception as e:
        log(f"Playlist fetch error: {e}", "error")
        return []

def find_new_videos(playlist: list[dict], known_ids: set) -> list[dict]:
    """Return videos from playlist that aren't in known_ids."""
    return [v for v in playlist if v["id"] not in known_ids]

# ═══════════════════════════════════════════════════════════════════
# VIDEO FETCHING (transcript + metadata)
# ═══════════════════════════════════════════════════════════════════

def fetch_video_data(video_id: str, title: str, url: str) -> Optional[dict]:
    """Fetch transcript + metadata for a single video using yt-dlp + youtube-transcript-api.
    
    Saves to YoutubeInsights archive with a best-guess category.
    """
    import urllib.request
    import urllib.error
    
    # Try to fetch transcript via youtube-transcript-api
    try:
        sys.path.insert(0, str(NUXTUBE_PROJECT))
        from nuxtube.transcript import fetch_transcript, extract_video_id
        from nuxtube.metadata import fetch_metadata
    except ImportError:
        # Direct fallback: use youtube-transcript-api directly
        pass
    
    # Fetch transcript
    transcript_text = None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.fetch(video_id)
        lines = []
        for snippet in transcript_list.snippets:
            mins = int(snippet.start // 60)
            secs = int(snippet.start % 60)
            lines.append(f"{mins}:{secs:02d} {snippet.text}")
        transcript_text = f"# {title}\n\n> **URL:** {url}\n> **Video ID:** {video_id}\n\n---\n\n## Timestamped Transcript\n\n" + "\n".join(lines)
        segment_count = len(transcript_list.snippets)
    except Exception as e:
        log(f"Transcript fetch failed for {video_id}: {e}", "error")
        return None
    
    # Fetch metadata via yt-dlp
    metadata = None
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=60,
        )
        if result.stdout.strip():
            raw = json.loads(result.stdout.strip().split("\n")[0])
            duration_secs = raw.get("duration", 0)
            mins = duration_secs // 60
            secs = duration_secs % 60
            metadata = {
                "title": raw.get("title", title),
                "video_id": video_id,
                "url": url,
                "channel": raw.get("uploader", raw.get("channel", "Unknown")),
                "channel_url": raw.get("uploader_url", raw.get("channel_url", "")),
                "category": guess_category(raw.get("title", title), raw.get("description", "")),
                "duration": f"{mins}:{secs:02d}",
                "segment_count": segment_count if 'segment_count' in dir() else 0,
                "thumbnail_url": raw.get("thumbnail", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "playlist-watcher",
                "files": {"transcript": "transcript.md", "metadata": "metadata.json"},
            }
    except Exception as e:
        log(f"Metadata fetch failed for {video_id}: {e}", "warn")
        # Fallback metadata
        metadata = {
            "title": title,
            "video_id": video_id,
            "url": url,
            "channel": "Unknown",
            "channel_url": "",
            "category": guess_category(title, ""),
            "duration": "Unknown",
            "segment_count": 0,
            "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "playlist-watcher",
            "files": {"transcript": "transcript.md", "metadata": "metadata.json"},
        }
    
    # Determine output directory
    category = metadata["category"]
    slug = slugify(title)
    video_dir = ARCHIVE_ROOT / category / slug
    video_dir.mkdir(parents=True, exist_ok=True)
    
    # Write files
    with open(video_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    with open(video_dir / "transcript.md", "w") as f:
        f.write(transcript_text)
    
    log(f"Fetched transcript + metadata for [{category}] {slug}", "ok")
    
    return {
        "category": category,
        "slug": slug,
        "video_dir": str(video_dir),
        "metadata_path": str(video_dir / "metadata.json"),
        "transcript_path": str(video_dir / "transcript.md"),
        "keypoints_path": None,
        "screenshots_manifest": None,
        "clips_manifest": None,
        "insight_path": str(VAULT_ROOT / "insights" / category / f"{slug}.md"),
    }

def slugify(text: str) -> str:
    """Create a URL-safe slug from a title."""
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_-]+', '-', slug)
    slug = slug.strip('-')
    return slug[:80] if slug else "untitled"

def guess_category(title: str, description: str) -> str:
    """Guess category from title/description keywords."""
    text = (title + " " + description).lower()
    
    category_keywords = {
        "ai-agents": ["ai agent", "agentic", "claude", "llm", "gpt", "ai operat", 
                       "agent loop", "multi-agent", "orchestrat", "anthropic", "openai",
                       "gemini", "copilot", "ai team", "ai assistant", "prompt engineer"],
        "coding": ["code", "programming", "developer", "javascript", "python", 
                   "react", "nextjs", "api", "github", "git", "terminal", "cli",
                   "debug", "typescript", "framework", "library"],
        "productivity": ["productivity", "second brain", "obsidian", "pkm", "note-tak",
                         "habit", "task", "todo", "workflow", "automat", "efficiency",
                         "gamif", "streak"],
        "business": ["money", "business", "saas", "startup", "freelance", "monetiz",
                     "entrepreneur", "revenue", "profit", "marketing", "seo",
                     "digital product", "sell", "income"],
        "design": ["design", "ui", "ux", "figma", "prototype", "css", "tailwind",
                   "web design", "app design", "design system", "spacing", "layout",
                   "typography", "color"],
        "marketing": ["marketing", "seo", "content strategy", "social media",
                      "brand", "audience", "engagement", "conversion"],
    }
    
    scores = {}
    for cat, keywords in category_keywords.items():
        scores[cat] = sum(1 for kw in keywords if kw in text)
    
    best_cat = "uncategorized"
    best_score = 0
    for cat, keywords in category_keywords.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_cat = cat
    if best_score == 0:
        return "uncategorized"
    return best_cat

# ═══════════════════════════════════════════════════════════════════
# LLM API (same as knowledge_grafter.py)
# ═══════════════════════════════════════════════════════════════════

def build_system_prompt() -> str:
    prompt = """You are a knowledge extraction engine for an Obsidian knowledge graph. You read YouTube video transcripts and extract insights, skills, workflows, and concepts.

You MUST respond with ONLY a valid JSON object (no markdown fences, no preamble). The JSON schema is:

{
  "summary": "3-5 sentences distilling the actionable value of this video",
  "key_insights": [
    {
      "timestamp": "M:SS or H:MM:SS",
      "insight": "1-2 sentences. Be specific and actionable. Include tool names, technique names, exact steps.",
      "wikilinks": ["concept-slug-in-kebab-case"]
    }
  ],
  "has_skills": true/false,
  "skills": [
    {
      "slug": "skill-slug-in-kebab-case",
      "title": "Human Readable Title",
      "what": "1-2 sentences describing what this skill is",
      "when": "1-2 sentences on when to use it",
      "steps": "Numbered steps as a string, e.g. '1. Do X\\n2. Do Y\\n3. Do Z'",
      "tips": "Tips and pitfalls as a string"
    }
  ],
  "has_workflow": true/false,
  "workflows": [
    {
      "slug": "workflow-slug-in-kebab-case",
      "title": "Human Readable Title",
      "goal": "1 sentence describing the end goal",
      "prerequisites": "What you need before starting",
      "steps": "Numbered steps as a string",
      "outcome": "1 sentence describing the expected outcome"
    }
  ],
  "tags": ["tag1", "tag2", "etc"],
  "related_concepts": ["concept-slug-1", "concept-slug-2"]
}

RULES:
- Extract 5-15 key insights depending on video density
- Only set has_skills=true if the video teaches a reusable technique
- Only set has_workflow=true if the video describes a repeatable multi-step process
- Use kebab-case for all slugs
- Timestamps should match the transcript format (M:SS)
- Use [[wikilinks]] style slugs for concepts (e.g. "agent-loops", "soul-md", "context-engineering")
- Tags must be from this taxonomy: """ + ", ".join(sorted(VALID_TAGS)) + """
- Be genuinely useful — extract actionable lessons, not vague platitudes
- Include specific tool names, technique names, and exact steps"""
    
    if lessons_learned:
        prompt += "\n\n## LESSONS LEARNED (from self-improvement)\n"
        prompt += "Apply these lessons to your extraction:\n"
        for i, lesson in enumerate(lessons_learned, 1):
            prompt += f"{i}. {lesson}\n"
    
    return prompt

def build_user_prompt(video: dict, metadata: dict, transcript: str) -> str:
    parts = [f"## Video Metadata"]
    parts.append(json.dumps({
        "title": metadata.get("title", "Unknown"),
        "channel": metadata.get("channel", "Unknown"),
        "duration": metadata.get("duration", "Unknown"),
        "category": video["category"],
        "segment_count": metadata.get("segment_count", 0),
    }, indent=2))
    
    parts.append(f"\n## Transcript (first {MAX_TRANSCRIPT_LINES} lines)")
    lines = transcript.split("\n")
    parts.append("\n".join(lines[:MAX_TRANSCRIPT_LINES]))
    
    return "\n".join(parts)

async def call_llm(session: aiohttp.ClientSession, video: dict, metadata: dict, transcript: str) -> Optional[dict]:
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(video, metadata, transcript)
    
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 8000,
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/p4ulypops/YTInsightFinder",
        "X-Title": "NuxTube Playlist Watcher",
    }
    
    for attempt in range(3):
        try:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    log(f"API error {resp.status}: {error_text[:200]}", "error")
                    watcher_stats["api_errors"] += 1
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return None
                
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                
                content = content.strip()
                if content.startswith("```"):
                    content = re.sub(r'^```(?:json)?\n?', '', content)
                    content = re.sub(r'\n?```$', '', content)
                
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    log(f"JSON parse error: {e}", "error")
                    watcher_stats["json_errors"] += 1
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            return json.loads(json_match.group())
                        except:
                            pass
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return None
        except asyncio.TimeoutError:
            log(f"LLM timeout (attempt {attempt+1})", "error")
            watcher_stats["api_errors"] += 1
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            return None
        except Exception as e:
            log(f"LLM exception: {e}", "error")
            watcher_stats["api_errors"] += 1
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            return None
    return None

# ═══════════════════════════════════════════════════════════════════
# SELF-IMPROVEMENT
# ═══════════════════════════════════════════════════════════════════

async def self_improve(session: aiohttp.ClientSession, recent_logs: list[str]):
    """Review recent processing logs and extract new lessons."""
    if not recent_logs:
        return
    
    recent = "\n".join(recent_logs[-20:])
    
    prompt = f"""Review these processing logs from a YouTube transcript-to-Obsidian knowledge graph generator. 
Identify patterns, errors, or optimization opportunities. Return ONLY a JSON array of new lessons (strings).

If there are no new lessons to learn, return an empty array: []

Logs:
{recent}

Current lessons already known:
{json.dumps(lessons_learned, indent=2) if lessons_learned else "[]"}

Return ONLY a JSON array of new lesson strings (max 3). Each lesson should be actionable and specific."""

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 2000,
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    
    try:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = re.sub(r'^```(?:json)?\n?', '', content)
                    content = re.sub(r'\n?```$', '', content)
                try:
                    new_lessons = json.loads(content)
                    if isinstance(new_lessons, list):
                        for lesson in new_lessons:
                            if isinstance(lesson, str) and lesson not in lessons_learned:
                                lessons_learned.append(lesson)
                                log(f"New lesson: {lesson}", "ok")
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        log(f"Self-improvement error: {e}", "warn")

def save_lessons():
    LESSONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LESSONS_PATH, "w") as f:
        f.write("# Lessons Learned (Self-Improvement)\n\n")
        f.write(f"Updated: {datetime.now().isoformat()}\n\n")
        for i, lesson in enumerate(lessons_learned, 1):
            f.write(f"{i}. {lesson}\n")

# ═══════════════════════════════════════════════════════════════════
# FILE WRITERS (same as knowledge_grafter.py)
# ═══════════════════════════════════════════════════════════════════

def filter_tags(tags: list) -> list:
    tag_map = {
        "ai": "ai-agents", "agents": "ai-agents", "ai agent": "ai-agents",
        "obsidian vault": "obsidian", "knowledge management": "pkm",
        "personal knowledge management": "pkm", "second brain": "second-brain",
        "3d printing": "3d-printing", "ui design": "ui", "ux design": "ux",
        "prompt engineering": "prompt-engineering", "prompt-injection": "security",
    }
    result = []
    for tag in tags:
        tag = tag.lower().strip()
        tag = tag_map.get(tag, tag)
        if tag in VALID_TAGS and tag not in result:
            result.append(tag)
    if len(result) < 2:
        result.extend(["tutorial", "tips-and-tricks"])
    return result[:6]

def write_insight_page(video: dict, metadata: dict, extraction: dict):
    insight_path = Path(video["insight_path"])
    insight_path.parent.mkdir(parents=True, exist_ok=True)
    
    tags = filter_tags(extraction.get("tags", []))
    has_skills = extraction.get("has_skills", False)
    has_workflow = extraction.get("has_workflow", False)
    
    insights_md = []
    for ki in extraction.get("key_insights", []):
        ts = ki.get("timestamp", "")
        text = ki.get("insight", "")
        wikilinks = ki.get("wikilinks", [])
        for wl in wikilinks:
            if f"[[{wl}]]" not in text:
                text = text.replace(wl.replace("-", " "), f"[[{wl}]]", 1) if wl.replace("-", " ") in text.lower() else text
        insights_md.append(f"- ⏱ {ts} — {text}")
    
    skills_md = []
    if has_skills and extraction.get("skills"):
        for skill in extraction["skills"]:
            slug = skill.get("slug", "")
            title = skill.get("title", slug)
            skills_md.append(f"- **{title}** — {skill.get('what', '')} See [[skills/{slug}]]")
    
    workflows_md = []
    if has_workflow and extraction.get("workflows"):
        for wf in extraction["workflows"]:
            slug = wf.get("slug", "")
            title = wf.get("title", slug)
            workflows_md.append(f"- **{title}** — {wf.get('goal', '')} See [[workflows/{slug}]]")
    
    concepts_md = [f"- [[{c}]]" for c in extraction.get("related_concepts", [])]
    media_index_rel = f"media/{video['category']}/{video['slug']}/media-index"
    
    content = f"""---
title: "{metadata.get('title', video['slug'])}"
video_id: "{metadata.get('video_id', '')}"
url: "{metadata.get('url', '')}"
channel: "{metadata.get('channel', '')}"
category: {video['category']}
duration: "{metadata.get('duration', '')}"
type: insight
tags: [{', '.join(tags)}]
created: {datetime.now().strftime('%Y-%m-%d')}
updated: {datetime.now().strftime('%Y-%m-%d')}
has_workflow: {str(has_workflow).lower()}
has_skills: {str(has_skills).lower()}
media_index: "{media_index_rel}"
---

## Summary

{extraction.get('summary', 'No summary extracted.')}

## Key Insights

{chr(10).join(insights_md) if insights_md else 'No key insights extracted.'}

"""
    if skills_md:
        content += f"## Skills\n\n{chr(10).join(skills_md)}\n\n"
    if workflows_md:
        content += f"## Workflows\n\n{chr(10).join(workflows_md)}\n\n"
    
    content += f"""## Related Concepts

{chr(10).join(concepts_md) if concepts_md else '- [[general]]'}

## Media

See [[{media_index_rel}]] for full screenshot and clip gallery.

## Source

Channel: {metadata.get('channel', 'Unknown')} | URL: {metadata.get('url', '')} | Fetched: {metadata.get('fetched_at', 'Unknown')}
"""
    
    with open(insight_path, "w") as f:
        f.write(content)

def write_media_index(video: dict, metadata: dict):
    media_path = VAULT_ROOT / "media" / video["category"] / video["slug"] / "media-index.md"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    
    content = f"""---
title: "Media Index — {metadata.get('title', video['slug'])}"
type: media-index
video_id: "{metadata.get('video_id', '')}"
category: {video['category']}
screenshot_count: 0
clip_count: 0
created: {datetime.now().strftime('%Y-%m-%d')}
---

## Screenshots

No screenshots available (auto-fetched via watcher).

## Clips

No clips available (auto-fetched via watcher).
"""
    with open(media_path, "w") as f:
        f.write(content)

def write_skill_page(skill: dict, video: dict):
    slug = skill.get("slug", "unknown-skill")
    skill_path = VAULT_ROOT / "skills" / f"{slug}.md"
    
    if skill_path.exists():
        with open(skill_path, "r") as f:
            existing = f.read()
        if f"insights/{video['category']}/{video['slug']}" not in existing:
            with open(skill_path, "a") as f:
                f.write(f"\n- [[insights/{video['category']}/{video['slug']}]]\n")
        return
    
    content = f"""---
title: "{skill.get('title', slug)}"
type: skill
source_videos: ["[[insights/{video['category']}/{video['slug']}]]"]
tags: []
created: {datetime.now().strftime('%Y-%m-%d')}
updated: {datetime.now().strftime('%Y-%m-%d')}
---

## What This Skill Is

{skill.get('what', '')}

## When To Use It

{skill.get('when', '')}

## Steps / Technique

{skill.get('steps', '')}

## Tips and Pitfalls

{skill.get('tips', '')}

## Source Videos

- [[insights/{video['category']}/{video['slug']}]]
"""
    with open(skill_path, "w") as f:
        f.write(content)
    watcher_stats["skills_created"] += 1

def write_workflow_page(wf: dict, video: dict):
    slug = wf.get("slug", "unknown-workflow")
    wf_path = VAULT_ROOT / "workflows" / f"{slug}.md"
    
    if wf_path.exists():
        with open(wf_path, "r") as f:
            existing = f.read()
        if f"insights/{video['category']}/{video['slug']}" not in existing:
            with open(wf_path, "a") as f:
                f.write(f"\n- [[insights/{video['category']}/{video['slug']}]]\n")
        return
    
    steps_text = wf.get("steps", "")
    step_count = steps_text.count("\n") + 1 if steps_text else 0
    
    content = f"""---
title: "{wf.get('title', slug)}"
type: workflow
source_videos: ["[[insights/{video['category']}/{video['slug']}]]"]
steps: {step_count}
tags: []
created: {datetime.now().strftime('%Y-%m-%d')}
updated: {datetime.now().strftime('%Y-%m-%d')}
---

## Goal

{wf.get('goal', '')}

## Prerequisites

{wf.get('prerequisites', '')}

## Steps

{steps_text}

## Outcome

{wf.get('outcome', '')}

## Source Videos

- [[insights/{video['category']}/{video['slug']}]]
"""
    with open(wf_path, "w") as f:
        f.write(content)
    watcher_stats["workflows_created"] += 1

def update_index():
    """Update the main INDEX.md with current vault state."""
    insight_pages = []
    insights_dir = VAULT_ROOT / "insights"
    if insights_dir.exists():
        for cat_dir in sorted(insights_dir.iterdir()):
            if cat_dir.is_dir():
                for page in sorted(cat_dir.glob("*.md")):
                    insight_pages.append((cat_dir.name, page.stem))
    
    skill_pages = sorted((VAULT_ROOT / "skills").glob("*.md")) if (VAULT_ROOT / "skills").exists() else []
    wf_pages = sorted((VAULT_ROOT / "workflows").glob("*.md")) if (VAULT_ROOT / "workflows").exists() else []
    
    elapsed = time.time() - watcher_stats["start_time"] if watcher_stats["start_time"] else 0
    mins, secs = divmod(int(elapsed), 60)
    hours, mins = divmod(mins, 60)
    
    content = f"""---
title: "NuxTube Knowledge Graft Index"
updated: {datetime.now().isoformat()}
total_insights: {len(insight_pages)}
total_skills: {len(skill_pages)}
total_workflows: {len(wf_pages)}
lessons_learned: {len(lessons_learned)}
watcher_uptime: "{hours}h {mins}m {secs}s"
watcher_polls: {watcher_stats['polls']}
---

# 🧠 NuxTube Knowledge Graft

> **Last updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> **Watcher status:** {watcher_stats['polls']} polls, {watcher_stats['videos_processed']} videos processed

## 📊 Vault Stats

- **Insight pages:** {len(insight_pages)}
- **Skill pages:** {len(skill_pages)}
- **Workflow pages:** {len(wf_pages)}
- **Lessons learned:** {len(lessons_learned)}

## 🧠 Lessons Learned ({len(lessons_learned)})

"""
    for i, lesson in enumerate(lessons_learned, 1):
        content += f"{i}. {lesson}\n"
    
    content += f"\n## 📝 Insight Pages ({len(insight_pages)})\n\n"
    current_cat = None
    for cat, slug in insight_pages:
        if cat != current_cat:
            content += f"\n### {cat.title()}\n\n"
            current_cat = cat
        content += f"- [[insights/{cat}/{slug}]]\n"
    
    content += f"\n## 🔧 Skills ({len(skill_pages)})\n\n"
    for sk in skill_pages:
        content += f"- [[skills/{sk.stem}]]\n"
    
    content += f"\n## 🔄 Workflows ({len(wf_pages)})\n\n"
    for wf in wf_pages:
        content += f"- [[workflows/{wf.stem}]]\n"
    
    with open(INDEX_PATH, "w") as f:
        f.write(content)

# ═══════════════════════════════════════════════════════════════════
# VIDEO PROCESSOR
# ═══════════════════════════════════════════════════════════════════

async def process_new_video(session: aiohttp.ClientSession, video_info: dict, sem: asyncio.Semaphore, recent_logs: list[str]) -> bool:
    """Fetch data for a new video and process it into the knowledge graph."""
    async with sem:
        vid = video_info["id"]
        title = video_info["title"]
        url = video_info["url"]
        
        display = f"[NEW] {title[:60]}"
        print_dashboard("Processing new video...", display)
        
        # Step 1: Fetch transcript + metadata
        print_dashboard(f"Fetching transcript for {vid}...", display)
        video = fetch_video_data(vid, title, url)
        
        if not video:
            log(f"Failed to fetch data for {title} ({vid})", "error")
            watcher_stats["videos_failed"] += 1
            return False
        
        # Step 2: Read files
        try:
            with open(video["metadata_path"]) as f:
                metadata = json.load(f)
            with open(video["transcript_path"]) as f:
                transcript = f.read()
        except Exception as e:
            log(f"Failed to read files for {vid}: {e}", "error")
            watcher_stats["videos_failed"] += 1
            return False
        
        if not transcript.strip():
            log(f"Empty transcript for {title}", "warn")
            watcher_stats["videos_failed"] += 1
            return False
        
        # Step 3: Call LLM for extraction
        print_dashboard(f"Extracting insights via {MODEL}...", display)
        extraction = await call_llm(session, video, metadata, transcript)
        
        if not extraction:
            log(f"Failed to extract insights for {title}", "error")
            watcher_stats["videos_failed"] += 1
            return False
        
        # Step 4: Write Obsidian pages
        print_dashboard("Writing Obsidian pages...", display)
        try:
            write_insight_page(video, metadata, extraction)
            log(f"Insight page: [{video['category']}] {video['slug']}", "ok")
            recent_logs.append(f"Insight page written for {video['slug']}")
        except Exception as e:
            log(f"Failed to write insight page: {e}", "error")
            watcher_stats["videos_failed"] += 1
            return False
        
        try:
            write_media_index(video, metadata)
        except Exception as e:
            log(f"Media index write failed: {e}", "warn")
        
        if extraction.get("has_skills") and extraction.get("skills"):
            for skill in extraction["skills"]:
                try:
                    write_skill_page(skill, video)
                    log(f"Skill: {skill.get('slug', '?')}", "skill")
                    recent_logs.append(f"Skill page written: {skill.get('slug')}")
                except Exception as e:
                    log(f"Skill write failed: {e}", "warn")
        
        if extraction.get("has_workflow") and extraction.get("workflows"):
            for wf in extraction["workflows"]:
                try:
                    write_workflow_page(wf, video)
                    log(f"Workflow: {wf.get('slug', '?')}", "workflow")
                    recent_logs.append(f"Workflow page written: {wf.get('slug')}")
                except Exception as e:
                    log(f"Workflow write failed: {e}", "warn")
        
        watcher_stats["videos_processed"] += 1
        watcher_stats["last_new_video"] = time.time()
        log(f"✨ Processed: {title}", "ok")
        
        # Update index
        update_index()
        
        return True

# ═══════════════════════════════════════════════════════════════════
# MAIN WATCH LOOP
# ═══════════════════════════════════════════════════════════════════

async def watch_loop():
    watcher_stats["start_time"] = time.time()
    
    # Load state and lessons
    state = load_state()
    load_lessons()
    
    # Create vault structure
    for subdir in ["insights", "media", "skills", "workflows", "_staging"]:
        (VAULT_ROOT / subdir).mkdir(parents=True, exist_ok=True)
    
    known_ids = set(state.get("known_ids", []))
    
    log(f"Watcher started. {len(known_ids)} known videos, {len(lessons_learned)} lessons loaded")
    
    # If first run, do an initial scan without processing (just record IDs)
    if not known_ids:
        log("First run — scanning playlist to establish baseline...")
        print_dashboard("First run — scanning playlist...", "")
        
        playlist = fetch_playlist_videos()
        if playlist:
            known_ids = {v["id"] for v in playlist}
            state["known_ids"] = list(known_ids)
            save_state(state)
            log(f"Baseline established: {len(known_ids)} videos recorded. Will watch for NEW videos only.")
            print_dashboard(f"Baseline: {len(known_ids)} videos. Watching for new ones...", "")
            await asyncio.sleep(3)
    
    recent_logs = []
    
    async with aiohttp.ClientSession() as session:
        while True:
            # Poll playlist
            watcher_stats["polls"] += 1
            watcher_stats["last_poll"] = time.time()
            
            print_dashboard(f"Polling playlist (poll #{watcher_stats['polls']})...", "")
            log(f"Poll #{watcher_stats['polls']}: fetching playlist...", "poll")
            
            playlist = fetch_playlist_videos()
            
            if not playlist:
                log("Failed to fetch playlist", "error")
                print_dashboard("Poll failed — retrying next cycle", "")
                await asyncio.sleep(POLL_INTERVAL)
                continue
            
            log(f"Poll #{watcher_stats['polls']}: {len(playlist)} videos in playlist", "poll")
            
            # Find new videos
            new_videos = find_new_videos(playlist, known_ids)
            
            if new_videos:
                log(f"🆕 {len(new_videos)} NEW video(s) found!", "new")
                watcher_stats["new_videos_found"] += len(new_videos)
                
                # Process each new video
                sem = asyncio.Semaphore(CONCURRENCY)
                for video_info in new_videos:
                    success = await process_new_video(session, video_info, sem, recent_logs)
                    
                    # Add to known IDs regardless of success (don't retry forever)
                    known_ids.add(video_info["id"])
                    state["known_ids"] = list(known_ids)
                    save_state(state)
                
                # Self-improve after processing batch
                if len(new_videos) >= 3:
                    print_dashboard("🧠 Self-improving...", "")
                    await self_improve(session, recent_logs)
                    save_lessons()
                
                update_index()
            else:
                log(f"No new videos. Next poll in {POLL_INTERVAL // 60} min", "info")
            
            # Countdown to next poll
            for remaining in range(POLL_INTERVAL, 0, -10):
                print_dashboard(
                    f"Next poll in {remaining // 60}m {remaining % 60}s",
                    next_poll_in=remaining
                )
                await asyncio.sleep(min(10, remaining))
                if remaining <= 10:
                    break

def handle_signal(signum, frame):
    """Handle SIGTERM/SIGINT gracefully."""
    log(f"Watcher stopping (signal {signum})...", "warn")
    save_lessons()
    update_index()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    
    daemon_mode = "--daemon" in sys.argv
    
    if daemon_mode:
        # Daemon mode: redirect output to log file
        log_path = VAULT_ROOT / "_staging" / "watcher-daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"Starting in daemon mode. Output: {log_path}")
        asyncio.run(watch_loop())
    else:
        asyncio.run(watch_loop())
