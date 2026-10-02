"""Resolve sources: local files pass through, URLs download, queries search YouTube."""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

from .util import log, probe

# YouTube's 1080p H.264 stream is starved of bitrate. The VP9 streams, and anything above 1080p,
# carry several times more picture; scaled down to 1080p they are far sharper.
MAX_HEIGHT = int(os.environ.get("EASYEDIT_MAX_HEIGHT", "2160"))
VIDEO_FMT = f"bv*[height<={MAX_HEIGHT}]+ba[ext=m4a]/bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/b"
# ordinary (SDR) colour first, since HDR needs tone mapping to look right; then the biggest picture;
# at the same size VP9 beats H.264, then whichever has more bitrate
VIDEO_SORT = ["hdr:sdr", "res", "vcodec:vp9", "br"]

# what an upload's title says about the footage inside it
GOOD = {"montage": [(r"\b(4k|2160p|uhd)\b", 2.0), (r"scene ?pack|scenepack|visuals|amazing shots|cinematography|every shot|best shots|shots of", 2.5),
                    (r"\b(hd|1080p|blu-?ray|remaster)", 0.6), (r"no (copyright|watermark)|clean|raw", 0.6)],
        "speech": [(r"\b(4k|2160p|uhd)\b", 1.5), (r"\b(hd|1080p|blu-?ray)\b", 0.8), (r"\bscene\b|\bclip\b", 0.6)]}
# anime is shared differently: "twixtor" (pre-slowed) clips and scenepacks are the raw footage editors want
GOOD["anime"] = [(r"\b(4k|2160p|uhd)\b", 2.0), (r"twixtor|scene ?pack|scenepack|raw (clips|scenes)|clips for edit", 2.5),
                 (r"\b(hd|1080p|blu-?ray|bd)\b", 0.6), (r"no (cc|copyright|watermark)|with cc|free to use|clean", 0.5)]
BAD_ANIME = [(r"reaction|reacts|review|explained|explanation|breakdown|analysis|essay|theory|recap|"
              r"things you|facts|interview|podcast|commentary|parody|abridged|ranked|tutorial|how to", 5.0),
             (r"\b(amv|edit|mmv)\b(?! ?(pack|clips))|status|whatsapp|#shorts|tiktok|lyrics|slowed|sped up", 2.5),
             (r"(?<!no )\b(eng(lish)? )?sub(bed|s)?\b|subtitulad|legendado|vietsub|hindi", 1.5), (r"\bcam\b|\bleak", 3.0)]
BAD = [(r"reaction|reacts|review|explained|explanation|breakdown|analysis|essay|theory|recap|ending|things you|facts|"
        r"behind the scenes|making of|interview|podcast|commentary|honest trailer|parody|pitch meeting|ranked", 5.0),
       (r"\bedit\b|\bamv\b|\bfmv\b|status|whatsapp|#shorts|tiktok|velocity|twixtor|slowed|sped up|lyrics", 2.5),
       (r"sub(title)?s?\b|subtitulad|legendado|vietsub|espa[nñ]ol|latino|hindi|dubbed|dublado|doblaje", 2.5),
       (r"upscaled?|ai enhanced|60 ?fps|interpolat", 1.0), (r"\bcam\b|\bleak", 3.0)]


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
        **({} if audio_only else {"format_sort": VIDEO_SORT}),
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


def _title_score(title: str, kind: str, movie: str, anime: bool = False) -> float:
    t = title.lower()
    good, bad = (GOOD["anime"] if kind == "montage" else GOOD[kind], BAD_ANIME) if anime else (GOOD[kind], BAD)
    score = sum(w for pat, w in good if re.search(pat, t)) - sum(w for pat, w in bad if re.search(pat, t))
    if kind == "montage" and re.search(r"trailer|teaser|tv spot", t):
        score -= 1.2   # trailers are full of title cards and logos; real scenes cut better
    if kind == "speech" and not any(re.search(pat, t) for pat, _ in GOOD["speech"]):
        score -= 1.5   # an upload that does not call itself a scene or clip is usually someone's own video
    words = [w for w in re.findall(r"[a-z0-9]+", movie.lower()) if len(w) > 2 and w not in ("the", "and")]
    if words and not any(w in t for w in words):
        score -= 6.0   # not this film at all
    return score


def _quality(url: str, cookies: str | None) -> tuple[int, float]:
    """The best picture an upload offers: (height, bitrate in kbps), without downloading it."""
    opts = {"cookiesfrombrowser": (cookies,)} if cookies else {}
    with _ydl(opts) as y:
        info = y.extract_info(url, download=False)
    best = (0, 0.0)
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none") or not f.get("height") or f["height"] > MAX_HEIGHT:
            continue
        best = max(best, (int(f["height"]), float(f.get("tbr") or 0)))
    return best


def best_sources(queries: list[str], dest: Path, *, kind: str, movie: str, n: int, min_dur: float, max_dur: float,
                 cookies: str | None = None, anime: bool = False) -> list[Path]:
    """Search several ways, then keep the uploads with the best real picture.

    A title can say 4K and be a blurry re-upload, so the top candidates are checked for the
    resolution and bitrate YouTube actually has before anything is downloaded."""
    found: dict[str, dict] = {}
    for q in queries:
        if is_url(q):
            found.setdefault(q, {"url": q, "id": q, "title": movie, "duration": 0, "given": True})
            continue
        try:
            for h in search(q, 8, min_dur, max_dur):
                found.setdefault(h["id"], h)
        except Exception as e:
            log(f"search failed ({q}): {e}")
    for c in found.values():
        c["score"] = _title_score(c["title"], kind, movie, anime) + (0.5 if c.get("given") else 0)
        if kind == "speech" and c.get("duration", 0) > 200:
            c["score"] -= 1.0   # the speech should come from the scene itself, not a long compilation
    ranked = sorted(found.values(), key=lambda c: -c["score"])[:7]
    for c in ranked:
        try:
            height, kbps = _quality(c["url"], cookies)
        except Exception as e:
            c["score"] -= 10
            log(f"skipped ({c['title'][:40]}): {str(e)[:80]}")
            continue
        c["height"], c["kbps"] = height, kbps
        c["score"] += 2.2 * math.log2(max(height, 240) / 720) + min(kbps, 12000) / 6000
    out = []
    for c in sorted(ranked, key=lambda c: -c["score"]):
        if len(out) >= n or c["score"] < -4:
            break
        try:
            p = download(c["url"], dest, False, cookies)
        except Exception as e:
            log(f"download failed ({c['title'][:50]}): {e}")
            continue
        if probe(p).get("width"):
            log(f"{kind} source: {c.get('height', '?')}p {c.get('kbps', 0) / 1000:.1f} Mbps  {c['title'][:60]}")
            out.append(p)
    if not out:
        raise RuntimeError(f"no usable {kind} footage found for {movie}")
    return out


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
