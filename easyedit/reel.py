"""One command from a topic to a finished reel in a creator's style.

    python -m easyedit.reel "Genghis Khan" --style nio_trade --template Dcc45l-PonG

The writer (Claude or Codex login, no API key) turns the topic into an original script in the creator's
shape: a hook line, a 60-80 word narration, a closing card, picture searches and footage searches. Then:
narration by neural TTS, pictures from Wikimedia Commons, footage from YouTube, a track picked by tempo
and drop, and the ordinary own-footage build (`python -m easyedit ... --footage ... --style ... --template`).

Everything lands in footage/reels/<slug>/ (script.json, narration.wav, images/, clips/) and jobs/<slug>/.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from .llm import ask_json
from .util import ROOT, die, log, read_json, slugify, write_json

REELS = ROOT / "footage" / "reels"
SYSTEM = "You write short-form video scripts for a creator. Reply with JSON only."
PROMPT = """Topic: {topic}

Write an original reel in this creator's shape: a hook line that stays on screen first (3-6 words, lower case,
like a caption someone typed), then a narration of 60-80 words in short punchy sentences (numbers and
contrasts, one idea per sentence, no lists), then a closing card.

Also list pictures to show (famous paintings, photographs of real places and objects, maps; things that exist
on Wikimedia Commons) as search phrases, and 2 YouTube searches for moving footage (film scenes, documentary
footage) about the topic.

Return JSON:
{{"title": "2-3 WORDS", "hook": "the hook line:", "script": "narration...", "cta": "TITLE|second line",
  "images": ["search phrase", ... 10 of them, in story order], "footage": ["youtube search", "youtube search"],
  "voice": "male" or "female"}}"""
UA = "easyedit-reel/1.0 (personal video project)"
VOICES = {"male": "en-US-ChristopherNeural", "female": "en-US-AriaNeural"}


def write_script(topic: str, provider: str) -> dict:
    reply = ask_json(PROMPT.format(topic=topic), SYSTEM, provider)
    if not isinstance(reply, dict) or not reply.get("script"):
        die("the writer did not answer (no Claude/Codex login, or rate limited); pass --script <file.json>")
    out = {"title": str(reply.get("title") or topic)[:40], "hook": str(reply.get("hook") or "")[:60],
           "script": str(reply["script"])[:1200], "cta": str(reply.get("cta") or reply.get("title") or topic)[:80],
           "images": [str(q)[:80] for q in (reply.get("images") or [])][:14],
           "footage": [str(q)[:80] for q in (reply.get("footage") or [])][:3],
           "voice": "female" if reply.get("voice") == "female" else "male"}
    log(f"script: {len(out['script'].split())} words, {len(out['images'])} pictures, {len(out['footage'])} footage searches")
    return out


def narrate(text: str, out: Path, voice: str = "male", rate: str = "+8%") -> Path:
    if out.exists():
        return out
    mp3 = out.with_suffix(".mp3")
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    args = ["--voice", VOICES.get(voice, VOICES["male"]), f"--rate={rate}", "--file", str(txt), "--write-media", str(mp3)]
    last = None
    for runner in ([sys.executable, "-m", "edge_tts"], ["edge-tts"], ["python", "-m", "edge_tts"], ["py", "-m", "edge_tts"]):
        try:
            subprocess.run([*runner, *args], check=True, timeout=300, capture_output=True)
            last = None
            break
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            last = e
    if last is not None or not mp3.exists():
        die("edge-tts is not installed for any Python here: pip install edge-tts")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "48000", "-ac", "1", str(out)], check=True)
    log(f"narration: {out.name}")
    return out


def _curl(url: str) -> bytes:
    return subprocess.run(["curl", "-sSL", "-m", "120", "-A", UA, url], capture_output=True, check=True).stdout


def _commons(params: dict) -> dict:
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    for attempt in range(4):
        raw = _curl(url)
        try:
            return json.loads(raw)
        except ValueError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("commons keeps answering with HTML (throttled)")


def fetch_images(queries: list[str], dest: Path, width: int = 2200) -> list[Path]:
    """Pictures from Wikimedia Commons for each search phrase, largest good hit, ~`width` px wide.
    Credits (file titles carry the author) go to dest/credits.txt."""
    dest.mkdir(parents=True, exist_ok=True)
    got, credits = [], []
    for i, q in enumerate(queries):
        stem = f"{i + 1:02d}-{slugify(q)[:30]}"
        have = next(dest.glob(stem + ".*"), None)
        if have:
            got.append(have)
            continue
        try:
            titles = []
            tries = [q, " ".join(q.split()[:3]), " ".join(q.split()[:2])]   # a long phrase, then its core
            for attempt in dict.fromkeys(t for t in tries if t):
                hits = _commons({"action": "query", "list": "search", "srnamespace": 6, "srlimit": 14,
                                 "srsearch": attempt}).get("query", {}).get("search", [])
                titles = [h["title"] for h in hits if h["title"].lower().endswith((".jpg", ".jpeg", ".png"))][:10]
                if titles:
                    break
            if not titles:
                log(f"images: nothing for {q!r}")
                continue
            info = _commons({"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
                             "iiprop": "url|size", "iiurlwidth": width})
            words = [w for w in slugify(q).split("-") if len(w) > 2]
            best = None
            for page in info.get("query", {}).get("pages", {}).values():
                ii = (page.get("imageinfo") or [{}])[0]
                if not ii.get("thumburl") or ii.get("width", 0) < 1000:
                    continue
                title = slugify(page["title"])
                hit = sum(1 for w in words if w in title)       # the file is about what was asked for
                if hit < min(2, len(words)):
                    continue
                aspect = ii["width"] / max(1, ii["height"])
                if not 0.45 <= aspect <= 2.4:                   # scrolls, strips and panoramas cut badly
                    continue
                score = hit * 1e7 + min(ii["width"] * ii["height"], 2e7)
                if best is None or score > best[0]:
                    best = (score, page["title"], ii["thumburl"])
            if not best:
                log(f"images: nothing large enough for {q!r}")
                continue
            ext = ".png" if best[2].lower().endswith(".png") else ".jpg"
            path = dest / (stem + ext)
            path.write_bytes(_curl(best[2]))
            got.append(path)
            credits.append(f"{path.name}: {best[1]}")
            log(f"images: {q!r} -> {best[1][:70]}")
        except Exception as e:
            log(f"images: {q!r} failed: {e}")
        time.sleep(3.0)
    if credits:
        with (dest / "credits.txt").open("a", encoding="utf-8") as f:
            f.write("\n".join(credits) + "\n")
    return got


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("topic")
    p.add_argument("--style", required=True, help="creator profile, e.g. nio_trade")
    p.add_argument("--template", help="one of the profile's reels to follow shot for shot")
    p.add_argument("--script", help="a script.json you wrote (skips the writer)")
    p.add_argument("--llm", default="auto", choices=["auto", "claude", "codex", "none"])
    p.add_argument("--music", help="your track (file or URL); otherwise one is picked to fit the profile")
    p.add_argument("--no-footage", action="store_true", help="pictures only, no YouTube footage")
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--draft", action="store_true")
    p.add_argument("--fresh", action="store_true", help="write the script and fetch everything again")
    a = p.parse_args(argv)

    slug = slugify(a.topic)
    base = REELS / slug
    base.mkdir(parents=True, exist_ok=True)
    script_file = base / "script.json"
    if a.script:
        script = read_json(Path(a.script))
    elif script_file.exists() and not a.fresh:
        script = read_json(script_file)
    else:
        script = write_script(a.topic, a.llm)
    write_json(script_file, script)
    log(f"hook: {script['hook']!r}  cta: {script['cta']!r}")

    narration = narrate(script["script"], base / "narration.wav", script.get("voice", "male"))
    images = fetch_images(script["images"], base / "images")
    if not images:
        die("no pictures could be fetched; add some to " + str(base / "images"))
    from . import cards
    clips_dir = base / "clips"
    clips_dir.mkdir(exist_ok=True)
    footage_clips = [cards.still_clip(img, clips_dir / f"{img.stem}.mp4", (1440, 1080), seconds=12.0, push=0.1)
                     for img in images]
    log(f"clips: {len(footage_clips)} picture clips")

    argv2 = [a.topic, "--footage", str(clips_dir)]
    if not a.no_footage:
        argv2 += [f"yt:{q}" for q in script.get("footage", [])]
    argv2 += ["--cards", *[str(i) for i in images], "--cta", script["cta"], "--hook", script["hook"],
              "--speech", str(narration), "--style", a.style, "--aspect", a.aspect, "--captions", "whole",
              "--llm", a.llm]
    if a.template:
        argv2 += ["--template", a.template]
    if a.music:
        argv2 += ["--music", a.music]
    if a.no_render:
        argv2.append("--no-render")
    if a.draft:
        argv2.append("--draft")
    if a.fresh:
        argv2.append("--fresh")
    log("build: python -m easyedit " + " ".join(f'"{x}"' if " " in x else x for x in argv2))
    from .__main__ import main as build
    build(argv2)


if __name__ == "__main__":
    main()
