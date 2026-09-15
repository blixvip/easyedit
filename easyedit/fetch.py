"""Resolve sources: local files pass through, URLs download, queries search YouTube."""
from __future__ import annotations

import re
from pathlib import Path

from .util import log, probe

VIDEO_FMT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"


def _ydl(opts: dict):
    try:
        import yt_dlp
    except ImportError:
        raise SystemExit("yt-dlp is required: pip install yt-dlp")
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "js_runtimes": {"node": {}}}
    return yt_dlp.YoutubeDL({**base, **opts})


def is_url(s: str) -> bool:
    return bool(re.match(r"https?://", s))


def search(query: str, n: int, min_dur: float, max_dur: float) -> list[dict]:
    with _ydl({"extract_flat": True}) as y:
        info = y.extract_info(f"ytsearch{n * 3}:{query}", download=False)
    hits = []
    for e in info.get("entries") or []:
        dur = e.get("duration") or 0
        if not (min_dur <= dur <= max_dur):
            continue
        if e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        hits.append({"url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                     "id": e["id"], "title": e.get("title", ""), "duration": dur})
    return hits[:n]


def download(url: str, dest: Path, audio_only: bool = False, cookies_browser: str | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "format": "ba[ext=m4a]/ba" if audio_only else VIDEO_FMT,
        "merge_output_format": None if audio_only else "mp4",
        "retries": 3,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    with _ydl(opts) as y:
        info = y.extract_info(url, download=False)
        existing = sorted(dest.glob(f"{info['id']}.*"))
        existing = [p for p in existing if p.suffix not in (".part", ".ytdl") and ".f" not in p.stem]
        if existing:
            return existing[0]
        log(f"download: {info.get('title', url)[:70]}")
        y.download([url])
    files = [p for p in dest.glob(f"{info['id']}.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise RuntimeError(f"download produced no file for {url}")
    return max(files, key=lambda p: p.stat().st_size)


def resolve(spec: str | None, query: str, dest: Path, *, n: int, min_dur: float, max_dur: float,
            audio_only: bool = False, cookies: str | None = None) -> list[Path]:
    """spec = local path | URL | None (search with query)."""
    if spec and Path(spec).exists():
        return [Path(spec).resolve()]
    if spec and is_url(spec):
        return [download(spec, dest, audio_only, cookies)]
    q = spec or query
    hits = search(q, n, min_dur, max_dur)
    if not hits:
        raise RuntimeError(f"no YouTube results between {min_dur:.0f}-{max_dur:.0f}s for: {q}")
    out = []
    for h in hits:
        try:
            p = download(h["url"], dest, audio_only, cookies)
            if audio_only or probe(p).get("width"):
                out.append(p)
        except Exception as e:
            log(f"download failed ({h['title'][:50]}): {e}")
    if not out:
        raise RuntimeError(f"every download failed for: {q}")
    return out
