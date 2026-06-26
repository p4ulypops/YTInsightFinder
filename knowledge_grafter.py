#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  🧠 NuxTube Knowledge Grafter                                     ║
║  ═════════════════════════════════                                ║
║  Processes YouTube transcripts into Obsidian knowledge-graph      ║
║  pages. Uses OpenRouter API with gemini-2.5-flash.               ║
║  Self-improves every 5 videos by reviewing logs and              ║
║  optimising the extraction prompt.                               ║
║                                                                  ║
║  Data sources:                                                   ║
║    1. _test_data/ (27 videos, some with key-points.json)         ║
║    2. YoutubeInsights/ (83 videos, transcript only)              ║
║                                                                  ║
║  Output: /Volumes/PSILVER-2TB/NuxTubeInsights/                   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import aiohttp
import json
import os
import re
import time
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

VAULT_ROOT = Path("/Volumes/PSILVER-2TB/NuxTubeInsights")
DATA_SOURCES = [
    Path("/Users/user/Projects2026/NeuroD-NuxTube/youtube_videos/_test_data"),
    Path("/Volumes/PSILVER-2TB/YoutubeInsights"),
]
INDEX_PATH = VAULT_ROOT / "INDEX.md"
LOG_PATH = VAULT_ROOT / "_staging" / "graft-log.md"
LESSONS_PATH = VAULT_ROOT / "_staging" / "lessons-learned.md"

MODEL = "google/gemini-2.5-flash"
CONCURRENCY = 5
SELF_IMPROVE_INTERVAL = 5
MAX_TRANSCRIPT_LINES = 2000

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
    "vector-database", "fine-tuning", "agents", "security", "prompt-injection"
}

# ═══════════════════════════════════════════════════════════════════
# SELF-IMPROVEMENT STATE
# ═══════════════════════════════════════════════════════════════════

lessons_learned = []
stats = {
    "total": 0,
    "completed": 0,
    "failed": 0,
    "skipped": 0,
    "skills_created": 0,
    "workflows_created": 0,
    "api_errors": 0,
    "json_errors": 0,
    "empty_extractions": 0,
    "retries_used": 0,
    "start_time": None,
    "current_batch": 0,
}
recent_logs = []  # Last 5 video logs for self-improvement review

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
    # Background
    BG_BLUE = "\033[44m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_RED = "\033[41m"

def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")

def print_dashboard(current_video: str = "", status: str = ""):
    """Print the live dashboard to terminal."""
    clear_screen()
    elapsed = time.time() - stats["start_time"] if stats["start_time"] else 0
    mins, secs = divmod(int(elapsed), 60)
    
    total = stats["total"]
    done = stats["completed"]
    failed = stats["failed"]
    skipped = stats["skipped"]
    remaining = total - done - failed - skipped
    pct = (done / total * 100) if total > 0 else 0
    
    # Progress bar
    bar_width = 40
    filled = int(bar_width * done / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    
    print(f"{Colors.CYAN}{Colors.BOLD}")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  🧠 NUXTUBE KNOWLEDGE GRAFTER                                    ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"{Colors.RESET}")
    
    print(f"  {Colors.GREEN}📊 PROGRESS{Colors.RESET}")
    print(f"  {bar} {pct:.1f}%")
    print(f"  ✅ {done} completed  ❌ {failed} failed  ⏭️  {skipped} skipped  ⏳ {remaining} remaining  /  {total} total")
    print(f"  ⏱️  Elapsed: {mins}m {secs}s")
    if done > 0 and elapsed > 0:
        rate = done / (elapsed / 60)
        eta_mins = remaining / rate if rate > 0 else 0
        eta_int = int(eta_mins)
        eta_secs = int((eta_mins - eta_int) * 60)
        print(f"  🚀 Rate: {rate:.1f} videos/min  |  🕐 ETA: {eta_int}m {eta_secs}s")
    
    print()
    print(f"  {Colors.MAGENTA}📦 OUTPUT{Colors.RESET}")
    print(f"  📝 Insight pages: {done}")
    print(f"  🔧 Skills created: {stats['skills_created']}")
    print(f"  🔄 Workflows created: {stats['workflows_created']}")
    
    print()
    print(f"  {Colors.YELLOW}🧠 SELF-IMPROVEMENT{Colors.RESET}")
    print(f"  📚 Lessons learned: {len(lessons_learned)}")
    print(f"  🔁 Batch: {stats['current_batch']}  |  🩺 API errors: {stats['api_errors']}  |  📋 JSON errors: {stats['json_errors']}")
    if lessons_learned:
        for i, lesson in enumerate(lessons_learned[-3:], 1):
            print(f"     {Colors.DIM}{i}. {lesson[:70]}...{Colors.RESET}" if len(lesson) > 70 else f"     {Colors.DIM}{i}. {lesson}{Colors.RESET}")
    
    if current_video:
        print()
        print(f"  {Colors.BLUE}▶️  NOW PROCESSING{Colors.RESET}")
        print(f"  {current_video}")
        if status:
            print(f"  {Colors.DIM}{status}{Colors.RESET}")
    
    print()
    print(f"  {Colors.DIM}Vault: {VAULT_ROOT}{Colors.RESET}")
    print(f"  {Colors.DIM}Model: {MODEL}{Colors.RESET}")
    print(f"  {Colors.DIM}Concurrency: {CONCURRENCY}{Colors.RESET}")
    print()

def log(msg: str, level: str = "info"):
    """Log a message and store for self-improvement review."""
    ts = datetime.now().strftime("%H:%M:%S")
    emoji = {"info": "ℹ️", "ok": "✅", "warn": "⚠️", "error": "❌", "skill": "🔧", "workflow": "🔄"}.get(level, "ℹ️")
    entry = f"[{ts}] {emoji} {msg}"
    recent_logs.append(entry)
    # Keep only last 50 logs
    if len(recent_logs) > 50:
        recent_logs.pop(0)
    
    # Append to log file
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(entry + "\n")
    except:
        pass

# ═══════════════════════════════════════════════════════════════════
# VIDEO DISCOVERY
# ═══════════════════════════════════════════════════════════════════

def discover_videos() -> list[dict]:
    """Scan all data sources and return list of video dicts."""
    videos = []
    for source in DATA_SOURCES:
        if not source.exists():
            continue
        for category_dir in sorted(source.iterdir()):
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            # Skip non-category dirs
            if category.startswith("_") or category.startswith("."):
                continue
            for video_dir in sorted(category_dir.iterdir()):
                if not video_dir.is_dir():
                    continue
                slug = video_dir.name
                metadata_path = video_dir / "metadata.json"
                transcript_path = video_dir / "transcript.md"
                keypoints_path = video_dir / "key-points.json"
                screenshots_manifest = video_dir / "_screenshots_manifest.json"
                clips_manifest = video_dir / "_clips_manifest.json"
                
                if not metadata_path.exists() or not transcript_path.exists():
                    continue
                
                # Check if insight page already exists
                insight_path = VAULT_ROOT / "insights" / category / f"{slug}.md"
                if insight_path.exists():
                    stats["skipped"] += 1
                    continue
                
                videos.append({
                    "category": category,
                    "slug": slug,
                    "video_dir": str(video_dir),
                    "source": str(source),
                    "metadata_path": str(metadata_path),
                    "transcript_path": str(transcript_path),
                    "keypoints_path": str(keypoints_path) if keypoints_path.exists() else None,
                    "screenshots_manifest": str(screenshots_manifest) if screenshots_manifest.exists() else None,
                    "clips_manifest": str(clips_manifest) if clips_manifest.exists() else None,
                    "insight_path": str(insight_path),
                })
    return videos

# ═══════════════════════════════════════════════════════════════════
# LLM API
# ═══════════════════════════════════════════════════════════════════

def build_system_prompt() -> str:
    """Build the system prompt, including any lessons learned."""
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

def build_user_prompt(video: dict, metadata: dict, transcript: str, keypoints: str = None) -> str:
    """Build the user prompt for a video."""
    parts = [f"## Video Metadata"]
    parts.append(json.dumps({
        "title": metadata.get("title", "Unknown"),
        "channel": metadata.get("channel", "Unknown"),
        "duration": metadata.get("duration", "Unknown"),
        "category": video["category"],
        "segment_count": metadata.get("segment_count", 0),
    }, indent=2))
    
    if keypoints:
        parts.append(f"\n## Pre-extracted Key Points (use as reference, enhance with transcript)")
        parts.append(keypoints[:5000])  # Cap to avoid token explosion
    
    parts.append(f"\n## Transcript (first {MAX_TRANSCRIPT_LINES} lines)")
    lines = transcript.split("\n")
    parts.append("\n".join(lines[:MAX_TRANSCRIPT_LINES]))
    
    return "\n".join(parts)

async def call_llm(session: aiohttp.ClientSession, video: dict, metadata: dict, transcript: str, keypoints: str = None) -> dict:
    """Call the LLM API and return parsed JSON."""
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(video, metadata, transcript, keypoints)
    
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,  # Low temp for consistent extraction
        "max_tokens": 8000,
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/p4ulypops/YTInsightFinder",
        "X-Title": "NuxTube Knowledge Grafter",
    }
    
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    for attempt in range(3):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    log(f"API error {resp.status} for {video['slug']}: {error_text[:200]}", "error")
                    stats["api_errors"] += 1
                    if attempt < 2:
                        stats["retries_used"] += 1
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return None
                
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                
                # Strip markdown fences if present
                content = content.strip()
                if content.startswith("```"):
                    content = re.sub(r'^```(?:json)?\n?', '', content)
                    content = re.sub(r'\n?```$', '', content)
                
                try:
                    parsed = json.loads(content)
                    return parsed
                except json.JSONDecodeError as e:
                    log(f"JSON parse error for {video['slug']}: {e}", "error")
                    stats["json_errors"] += 1
                    # Try to extract JSON from text
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        try:
                            parsed = json.loads(json_match.group())
                            return parsed
                        except:
                            pass
                    if attempt < 2:
                        stats["retries_used"] += 1
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return None
        except asyncio.TimeoutError:
            log(f"Timeout for {video['slug']} (attempt {attempt+1})", "error")
            stats["api_errors"] += 1
            if attempt < 2:
                stats["retries_used"] += 1
                await asyncio.sleep(2 ** attempt)
                continue
            return None
        except Exception as e:
            log(f"Exception for {video['slug']}: {e}", "error")
            stats["api_errors"] += 1
            if attempt < 2:
                stats["retries_used"] += 1
                await asyncio.sleep(2 ** attempt)
                continue
            return None
    
    return None

# ═══════════════════════════════════════════════════════════════════
# SELF-IMPROVEMENT
# ═══════════════════════════════════════════════════════════════════

async def self_improve(session: aiohttp.ClientSession):
    """Review recent logs and extract new lessons."""
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
        async with session.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
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
                                log(f"New lesson learned: {lesson}", "ok")
                                print(f"  {Colors.GREEN}🧠 NEW LESSON: {lesson}{Colors.RESET}")
                        
                        # Save lessons to file
                        LESSONS_PATH.parent.mkdir(parents=True, exist_ok=True)
                        with open(LESSONS_PATH, "w") as f:
                            f.write("# Lessons Learned (Self-Improvement)\n\n")
                            f.write(f"Updated: {datetime.now().isoformat()}\n\n")
                            for i, lesson in enumerate(lessons_learned, 1):
                                f.write(f"{i}. {lesson}\n")
                except json.JSONDecodeError:
                    pass  # Non-critical
    except Exception as e:
        log(f"Self-improvement error: {e}", "warn")

# ═══════════════════════════════════════════════════════════════════
# FILE WRITERS
# ═══════════════════════════════════════════════════════════════════

def filter_tags(tags: list) -> list:
    """Filter tags to only valid taxonomy, map common variants."""
    tag_map = {
        "ai": "ai-agents",
        "agents": "ai-agents",
        "ai agent": "ai-agents",
        "obsidian vault": "obsidian",
        "knowledge management": "pkm",
        "personal knowledge management": "pkm",
        "second brain": "second-brain",
        "3d printing": "3d-printing",
        "3d-printing": "3d-printing",
        "ui design": "ui",
        "ux design": "ux",
        "prompt engineering": "prompt-engineering",
        "prompt-injection": "security",
    }
    result = []
    for tag in tags:
        tag = tag.lower().strip()
        tag = tag_map.get(tag, tag)
        if tag in VALID_TAGS and tag not in result:
            result.append(tag)
    # Ensure at least 2 tags
    if len(result) < 2:
        result.extend(["tutorial", "tips-and-tricks"])
    return result[:6]

def write_insight_page(video: dict, metadata: dict, extraction: dict):
    """Write the main insight page."""
    insight_path = Path(video["insight_path"])
    insight_path.parent.mkdir(parents=True, exist_ok=True)
    
    tags = filter_tags(extraction.get("tags", []))
    has_skills = extraction.get("has_skills", False)
    has_workflow = extraction.get("has_workflow", False)
    
    # Build key insights section
    insights_md = []
    for ki in extraction.get("key_insights", []):
        ts = ki.get("timestamp", "")
        text = ki.get("insight", "")
        wikilinks = ki.get("wikilinks", [])
        # Add wikilinks to the text if not already there
        for wl in wikilinks:
            if f"[[{wl}]]" not in text:
                text = text.replace(wl.replace("-", " "), f"[[{wl}]]", 1) if wl.replace("-", " ") in text.lower() else text
        insights_md.append(f"- ⏱ {ts} — {text}")
    
    # Build skills section
    skills_md = []
    if has_skills and extraction.get("skills"):
        for skill in extraction["skills"]:
            slug = skill.get("slug", "")
            title = skill.get("title", slug)
            skills_md.append(f"- **{title}** — {skill.get('what', '')} See [[skills/{slug}]]")
    
    # Build workflows section
    workflows_md = []
    if has_workflow and extraction.get("workflows"):
        for wf in extraction["workflows"]:
            slug = wf.get("slug", "")
            title = wf.get("title", slug)
            workflows_md.append(f"- **{title}** — {wf.get('goal', '')} See [[workflows/{slug}]]")
    
    # Related concepts
    concepts_md = [f"- [[{c}]]" for c in extraction.get("related_concepts", [])]
    
    # Media section
    media_md = []
    media_index_rel = f"media/{video['category']}/{video['slug']}/media-index"
    media_md.append(f"See [[{media_index_rel}]] for full screenshot and clip gallery.")
    
    # Add screenshot embeds if available
    screenshots = get_screenshot_paths(video)
    for i, ss in enumerate(screenshots[:3]):
        media_md.append(f"\n![{ss['description']}]({ss['path']})")
    
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

{chr(10).join(media_md)}

## Source

Channel: {metadata.get('channel', 'Unknown')} | URL: {metadata.get('url', '')} | Fetched: {metadata.get('fetched_at', 'Unknown')}
"""
    
    with open(insight_path, "w") as f:
        f.write(content)

def write_media_index(video: dict, metadata: dict):
    """Write the media index page for a video."""
    media_path = VAULT_ROOT / "media" / video["category"] / video["slug"] / "media-index.md"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    
    screenshots = get_screenshot_paths(video)
    clips = get_clip_paths(video)
    
    ss_md = []
    for ss in screenshots:
        ss_md.append(f"### ⏱ {ss['timestamp']}\n- Context: {ss['description']}\n- ![{ss['description']}]({ss['path']})\n")
    
    clip_md = []
    for clip in clips:
        clip_md.append(f"### ⏱ {clip['timestamp']}\n- {clip['description']}\n- Path: `{clip['path']}`\n")
    
    content = f"""---
title: "Media Index — {metadata.get('title', video['slug'])}"
type: media-index
video_id: "{metadata.get('video_id', '')}"
category: {video['category']}
screenshot_count: {len(screenshots)}
clip_count: {len(clips)}
created: {datetime.now().strftime('%Y-%m-%d')}
---

## Screenshots

{chr(10).join(ss_md) if ss_md else 'No screenshots available.'}

## Clips

{chr(10).join(clip_md) if clip_md else 'No clips available.'}
"""
    
    with open(media_path, "w") as f:
        f.write(content)

def write_skill_page(skill: dict, video: dict):
    """Write a skill page."""
    slug = skill.get("slug", "unknown-skill")
    skill_path = VAULT_ROOT / "skills" / f"{slug}.md"
    
    # Don't overwrite if exists — append source video
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
    stats["skills_created"] += 1

def write_workflow_page(wf: dict, video: dict):
    """Write a workflow page."""
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
    stats["workflows_created"] += 1

# ═══════════════════════════════════════════════════════════════════
# MEDIA HELPERS
# ═══════════════════════════════════════════════════════════════════

def get_screenshot_paths(video: dict) -> list[dict]:
    """Get screenshot paths from manifest or directory listing."""
    screenshots = []
    video_dir = Path(video["video_dir"])
    manifest_path = video.get("screenshots_manifest")
    
    if manifest_path and os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            for entry in manifest if isinstance(manifest, list) else manifest.get("screenshots", []):
                filename = entry.get("filename", entry.get("path", ""))
                if filename:
                    full_path = str(video_dir / "screenshots" / filename) if not os.path.isabs(filename) else filename
                    screenshots.append({
                        "path": full_path,
                        "timestamp": entry.get("timestamp", entry.get("time", "")),
                        "description": entry.get("description", entry.get("context", filename)),
                    })
        except:
            pass
    
    # Fallback: scan screenshots directory
    if not screenshots:
        ss_dir = video_dir / "screenshots"
        if ss_dir.exists():
            for ss in sorted(ss_dir.glob("*.jpg")) + sorted(ss_dir.glob("*.png")):
                # Try to extract timestamp from filename
                name = ss.stem
                screenshots.append({
                    "path": str(ss),
                    "timestamp": name,
                    "description": name.replace("_", " "),
                })
    
    return screenshots

def get_clip_paths(video: dict) -> list[dict]:
    """Get clip paths from manifest or directory listing."""
    clips = []
    video_dir = Path(video["video_dir"])
    manifest_path = video.get("clips_manifest")
    
    if manifest_path and os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            for entry in manifest if isinstance(manifest, list) else manifest.get("clips", []):
                filename = entry.get("filename", entry.get("path", ""))
                if filename:
                    full_path = str(video_dir / "clips" / filename) if not os.path.isabs(filename) else filename
                    clips.append({
                        "path": full_path,
                        "timestamp": entry.get("timestamp", entry.get("time", "")),
                        "description": entry.get("description", entry.get("context", filename)),
                    })
        except:
            pass
    
    if not clips:
        clips_dir = video_dir / "clips"
        if clips_dir.exists():
            for clip in sorted(clips_dir.glob("*.mp4")):
                clips.append({
                    "path": str(clip),
                    "timestamp": clip.stem,
                    "description": clip.stem.replace("_", " "),
                })
    
    return clips

# ═══════════════════════════════════════════════════════════════════
# INDEX WRITER
# ═══════════════════════════════════════════════════════════════════

def write_index(videos: list[dict]):
    """Write the continuously updated INDEX.md to the vault root."""
    elapsed = time.time() - stats["start_time"] if stats["start_time"] else 0
    mins, secs = divmod(int(elapsed), 60)
    done = stats["completed"]
    total = stats["total"]
    pct = (done / total * 100) if total > 0 else 0
    
    # Collect all insight pages
    insight_pages = []
    for cat_dir in sorted((VAULT_ROOT / "insights").iterdir()) if (VAULT_ROOT / "insights").exists() else []:
        if cat_dir.is_dir():
            for page in sorted(cat_dir.glob("*.md")):
                slug = page.stem
                insight_pages.append((cat_dir.name, slug, page))
    
    # Collect skills
    skill_pages = sorted((VAULT_ROOT / "skills").glob("*.md")) if (VAULT_ROOT / "skills").exists() else []
    
    # Collect workflows
    wf_pages = sorted((VAULT_ROOT / "workflows").glob("*.md")) if (VAULT_ROOT / "workflows").exists() else []
    
    content = f"""---
title: "NuxTube Knowledge Graft Index"
updated: {datetime.now().isoformat()}
total_videos: {total}
completed: {done}
failed: {stats['failed']}
skipped: {stats['skipped']}
skills: {len(skill_pages)}
workflows: {len(wf_pages)}
lessons_learned: {len(lessons_learned)}
---

# 🧠 NuxTube Knowledge Graft

> **Last updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 📊 Progress

- **Completed:** {done} / {total} ({pct:.1f}%)
- **Failed:** {stats['failed']}
- **Skipped (already done):** {stats['skipped']}
- **Elapsed:** {mins}m {secs}s
- **Skills created:** {stats['skills_created']}
- **Workflows created:** {stats['workflows_created']}

## 🧠 Lessons Learned ({len(lessons_learned)})

"""
    for i, lesson in enumerate(lessons_learned, 1):
        content += f"{i}. {lesson}\n"
    
    content += f"\n## 📝 Insight Pages ({len(insight_pages)})\n\n"
    
    current_cat = None
    for cat, slug, path in insight_pages:
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

async def process_video(session: aiohttp.ClientSession, video: dict, sem: asyncio.Semaphore):
    """Process a single video: extract insights and write all files."""
    async with sem:
        slug = video["slug"]
        cat = video["category"]
        display = f"[{cat}] {slug}"
        
        print_dashboard(display, "Reading metadata + transcript...")
        
        # Read metadata
        try:
            with open(video["metadata_path"]) as f:
                metadata = json.load(f)
        except Exception as e:
            log(f"Failed to read metadata for {slug}: {e}", "error")
            stats["failed"] += 1
            return
        
        # Read transcript
        try:
            with open(video["transcript_path"]) as f:
                transcript = f.read()
        except Exception as e:
            log(f"Failed to read transcript for {slug}: {e}", "error")
            stats["failed"] += 1
            return
        
        # Read key-points if available
        keypoints = None
        if video.get("keypoints_path"):
            try:
                with open(video["keypoints_path"]) as f:
                    keypoints = f.read()
            except:
                pass
        
        # Check for empty extraction
        if not transcript.strip():
            log(f"Empty transcript for {slug}", "warn")
            stats["empty_extractions"] += 1
        
        print_dashboard(display, f"Calling {MODEL}...")
        
        # Call LLM
        extraction = await call_llm(session, video, metadata, transcript, keypoints)
        
        if not extraction:
            log(f"Failed to extract insights for {slug}", "error")
            stats["failed"] += 1
            write_index([])
            return
        
        print_dashboard(display, "Writing Obsidian pages...")
        
        # Write insight page
        try:
            write_insight_page(video, metadata, extraction)
            log(f"Insight page written: [{cat}] {slug}", "ok")
        except Exception as e:
            log(f"Failed to write insight page for {slug}: {e}", "error")
            stats["failed"] += 1
            return
        
        # Write media index
        try:
            write_media_index(video, metadata)
        except Exception as e:
            log(f"Failed to write media index for {slug}: {e}", "warn")
        
        # Write skill pages
        if extraction.get("has_skills") and extraction.get("skills"):
            for skill in extraction["skills"]:
                try:
                    write_skill_page(skill, video)
                    log(f"Skill page written: {skill.get('slug', '?')}", "skill")
                except Exception as e:
                    log(f"Failed to write skill page: {e}", "warn")
        
        # Write workflow pages
        if extraction.get("has_workflow") and extraction.get("workflows"):
            for wf in extraction["workflows"]:
                try:
                    write_workflow_page(wf, video)
                    log(f"Workflow page written: {wf.get('slug', '?')}", "workflow")
                except Exception as e:
                    log(f"Failed to write workflow page: {e}", "warn")
        
        stats["completed"] += 1
        log(f"✨ Completed: [{cat}] {slug} ({stats['completed']}/{stats['total']})", "ok")
        
        # Update index
        write_index([])
        
        print_dashboard(display, "✅ Done!")

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

async def main():
    stats["start_time"] = time.time()
    
    print(f"{Colors.CYAN}{Colors.BOLD}🧠 NuxTube Knowledge Grafter{Colors.RESET}")
    print(f"{Colors.DIM}Scanning for videos...{Colors.RESET}")
    
    videos = discover_videos()
    stats["total"] = len(videos) + stats["skipped"]  # Total includes already-done
    
    print(f"\n📊 Found {len(videos)} videos to process ({stats['skipped']} already done)")
    print(f"📦 Total: {stats['total']} videos")
    print(f"🤖 Model: {MODEL}")
    print(f"⚡ Concurrency: {CONCURRENCY}")
    print(f"🧠 Self-improve every: {SELF_IMPROVE_INTERVAL} videos")
    print(f"\n{Colors.DIM}Press Enter to start...{Colors.RESET}")
    
    # Don't wait for input in non-interactive mode
    # input()
    
    if not videos:
        print(f"{Colors.GREEN}✅ All videos already processed!{Colors.RESET}")
        return
    
    # Create vault structure
    for subdir in ["insights", "media", "skills", "workflows", "_staging"]:
        (VAULT_ROOT / subdir).mkdir(parents=True, exist_ok=True)
    
    sem = asyncio.Semaphore(CONCURRENCY)
    
    async with aiohttp.ClientSession() as session:
        # Process in batches for self-improvement
        for i in range(0, len(videos), SELF_IMPROVE_INTERVAL):
            batch = videos[i:i + SELF_IMPROVE_INTERVAL]
            stats["current_batch"] += 1
            
            print_dashboard("", f"Batch {stats['current_batch']} — {len(batch)} videos")
            
            # Process batch concurrently
            tasks = [process_video(session, v, sem) for v in batch]
            await asyncio.gather(*tasks)
            
            # Self-improvement review after each batch
            if i + SELF_IMPROVE_INTERVAL < len(videos):
                print_dashboard("", "🧠 Self-improving...")
                await self_improve(session)
    
    # Final dashboard
    elapsed = time.time() - stats["start_time"]
    mins, secs = divmod(int(elapsed), 60)
    
    print_dashboard("", "🎉 All done!")
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}🎉 COMPLETE!{Colors.RESET}")
    print(f"  ✅ Completed: {stats['completed']}/{stats['total']}")
    print(f"  ❌ Failed: {stats['failed']}")
    print(f"  ⏭️  Skipped: {stats['skipped']}")
    print(f"  🔧 Skills: {stats['skills_created']}")
    print(f"  🔄 Workflows: {stats['workflows_created']}")
    print(f"  🧠 Lessons: {len(lessons_learned)}")
    print(f"  ⏱️  Time: {mins}m {secs}s")
    print(f"  📁 Vault: {VAULT_ROOT}")
    print(f"  📋 Index: {INDEX_PATH}")
    
    # Final index write
    write_index(videos)
    
    if stats["failed"] > 0:
        print(f"\n{Colors.YELLOW}⚠️  {stats['failed']} videos failed. Check log: {LOG_PATH}{Colors.RESET}")

if __name__ == "__main__":
    asyncio.run(main())
