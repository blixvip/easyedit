"""Movie title -> search plan (which scene to caption, what to montage, what song)."""
from __future__ import annotations

from .llm import ask_json
from .util import log

SYSTEM = (
    "You plan short cinematic fan edits of films for social media. "
    "Reply with a single JSON object and nothing else."
)

PROMPT = """Plan a ~45 second fan edit of the film "{title}".

Structure of the edit:
1. A captioned monologue: the film's most iconic, quotable spoken moment (10-24s of speech).
2. A fast montage of the film's best shots cut to music.

Return JSON:
{{
  "title": "canonical film title",
  "year": 2013,
  "speech_scene": "one line naming the iconic speech/monologue and who says it",
  "speech_query": "YouTube search query that finds a clip of exactly that scene",
  "montage_queries": ["2-3 YouTube queries for high quality clips/scene compilations of the film's most visual moments"],
  "music_query": "YouTube search query for a song or instrumental that fits the edit's mood (the film's own score is fine)",
  "palette": {{"positive": "#hex for words like wealth/victory/love", "negative": "#hex for pain/loss/danger", "gold": "#hex for luxury/power/emphasis", "cool": "#hex for places/objects/names"}},
  "grade": "one of: warm, cool, teal-orange, neutral, noir"
}}
Choose bright, saturated palette colors that read on dark footage."""

DEFAULT_PALETTE = {"positive": "#8dff8a", "negative": "#ff3b57", "gold": "#ffe27a", "cool": "#9ef3ff"}


def make_plan(title: str, provider: str) -> dict:
    fallback = {
        "title": title,
        "year": None,
        "speech_scene": f"best speech in {title}",
        "speech_query": f"{title} best speech scene",
        "montage_queries": [f"{title} best scenes", f"{title} trailer 4k"],
        "music_query": f"{title} soundtrack main theme",
        "palette": DEFAULT_PALETTE,
        "grade": "neutral",
        "source": "fallback",
    }
    reply = ask_json(PROMPT.format(title=title), SYSTEM, provider)
    if not isinstance(reply, dict) or not reply.get("speech_query"):
        log("plan: using fallback search queries")
        return fallback
    plan = {**fallback, **{k: v for k, v in reply.items() if v}, "source": "llm"}
    plan["palette"] = {**DEFAULT_PALETTE, **(reply.get("palette") or {})}
    if not isinstance(plan["montage_queries"], list) or not plan["montage_queries"]:
        plan["montage_queries"] = fallback["montage_queries"]
    log(f"plan: {plan['speech_scene']}")
    return plan
