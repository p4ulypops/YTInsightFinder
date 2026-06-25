#!/usr/bin/env python3
"""
extract_keypoints.py — Extract key lessons/points from a video transcript.

Reads transcript.md (or metadata.json) from a video folder, sends the transcript
to an LLM via `hermes -z` one-shot, and writes two new files:

  1. key-points.md   — Human-readable, rich-emoji markdown (scannable, fun)
  2. key-points.json — Machine-readable, structured JSON (for AI ingestion)

Usage:
  python3 _tools/extract_keypoints.py <video-folder>
  python3 _tools/extract_keypoints.py ai-agents/my-ai-team-now-has-an-interface-all-12-agents-free

Works from the youtube_videos/ base directory. Accepts absolute or relative paths.
"""
import json, os, subprocess, sys, re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # youtube_videos/
HERMES = "hermes"

SYSTEM_PROMPT = """You are a key-point extraction engine. You read YouTube video transcripts and extract the most valuable, reusable lessons and insights — especially anything related to business, coding, AI agents, productivity, or design.

You MUST respond with ONLY a valid JSON object (no markdown fences, no preamble). The JSON schema is:

{
  "summary": "One paragraph (2-4 sentences) summarising what the video covers.",
  "key_points": [
    {
      "id": 1,
      "timestamp": "M:SS or null if unknown",
      "category": "one of: coding | business | ai-agents | productivity | design | gamification | general",
      "title": "Short punchy title (max 80 chars)",
      "lesson": "The actual takeaway, 1-3 sentences. Be specific and actionable. Include tool names, technique names, exact steps. Avoid vague platitudes.",
      "tags": ["tag1", "tag2", "tag3"],
      "importance": "high | medium | low"
    }
  ]
}

Rules:
- Extract 5-15 key points depending on video density. Quality over quantity.
- Prioritise concrete, actionable lessons over vague observations.
- If the speaker names a specific tool, technique, config, or workflow — capture it.
- If there's a code snippet or command mentioned, include it in the lesson field.
- Timestamps: estimate from the transcript context if not exact.
- Tags: lowercase, hyphenated, 1-4 per point.
- Importance "high" = immediately actionable or fundamentally shifts understanding.
- Be honest: if the video is fluff with no real lessons, return fewer points and say so in the summary.
"""


def find_video_folder(path_arg):
    p = Path(path_arg)
    if p.is_absolute() and p.exists():
        return p
    # Try relative to BASE
    rel = BASE / p
    if rel.exists():
        return rel
    # Try as a category/video-slug
    for candidate in [BASE / path_arg, BASE / "ai-agents" / path_arg, BASE / "coding" / path_arg,
                      BASE / "productivity" / path_arg, BASE / "business" / path_arg,
                      BASE / "uncategorized" / path_arg]:
        if candidate.exists():
            return candidate
    sys.exit(f"Could not find video folder: {path_arg}")


def read_transcript(vdir):
    tpath = vdir / "transcript.md"
    if tpath.exists():
        return tpath.read_text()
    # Fallback: try metadata + raw transcript fetch
    mpath = vdir / "metadata.json"
    if mpath.exists():
        meta = json.loads(mpath.read_text())
        url = meta.get("url", "")
        if url:
            import subprocess
            r = subprocess.run(["python3",
                "/Users/user/.hermes/skills/media/youtube-content/scripts/fetch_transcript.py",
                url, "--text-only"], capture_output=True, text=True)
            return r.stdout
    sys.exit(f"No transcript found in {vdir}")


def extract_metadata(vdir):
    mpath = vdir / "metadata.json"
    if mpath.exists():
        return json.loads(mpath.read_text())
    return {}


def call_llm(prompt):
    """Call hermes -z for a one-shot LLM response."""
    result = subprocess.run(
        [HERMES, "-z", prompt, "--cli", "--yolo"],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        sys.exit(f"hermes -z failed: {result.stderr[:500]}")
    return result.stdout.strip()


def parse_json_response(text):
    """Robustly extract JSON from LLM output."""
    # Remove markdown fences if present
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    # Find the JSON object
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        sys.exit(f"Could not find JSON in LLM response:\n{text[:500]}")
    return json.loads(text[start:end+1])


EMOJI_MAP = {
    "coding": "💻",
    "business": "💰",
    "ai-agents": "🤖",
    "productivity": "⚡",
    "design": "🎨",
    "gamification": "🎮",
    "general": "📌",
}

IMPORTANCE_EMOJI = {
    "high": "🔥",
    "medium": "⭐",
    "low": "💡",
}


def write_human_md(vdir, meta, data):
    """Write key-points.md — human-readable, rich emoji markdown."""
    title = meta.get("title", vdir.name)
    channel = meta.get("channel", "unknown")
    url = meta.get("url", "")
    category = meta.get("category", "uncategorized")

    lines = [
        f"# 🔑 Key Points: {title}",
        "",
        f"> 📺 **Channel:** {channel}  ",
        f"> 🔗 **Video:** [{url}]({url})  ",
        f"> 📂 **Category:** {category}",
        "",
        "---",
        "",
        f"## 📝 Summary",
        "",
        data.get("summary", ""),
        "",
        "---",
        "",
        "## 🎯 Key Lessons",
        "",
    ]

    for kp in data.get("key_points", []):
        cat = kp.get("category", "general")
        imp = kp.get("importance", "medium")
        emoji = EMOJI_MAP.get(cat, "📌")
        imp_emoji = IMPORTANCE_EMOJI.get(imp, "⭐")
        ts = kp.get("timestamp")
        ts_str = f" `⏱ {ts}`" if ts else ""

        lines.append(f"### {emoji} {imp_emoji} {kp.get('title', 'Untitled')}{ts_str}")
        lines.append("")
        lines.append(f"**Category:** {cat} | **Importance:** {imp}")
        lines.append("")

        lesson = kp.get("lesson", "")
        # Preserve any code blocks in the lesson
        lines.append(lesson)
        lines.append("")

        tags = kp.get("tags", [])
        if tags:
            lines.append(f"🏷️ `{'` `'.join(tags)}`")
            lines.append("")

        lines.append("---")
        lines.append("")

    md_path = vdir / "key-points.md"
    md_path.write_text("\n".join(lines))
    return md_path


def write_machine_json(vdir, meta, data):
    """Write key-points.json — structured, AI-optimised."""
    output = {
        "video": {
            "title": meta.get("title", vdir.name),
            "video_id": meta.get("video_id", ""),
            "url": meta.get("url", ""),
            "channel": meta.get("channel", ""),
            "category": meta.get("category", "uncategorized"),
        },
        "summary": data.get("summary", ""),
        "key_points": [],
        "metadata": {
            "format_version": "1.0",
            "optimised_for": "machine_ingestion",
            "extraction_method": "llm_extracted",
        },
    }

    for kp in data.get("key_points", []):
        output["key_points"].append({
            "id": kp.get("id", 0),
            "timestamp": kp.get("timestamp"),
            "category": kp.get("category", "general"),
            "title": kp.get("title", ""),
            "lesson": kp.get("lesson", ""),
            "tags": kp.get("tags", []),
            "importance": kp.get("importance", "medium"),
        })

    json_path = vdir / "key-points.json"
    json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    return json_path


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 _tools/extract_keypoints.py <video-folder>")

    vdir = find_video_folder(sys.argv[1])
    meta = extract_metadata(vdir)
    transcript = read_transcript(vdir)

    # Truncate very long transcripts to fit context
    if len(transcript) > 30000:
        transcript = transcript[:30000] + "\n...[truncated]"

    title = meta.get("title", vdir.name)
    print(f"Extracting key points: {title}")

    prompt = f"{SYSTEM_PROMPT}\n\n--- VIDEO TRANSCRIPT ---\nTitle: {title}\n\n{transcript}"

    response = call_llm(prompt)
    data = parse_json_response(response)

    md_path = write_human_md(vdir, meta, data)
    json_path = write_machine_json(vdir, meta, data)

    kp_count = len(data.get("key_points", []))
    print(f"Done: {kp_count} key points extracted")
    print(f"  Human:    {md_path}")
    print(f"  Machine:  {json_path}")


if __name__ == "__main__":
    main()
