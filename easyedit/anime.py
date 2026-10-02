"""Anime: what a title is (AniList), its openings and endings (AnimeThemes), and how its footage
is named where editors share it.

An edit linked to an anime keeps what was found in anime.json, next to plan.json. Nothing in the
cut depends on that file, so linking an anime to an edit that already exists never makes it stale.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import log, read_json, write_json

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) easyedit"
ANILIST = "https://graphql.anilist.co"
THEMES = "https://api.animethemes.moe"
# a film that only happens to share a word with some obscure short must not be taken for an anime
MIN_POPULARITY = 15000
_FIELDS = """id format seasonYear episodes popularity title{romaji english native} synonyms
  coverImage{large color} bannerImage studios(isMain:true){nodes{name}}
  characters(sort:[ROLE,FAVOURITES_DESC],perPage:12){edges{role node{name{full} image{medium}}}}"""
_seen: dict = {}


def _json(url: str, body: dict | None = None, timeout: float = 20):
    req = urllib.request.Request(url, json.dumps(body).encode() if body else None,
                                 {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _tidy(m: dict) -> dict:
    t = m["title"]
    return {"id": m["id"], "title": t.get("english") or t.get("romaji") or "", "romaji": t.get("romaji") or "",
            "native": t.get("native") or "", "synonyms": (m.get("synonyms") or [])[:8],
            "format": {"TV": "TV", "TV_SHORT": "TV short", "OVA": "OVA", "ONA": "ONA"}.get(m.get("format") or "", (m.get("format") or "").replace("_", " ").title()), "year": m.get("seasonYear"),
            "episodes": m.get("episodes"), "popularity": m.get("popularity") or 0,
            "cover": (m.get("coverImage") or {}).get("large"), "color": (m.get("coverImage") or {}).get("color"),
            "banner": m.get("bannerImage"),
            "studio": ", ".join(s["name"] for s in (m.get("studios") or {}).get("nodes", [])[:2]),
            "characters": [{"name": e["node"]["name"]["full"], "image": e["node"]["image"]["medium"],
                            "main": e.get("role") == "MAIN"}
                           for e in (m.get("characters") or {}).get("edges", []) if e["node"]["name"].get("full")]}


def lookup(query: str, n: int = 6) -> list[dict]:
    """Anime that match these words, best match first."""
    query = query.strip()[:120]
    if not query:
        return []
    key = ("lookup", query.lower(), n)
    if key not in _seen:
        gql = "query($s:String,$n:Int){Page(perPage:$n){media(search:$s,type:ANIME,sort:SEARCH_MATCH){%s}}}" % _FIELDS
        data = _json(ANILIST, {"query": gql, "variables": {"s": query, "n": n}})
        _seen[key] = [_tidy(m) for m in data["data"]["Page"]["media"]]
    return _seen[key]


def by_id(anilist_id: int) -> dict | None:
    gql = "query($i:Int){Media(id:$i,type:ANIME){%s}}" % _FIELDS
    m = _json(ANILIST, {"query": gql, "variables": {"i": int(anilist_id)}})["data"]["Media"]
    return _tidy(m) if m else None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def match(title: str) -> dict | None:
    """The anime a typed title means, or None when it is a film (or anything else)."""
    want = _norm(title)
    try:
        found = lookup(title)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        return None
    for m in sorted(found, key=lambda m: -m["popularity"]):
        names = [_norm(x) for x in (m["title"], m["romaji"], *m["synonyms"]) if x]
        exact = want in names
        # "demon slayer" for "Demon Slayer: Kimetsu no Yaiba" counts, but only for an anime well known enough
        # that the short name means it ("interstellar" must not become Interstella 5555)
        begins = any(len(want) > 5 and (n.startswith(want + " ") or want.startswith(n + " ")) for n in names)
        if (exact and m["popularity"] >= MIN_POPULARITY) or (begins and m["popularity"] >= 100000):
            return m
    return None


def of(job: Path) -> dict | None:
    f = job / "anime.json"
    return read_json(f) if f.exists() else None


def link(job: Path, info: dict | None) -> None:
    if info:
        write_json(job / "anime.json", info)
    else:
        (job / "anime.json").unlink(missing_ok=True)


def names(info: dict) -> str:
    """Every name uploads use for this anime, for telling its footage from something else's."""
    return " ".join(dict.fromkeys(x for x in (info["title"], info["romaji"], *info.get("synonyms", [])[:3]) if x))


def short(info: dict) -> str:
    """The name people actually type: 'Demon Slayer', not the full licensed title."""
    for name in (info["title"], info["romaji"]):
        cut = re.split(r"\s*[:\-–(]\s+|\s+-", name)[0].strip()
        if len(cut) >= 4:
            return cut
    return info["title"] or info["romaji"]


def footage_queries(info: dict, who: str = "") -> list[str]:
    """How editors label raw anime footage on YouTube: twixtor (pre-slowed) clips and scenepacks."""
    base = f"{who} {short(info)}".strip() if who else short(info)
    return [f"{base} twixtor 4K", f"{base} scenepack 4K", f"{base} raw clips 4K", f"{base} clips for edits"]


def sakuga_tags(info: dict) -> list[str]:
    """Sakugabooru files anime under its romaji name; a franchise also has a `_series` tag."""
    out = []
    for name in (info["romaji"], info["title"], *info.get("synonyms", [])[:2]):
        slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
        if slug:
            out += [slug, re.sub(r"_(season|part|cour)_?\d+.*$|_\d+(st|nd|rd|th)_season.*$|_\d+$", "", slug)]
    return list(dict.fromkeys(t for t in out if t))


def themes(info: dict) -> list[dict]:
    """Openings and endings with their best video: creditless and Blu-ray first, then the biggest picture."""
    key = ("themes", info["id"])
    if key in _seen:
        return _seen[key]
    data = _json(f"{THEMES}/anime?" + urllib.parse.urlencode({
        "filter[has]": "resources", "filter[site]": "AniList", "filter[external_id]": info["id"],
        "include": "animethemes.animethemeentries.videos.audio,animethemes.song.artists"}))
    out = []
    for a in data.get("anime", []):
        for t in a.get("animethemes", []):
            videos = [v for e in t.get("animethemeentries", []) for v in e.get("videos", []) if v.get("basename")]
            if not videos:
                continue
            v = max(videos, key=lambda v: (bool(v.get("nc")), v.get("resolution") or 0, v.get("source") == "BD"))
            song = t.get("song") or {}
            out.append({"slug": t.get("slug") or t.get("type") or "", "song": song.get("title") or "",
                        "artists": ", ".join(x["name"] for x in song.get("artists", [])[:3]),
                        "video": v["basename"], "audio": (v.get("audio") or {}).get("basename"),
                        "height": v.get("resolution"), "creditless": bool(v.get("nc")),
                        "size_mb": round((v.get("size") or 0) / 2**20)})
    _seen[key] = out
    return out


PLAN = """Plan a ~45 second fan edit (AMV) of the anime "{title}"{aka}.

Structure of the edit:
1. A captioned line: the anime's most iconic spoken moment in the ENGLISH DUB (10-24s of speech).
2. A fast montage of its best animation cut to music.

Return JSON:
{{
  "title": "{title}",
  "year": {year},
  "speech_scene": "one line naming the iconic speech and who says it",
  "speech_query": "YouTube search query that finds that exact scene in the English dub",
  "montage_queries": ["2-3 YouTube queries for raw footage of its best fights and visuals, the way editors label it: twixtor, scenepack, 4K"],
  "music_query": "YouTube search query for a song that fits an edit of this anime (phonk, a hard instrumental, or its own opening)",
  "palette": {{"positive": "#hex for words like victory/love", "negative": "#hex for pain/loss/danger", "gold": "#hex for power/emphasis", "cool": "#hex for places/objects/names"}},
  "grade": "anime"
}}
Choose bright, saturated palette colors that read on dark footage."""


def plan_prompt(info: dict) -> str:
    aka = f' (also known as "{info["romaji"]}")' if info["romaji"] and info["romaji"] != info["title"] else ""
    return PLAN.format(title=info["title"], aka=aka, year=info.get("year") or "null")


def plan_fallback(info: dict) -> dict:
    name, queries = short(info), footage_queries(info)
    return {"title": info["title"], "year": info.get("year"), "speech_scene": f"best speech in {name}",
            "speech_query": f"{name} best speech english dub", "montage_queries": queries[:2],
            "music_query": f"{name} opening full", "grade": "anime"}


def detect(job: Path, title: str, want: bool | None) -> dict | None:
    """Link a new edit to its anime. `want`: True forces the best match, False never links, None decides
    from the title."""
    known = of(job)
    if known or want is False:
        return known if want is not False else None
    info = match(title)
    if not info and want:
        try:
            found = lookup(title, 1)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            found = []
        info = found[0] if found else None
    if info:
        link(job, info)
        log(f"anime: {info['title']} ({info.get('year') or '?'}, {info['format']}, {info['studio'] or 'studio unknown'})")
    return info
