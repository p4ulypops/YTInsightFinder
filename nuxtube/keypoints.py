#!/usr/bin/env python3
"""LLM key-point extraction from video transcripts.

Calls `hermes -z` one-shot to extract structured key points from a transcript.
Outputs both human-readable markdown and machine-readable JSON.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


SYSTEM_PROMPT = """You are a key-point extraction engine. You read YouTube video transcripts and extract the most valuable, reusable lessons and insights.

You MUST respond with ONLY a valid JSON object (no markdown fences, no preamble). The JSON schema is:

{
  "summary": "One paragraph (2-4 sentences) summarising what the video covers.",
  "key_points": [
    {
      "id": 1,
      "timestamp": "M:SS or H:MM:SS or null if unknown",
      "category": "one of: coding | business | ai-agents | productivity | design | gamification | general",
      "title": "Short punchy title (max 80 chars)",
      "lesson": "The actual takeaway, 1-3 sentences. Be specific and actionable. Include tool names, technique names, exact steps.",
      "importance": "high | medium | low",
      "tags": ["tag1", "tag2"]
    }
  ]
}

Rules:
- Extract 5-15 key points (only the most valuable, not everything)
- Use importance sparingly: ~30% high, ~50% medium, ~20% low
- Tags should be lowercase, hyphenated, reusable across videos
- Timestamps must be in chronological order
- Do NOT hallucinate information not in the transcript
- When chapter structure and heatmap data are provided, treat them as strong signals:
  - Chapters = the creator's own segmentation (use chapter titles in your key point titles where relevant)
  - Heatmap peaks = sections viewers actually replayed most (bias toward extracting key points from these timestamps)
"""


def _build_player_context(folder_path) -> str:
    """Build a player data context string from metadata.json for the LLM prompt."""
    import json as _json
    meta_path = folder_path / "metadata.json"
    if not meta_path.exists():
        return ""

    try:
        meta = _json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""

    pd = meta.get("player_data", {})
    chapters = pd.get("chapters", [])
    heatmap = pd.get("heatmap", [])
    view_count = pd.get("view_count")
    lines = []

    if chapters:
        lines.append("\n## Chapter Structure (creator-defined segments):")
        for ch in chapters:
            ts = ch.get("start_time", 0) or 0
            m, s = int(ts) // 60, int(ts) % 60
            lines.append(f"  {m}:{s:02d}  {ch.get('title', '')}")

    if heatmap:
        # Top 12 peaks sorted by engagement value, then re-sorted chronologically for display
        peaks = sorted(heatmap, key=lambda x: x.get("heat_value", x.get("value", 0)), reverse=True)[:12]
        peaks = sorted(peaks, key=lambda x: x.get("start_millis", 0))
        lines.append("\n## Most-Replayed Moments (YouTube viewer heatmap — strongest engagement signals):")
        lines.append("  (These are timestamps viewers rewatched the most. Strongly prefer extracting key points from these windows.)")
        for h in peaks:
            ts_s = (h.get("start_millis") or 0) / 1000
            m, s = int(ts_s) // 60, int(ts_s) % 60
            v = h.get("heat_value", h.get("value", 0))
            lines.append(f"  {m}:{s:02d}  engagement score: {v:.3f}")

    if view_count:
        lines.append(f"\n  Total views: {view_count:,}")

    return "\n".join(lines) if lines else ""


def parse_json_response(text: str) -> Optional[dict]:
    """Robustly extract JSON from LLM response.

    Handles markdown fences, preamble text, and trailing content.
    """
    # Remove markdown fences (with or without language tag)
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")

    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the outermost JSON object using brace matching
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def validate_schema(data: dict) -> bool:
    """Validate that the LLM output has the expected structure."""
    if not isinstance(data, dict):
        return False
    if "summary" not in data or "key_points" not in data:
        return False
    if not isinstance(data["key_points"], list):
        return False
    for point in data["key_points"]:
        if not isinstance(point, dict):
            return False
        if "title" not in point or "lesson" not in point:
            return False
    return True


def call_llm(prompt: str, timeout: int = 180) -> Optional[str]:
    """Call hermes -z one-shot with the given prompt."""
    try:
        result = subprocess.run(
            ["hermes", "-z", "--cli", "--yolo", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None


def extract_keypoints(folder: str, config=None) -> bool:
    """Extract key points from a video's transcript.

    Reads transcript.md from the folder, sends to LLM,
    writes key-points.md and key-points.json.

    Returns True on success.
    """
    folder = Path(folder)
    transcript_path = folder / "transcript.md"
    if not transcript_path.exists():
        return False

    # Read transcript (truncate to 30000 chars for token budget)
    transcript = transcript_path.read_text(errors="replace")
    if len(transcript) > 30000:
        transcript = transcript[:30000] + "\n\n[... truncated ...]"

    if not transcript.strip():
        return False

    # Inject player data context (chapters + heatmap peaks) when available
    player_ctx = _build_player_context(folder)
    player_section = f"\n## Video Intelligence (use to bias key point selection):\n{player_ctx}\n" if player_ctx else ""

    prompt = f"""{SYSTEM_PROMPT}
{player_section}
---

Transcript:

{transcript}

---

Extract the key points now. Respond with ONLY the JSON object."""

    response = call_llm(prompt)
    if not response:
        return False

    data = parse_json_response(response)
    if not data or not validate_schema(data):
        return False

    # Deduplicate key point IDs (fixes the duplicate ID 10 bug)
    seen_ids = set()
    for i, point in enumerate(data["key_points"]):
        while point.get("id", i + 1) in seen_ids:
            point["id"] = point.get("id", 0) + 1
        seen_ids.add(point.get("id", i + 1))
        if "id" not in point:
            point["id"] = i + 1

    # Write JSON
    with open(folder / "key-points.json", "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Write Markdown
    md_lines = []
    title = folder.name.replace("-", " ").title()
    md_lines.append(f"# \U0001f511 Key Points: {title}\n")
    md_lines.append("---\n")
    md_lines.append("## \U0001f4dd Summary\n")
    md_lines.append(data.get("summary", "") + "\n")
    md_lines.append("---\n")
    md_lines.append("## \U0001f3af Key Lessons\n")

    importance_emoji = {"high": "\u2b50", "medium": "\u2705", "low": "\u2139\ufe0f"}
    cat_emoji = {
        "coding": "\U0001f4bb", "business": "\U0001f4b0", "ai-agents": "\U0001f916",
        "productivity": "\u26a1", "design": "\U0001f3a8", "gamification": "\U0001f3ae",
        "general": "\U0001f4cb",
    }

    for point in data["key_points"]:
        imp = point.get("importance", "medium")
        cat = point.get("category", "general")
        ts = point.get("timestamp", "")
        ts_str = f" `\u23f1 {ts}`" if ts else ""
        emoji = importance_emoji.get(imp, "\u2705")
        cat_e = cat_emoji.get(cat, "\U0001f4cb")

        md_lines.append(f"### {cat_e} {emoji} {point.get('title', 'Untitled')}{ts_str}\n")
        md_lines.append(f"**Category:** {cat} | **Importance:** {imp}\n")
        md_lines.append(f"{point.get('lesson', '')}\n")
        tags = point.get("tags", [])
        if tags:
            md_lines.append(f"\U0001f3f7\ufe0f {' '.join(f'`{t}`' for t in tags)}\n")
        md_lines.append("---\n")

    with open(folder / "key-points.md", "w") as f:
        f.write("\n".join(md_lines))

    return True
