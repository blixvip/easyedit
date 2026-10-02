"""Footage finder: type a film, a show, an anime or a character and get sharp footage of it, in a
random order.

What was typed is first worked out (Wikipedia for films, shows and their characters, AniList for
anime and anime characters). Then the places editors share raw footage are asked for it, and random
moments are dealt from what came back. Nothing is downloaded until a clip is saved; saved clips
land in footage/<subject>/ as H.264 MP4, which every editing program opens.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import anime, clips, fetch
from .util import ROOT, probe as ffprobe, read_json, slugify, write_json

LIBRARY = ROOT / "footage"
KINDS = ("movie", "tv", "anime", "character")
LENGTHS = (6, 12, 30)
PER_VIDEO = 4            # moments dealt from one upload before the page asks again
POOL_AGE = 15 * 60       # a subject's uploads are remembered this long, so a reshuffle is instant
PROBE_AGE = 90 * 60      # YouTube's storyboard links stop working after a few hours
MIN_FREE = 2 * 2**30
WIKI = "https://en.wikipedia.org/w/api.php"
UA = "easyedit/1.0 (https://github.com/blixvip/easyedit)"
_YT = re.compile(r"[\w-]{11}")
_GENERIC = re.compile(r"comics?|character|fictional|video game|television|literary|cartoon|anime|manga|film|book|novel")
_NOT_A_TITLE = re.compile(r"soundtrack|album|\bsong\b|\bactor\b|actress|director|franchise|company|studio|"
                          r"\bnovel\b|\bbook\b|episode|playing card|topics referred|\bband\b|\bborn\b")

_pools: dict = {}
_probes: dict = {}
_saves: dict = {}
_lock = threading.Lock()
_probing = threading.BoundedSemaphore(4)
_saving = threading.BoundedSemaphore(1)   # one download and encode at a time: a 4K encode is heavy


# ---------- what was typed ----------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _fit(q: str, names: list[str]) -> int:
    """How well typed words name something: 2 is its name, 1 is part of its name, 0 is neither."""
    want = _norm(q)
    names = [_norm(n) for n in names if n]
    if not want:
        return 0
    if want in names:
        return 2
    words = want.split()
    return 1 if any(all(w in n.split() for w in words) for n in names) else 0


def _get(url: str) -> bytes:
    """Wikipedia asks apps to say who they are; one that does not is turned away when it asks often."""
    with clips._open(urllib.request.Request(url, headers={"User-Agent": UA}), 12) as r:
        return r.read()


def _wiki_kind(title: str, desc: str) -> str | None:
    d = desc.lower()
    if not d or title.startswith("List of") or re.search(r"\bseason \d+$", title):
        return None
    named = re.search(r"\((?:\d{4} )?(?:\w+ )?(tv series|miniseries|film|character)\)$", title.lower())
    if named:   # the page says what it is in its own name: "Fargo (TV series)"
        return {"film": "movie", "character": "character"}.get(named.group(1), "tv")
    if re.search(r"\b(character|protagonist|antagonist|supervillain|superhero)\b", d):
        return "character"
    if _NOT_A_TITLE.search(d):
        return None
    if re.search(r"miniseries|sitcom|\b(tv|television) (show|drama|program(me)?)\b", d) or (
            re.search(r"\bseries\b", d) and not re.search(r"manga|comic|\bgame|film series|light novel", d)):
        return "tv"
    if re.search(r"\bfilm\b", d):
        return "movie"
    return None


def _wiki_of(title: str, desc: str) -> str:
    """The film or show a character is from, when the page says so: 'Breaking Bad character'."""
    said = re.search(r"(?:character|protagonist|antagonist|villain|hero)s? (?:from|in|of) (?:the )?"
                     r"(?:(?:\w+ )?(?:television|tv|anime|manga|film|animated|book|comic) (?:series|franchise) )?(.+)$",
                     desc, re.I) or re.fullmatch(r"(.+?) character", desc, re.I)
    of = re.sub(r"^franchise | franchise$", "", said.group(1).strip()) if said else ""
    if not of and (m := re.search(r"\(([^)]+)\)$", title)):
        of = m.group(1)
    return "" if not of or _GENERIC.search(of.lower()) or len(of) > 60 else of


def _wiki(q: str, kind: str = "") -> list[dict]:
    """Films, shows and characters by that name, in the order Wikipedia's own search ranks them."""
    hint = {"movie": " film", "tv": " TV series", "character": " character"}.get(kind, "")
    url = WIKI + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": q + hint, "gsrlimit": 10,
        "prop": "pageimages|description", "piprop": "thumbnail", "pithumbsize": 300, "pilicense": "any"})
    data = json.loads(_get(url))
    out = []
    for p in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99)):
        desc = p.get("description") or ""
        kind = _wiki_kind(p["title"], desc)
        if not kind:
            continue
        name = re.sub(r"\s*\([^)]*\)$", "", p["title"])
        year = re.search(r"\b(19|20)\d{2}\b", f"{desc} {p['title']}")
        out.append({"kind": kind, "title": name, "sub": desc[:160], "year": int(year.group()) if year else None,
                    "image": (p.get("thumbnail") or {}).get("source"), "names": [],
                    "of": _wiki_of(p["title"], desc) if kind == "character" else "", "anime": None,
                    "order": len(out)})
    return out


def _characters(q: str) -> list[dict]:
    """Anime characters by that name, with the anime each is best known from."""
    gql = ("query($s:String){Page(perPage:6){characters(search:$s,sort:[SEARCH_MATCH]){favourites "
           "name{full alternative} image{medium} media(sort:POPULARITY_DESC,perPage:1,type:ANIME)"
           "{nodes{id title{romaji english}}}}}}")
    data = anime._json(anime.ANILIST, {"query": gql, "variables": {"s": q}}, timeout=12)
    out = []
    for c in data["data"]["Page"]["characters"]:
        show = next(iter(c["media"]["nodes"]), None)
        if not show or not c["name"].get("full"):
            continue
        of = show["title"].get("english") or show["title"].get("romaji") or ""
        out.append({"kind": "character", "title": c["name"]["full"], "sub": f"Character in {of}", "year": None,
                    "image": (c.get("image") or {}).get("medium"), "of": of, "anime": show["id"],
                    "names": [n for n in (c["name"].get("alternative") or []) if n][:6],
                    "favourites": c.get("favourites") or 0})
    return out


def _clean(raw) -> dict:
    """A subject the page sent back: only what the search itself would have made."""
    if not isinstance(raw, dict) or raw.get("kind") not in (*KINDS, "text") or not str(raw.get("title") or "").strip():
        raise ValueError("pick something to find footage of")
    image = str(raw.get("image") or "")
    return {"kind": raw["kind"], "title": str(raw["title"]).strip()[:120], "sub": str(raw.get("sub") or "")[:160],
            "year": raw["year"] if isinstance(raw.get("year"), int) else None,
            "image": image if image.startswith("https://") else None, "of": str(raw.get("of") or "")[:120],
            "anime": raw["anime"] if isinstance(raw.get("anime"), int) else None,
            "names": [str(n)[:60] for n in (raw.get("names") or [])[:6]] if isinstance(raw.get("names"), list) else []}


def subjects(q: str, kind: str = "") -> list[dict]:
    """Everything the typed words could mean, the likeliest first. `kind` narrows it to one sort."""
    q = q.strip()[:120]
    if not q:
        raise ValueError("type a film, a show, an anime or a character")
    kind = kind if kind in KINDS else ""

    def ask(find, *args):
        try:
            return find(*args)
        except Exception:   # a lookup that is down only means fewer suggestions
            return []
    with ThreadPoolExecutor(3) as pool:
        wiki = pool.submit(ask, _wiki, q, kind) if kind != "anime" else None
        shows = pool.submit(ask, anime.lookup, q, 5) if kind in ("", "anime") else None
        chars = pool.submit(ask, _characters, q) if kind in ("", "character") else None
    found = []
    taken = set()
    for i, m in enumerate(shows.result() if shows else []):
        names = [m["title"], m["romaji"], *m["synonyms"]]
        fit, want = _fit(q, names), _norm(q)
        begins = any(len(want) > 5 and _norm(n).startswith(want + " ") for n in names if n)
        sure = (fit == 2 and m["popularity"] >= anime.MIN_POPULARITY) or (begins and m["popularity"] >= 100000)
        rank = 0 if sure else 3 if (fit or begins) and m["popularity"] >= anime.MIN_POPULARITY else 9
        if rank < 9:
            taken |= {_norm(n) for n in names if n}
        found.append((rank + i * 0.01, {
            "kind": "anime", "title": m["title"] or m["romaji"], "year": m["year"], "image": m["cover"], "of": "",
            "sub": " · ".join(str(x) for x in ("Anime", m["format"], m["year"], m["studio"]) if x),
            "anime": m["id"], "names": []}))
    for c in chars.result() if chars else []:
        fit = _fit(q, [c["title"], *c["names"], " ".join(reversed(c["title"].split()))])
        # a character thousands of people have marked as a favourite is the one meant by its name
        rank = 0.5 if fit and c["favourites"] >= 5000 else 2 if fit and c["favourites"] >= 1000 else 5 if fit else 9
        found.append((rank - min(c.pop("favourites"), 99999) / 1e6, c))
    for w in wiki.result() if wiki else []:
        i = w.pop("order")
        if kind and w["kind"] != kind:
            continue
        if w["kind"] != "character" and _norm(w["title"]) in taken:
            continue   # the anime itself, which AniList knows better
        if w["kind"] == "character" and any(s["kind"] == "character" and s["anime"] and _norm(w["of"]) and
                                            set(_norm(w["title"]).split()) & set(_norm(s["title"]).split())
                                            and _fit(w["of"], [s["of"]]) for _, s in found):
            continue   # the same character, already found with its anime
        fit = _fit(q, [w["title"]])
        found.append(((1 if i < 3 else 4) + i * 0.01 if fit else 9 + i * 0.01, w))
    found = [(r, s) for r, s in found if not kind or s["kind"] == kind]
    found.sort(key=lambda f: f[0])
    out = [s for r, s in found if r < 9 or kind][:8]
    if not out or (found and found[0][0] >= 9 and kind):
        # nothing by that name: the words themselves are searched for
        out.insert(0, {"kind": kind if kind in ("movie", "tv", "character") else "text", "title": q,
                       "sub": "Searched as typed", "year": None, "image": None, "of": "", "anime": None, "names": []})
    return out


# ---------- where the footage is ----------
def _asks(s: dict, ani: dict | None) -> tuple[list[str], str]:
    """How to ask YouTube for raw footage of this, and the words a title must share to be about it."""
    t = s["title"]
    if s["kind"] == "anime" and ani:
        return anime.footage_queries(ani)[:3], anime.names(ani)
    if s["kind"] == "character":
        who = " ".join([t, *s["names"]])
        if ani:
            return anime.footage_queries(ani, t)[:3], who
        of = f" {s['of']}" if s["of"] else ""
        return [f"{t}{of} scenepack 4K", f"{t}{of} 4K scenes", f"{t} clips for edits 4K"], who
    if s["kind"] == "movie":
        return [f"{t} scenepack 4K", f"{t} 4K scene", f"{t} {s['year'] or ''} visuals 4K".replace("  ", " ")], t
    if s["kind"] == "tv":
        return [f"{t} scenepack 4K", f"{t} 4K scene", f"{t} best scenes 4K"], t
    return [f"{t} scenepack 4K", f"{t} 4K scene", f"{t} clips for edits 4K"], t


def _youtube(asks: list[str], known: str, as_anime: bool) -> list[dict]:
    def one(ask):
        try:
            return fetch.search(ask, 14, 20, 3600)
        except Exception:
            return []
    with ThreadPoolExecutor(len(asks)) as pool:
        found = {h["id"]: h for hits in pool.map(one, asks) for h in hits}
    out = []
    for h in found.values():
        # reactions, reviews, other people's edits and uploads about something else are left out
        if fetch._title_score(h["title"], "montage", known, anime=as_anime) <= -2 or not _YT.fullmatch(h["id"]):
            continue
        out.append({"site": "youtube", "id": h["id"], "kind": "video", "title": h["title"], "sub": "YouTube",
                    "duration": h["duration"], "thumb": f"https://i.ytimg.com/vi/{h['id']}/mqdefault.jpg",
                    "page": f"https://www.youtube.com/watch?v={h['id']}"})
    return out


def _sakuga(ani: dict) -> list[dict]:
    """Animation cuts of this anime, in an order Sakugabooru itself shuffles."""
    tag, posts = clips.sakuga_posts(anime.sakuga_tags(ani), "random", 60)
    out = []
    for p in posts:
        if p.get("file_ext") not in ("mp4", "webm") or not p.get("file_url") or (p.get("height") or 0) < 720:
            continue
        names = [t for t in p.get("tags", "").split() if t not in ("animated", tag)]
        out.append({"site": "sakuga", "id": str(p["id"]), "kind": "video", "sub": "Sakugabooru",
                    "title": ", ".join(t.replace("_", " ") for t in names[:4]) or tag.replace("_", " "),
                    "thumb": p.get("preview_url"), "preview": p["file_url"], "width": p.get("width"),
                    "height": p.get("height"), "page": f"https://www.sakugabooru.com/post/show/{p['id']}"})
    return out


def _themes(ani: dict) -> list[dict]:
    return [{"site": "themes", "id": t["video"], "kind": "video", "sub": "AnimeThemes", "label": t["slug"],
             "title": " · ".join(x for x in (t["slug"], t["song"], "creditless" if t["creditless"] else "") if x),
             "thumb": None, "preview": f"https://v.animethemes.moe/{t['video']}", "height": t["height"],
             "page": f"https://v.animethemes.moe/{t['video']}"} for t in anime.themes(ani) if (t["height"] or 0) >= 720]


def _stills(title: str) -> list[dict]:
    return [{**r, "sub": "Film-Grab"} for r in clips._filmgrab(title, title, None)]


def gather(s: dict) -> dict:
    """Every upload, cut and still found for a subject. Remembered for a while: dealing again is free."""
    key = (s["kind"], _norm(s["title"]), _norm(s["of"]), s["anime"])
    with _lock:
        kept = _pools.get(key)
    if kept and time.time() - kept["at"] < POOL_AGE:
        return kept
    ani = None
    try:
        if s["anime"]:
            ani = anime.by_id(s["anime"])
        elif s["kind"] == "character" and s["of"]:
            ani = anime.match(s["of"])
    except Exception:
        ani = None
    asks, known = _asks(s, ani)
    jobs = {"youtube": (_youtube, asks, known, bool(ani))}
    if ani and s["kind"] == "anime":
        jobs |= {"sakuga": (_sakuga, ani), "themes": (_themes, ani)}
    if s["kind"] == "movie":
        jobs["filmgrab"] = (_stills, s["title"])
    sources, failed = [], []
    with ThreadPoolExecutor(len(jobs)) as pool:
        for site, job in {site: pool.submit(*job) for site, job in jobs.items()}.items():
            try:
                sources += job.result()
            except Exception:   # one site being down must not empty the page
                failed.append(clips._NAMES.get(site, site))
    kept = {"at": time.time(), "sources": sources, "asked": asks, "failed": failed}
    if sources:
        with _lock:
            _pools[key] = kept
    return kept


def moments(sources: list[dict], n: int, length: float, rng: random.Random) -> list[dict]:
    """Deal `n` clips: a random moment from each upload before a second from any, with animation
    cuts, openings and stills mixed in among the first."""
    first = []
    for site, most in (("sakuga", n // 3), ("themes", 4), ("filmgrab", n // 5)):
        have = [s for s in sources if s["site"] == site]
        first += [{**s, "key": f"{site}:{s['id']}"} for s in rng.sample(have, min(most, len(have)))]
    plans = []
    for s in (s for s in sources if s["site"] == "youtube"):
        dur = float(s["duration"])
        long = dur >= 90   # a long upload opens on a channel intro and closes on an end screen
        lo = max(4.0, dur * 0.05) if long else 0.0
        hi = dur - (max(6.0, dur * 0.06) if long else 0.0)
        slots = list(range(max(1, int((hi - lo) // length))))
        rng.shuffle(slots)
        plans.append((s, lo, min(length, dur), slots[:PER_VIDEO]))
    out = []
    for turn in range(PER_VIDEO):
        row = [{**s, "start": round(lo + slots[turn] * length, 1), "length": round(span, 1),
                "key": f"youtube:{s['id']}:{int(lo + slots[turn] * length)}"}
               for s, lo, span, slots in plans if turn < len(slots)]
        if turn == 0:
            row += first
        rng.shuffle(row)
        out += row
    return out[:n]


def search(q: str = "", kind: str = "", pick=None, seed: int | None = None, length: int = 12, n: int = 60) -> dict:
    found = [_clean(pick)] if pick else subjects(q, kind)
    pool = gather(found[0])
    length = length if length in LENGTHS else 12
    dealt = moments(pool["sources"], max(1, min(n, 120)), length, random.Random(seed))
    return {"subject": found[0], "subjects": found, "clips": dealt, "uploads": len(pool["sources"]),
            "asked": pool["asked"], "failed": pool["failed"]}


# ---------- how sharp an upload really is ----------
def probe(vid: str) -> dict:
    """What YouTube actually has for an upload (a title can say 4K and be a blurry re-upload), plus
    its storyboard: the sheet of small frames the page shows each moment from."""
    if not _YT.fullmatch(vid or ""):
        raise ValueError("unknown YouTube video")
    kept = _probes.get(vid)
    if kept and time.time() - kept["at"] < PROBE_AGE:
        return kept
    try:
        with _probing, fetch._ydl({}) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    except Exception as e:
        out = {"at": time.time(), "error": re.sub(r"^ERROR: (\[\w+\] )?([\w-]{11}: )?", "", str(e))[:160]}
        _probes[vid] = out
        return out
    best, board = None, None
    for f in info.get("formats") or []:
        if f.get("format_note") == "storyboard":
            if f.get("fragments") and (not board or (f.get("width") or 0) > board["width"]):
                board = f
        elif f.get("vcodec") not in (None, "none") and f.get("height") and f["height"] <= fetch.MAX_HEIGHT:
            # a wide film in 4K is 3840x1600: its size is judged by the frame it would fill
            tier = max(int(f["height"]), round(int(f.get("width") or 0) * 9 / 16))
            hdr = (f.get("dynamic_range") or "SDR") != "SDR"   # a save takes ordinary colour first, so that is what is judged
            if not best or (not hdr, tier, f.get("tbr") or 0) > (not best["hdr"], best["tier"], best["kbps"]):
                best = {"tier": tier, "width": f.get("width"), "height": f["height"], "kbps": round(f.get("tbr") or 0),
                        "fps": round(f.get("fps") or 0), "hdr": hdr}
    out = {"at": time.time(), **(best or {"tier": 0}), "embed": info.get("playable_in_embed") is not False,
           "channel": info.get("channel") or "", "duration": info.get("duration")}
    if board:
        out["board"] = {"w": board["width"], "h": board["height"], "cols": board["columns"], "rows": board["rows"],
                        "fps": board["fps"], "sheets": [f["url"] for f in board["fragments"]]}
    _probes[vid] = out
    return out


# ---------- keeping a clip ----------
def _index() -> dict:
    f = LIBRARY / "library.json"
    return read_json(f) if f.exists() else {}


def _h264(src: Path, out: Path) -> None:
    """Re-encode to H.264 at a quality that hides the second generation. YouTube's sharpest streams
    are VP9 and AV1, which Premiere and After Effects will not open."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
                    "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], check=True, timeout=1800)


def _section(vid: str, start: float, length: float, out: Path) -> None:
    """Fetch only the moment, at the best picture YouTube has, straight into an H.264 MP4."""
    with fetch._ydl({"format": fetch.VIDEO_FMT, "format_sort": fetch.VIDEO_SORT}) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    streams = info.get("requested_formats") or [info]
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for f in streams:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in (f.get("http_headers") or {}).items())]
        if f.get("protocol") == "https":
            # YouTube leaves a request for "everything from here on" hanging; asked for in pieces it answers at once
            cmd += ["-request_size", "1000000"]
        cmd += ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", f["url"]]
    seen = next((i for i, f in enumerate(streams) if f.get("vcodec") not in (None, "none")), None)
    heard = next((i for i, f in enumerate(streams) if f.get("acodec") not in (None, "none")), None)
    if seen is None:
        raise ValueError("that upload has no picture")
    part = out.with_name(out.stem + ".part.mp4")   # a save that is cut short must not pass for a finished one
    cmd += ["-map", f"{seen}:v:0", *(["-map", f"{heard}:a:0"] if heard is not None else []),
            "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=420)
        if done.returncode or not part.exists() or part.stat().st_size < 20000:
            raise ValueError("YouTube did not send that moment" + (f" ({done.stderr.strip()[-120:]})" if done.stderr.strip() else ""))
        part.replace(out)
    except subprocess.TimeoutExpired:
        raise ValueError("YouTube took too long to send that moment. Try it again, or pick another clip.")
    finally:
        part.unlink(missing_ok=True)


def _keep(clip: dict, folder: Path) -> Path:
    site, cid = clip.get("site"), str(clip.get("id") or "")
    if site == "youtube":
        if not _YT.fullmatch(cid):
            raise ValueError("unknown YouTube video")
        start, length = max(0.0, float(clip.get("start") or 0)), min(120.0, max(1.0, float(clip.get("length") or 12)))
        out = folder / f"yt-{cid}-{int(start):05d}-{int(length)}s.mp4"
        if not out.exists():
            _section(cid, start, length, out)
        return out
    if site == "sakuga":
        if not cid.isdigit():
            raise ValueError("unknown Sakugabooru post")
        posts = json.loads(clips._fetch(f"https://www.sakugabooru.com/post.json?tags=id:{cid}"))
        if not posts or posts[0].get("file_ext") not in ("mp4", "webm"):
            raise ValueError("that Sakugabooru post is not a video")
        got = clips._save(posts[0]["file_url"], folder / f"sk-{cid}.{posts[0]['file_ext']}")
    elif site == "themes":
        if not re.fullmatch(r"[\w.-]{1,120}\.webm", cid):
            raise ValueError("unknown opening")
        got = clips._save(f"https://v.animethemes.moe/{cid}", folder / f"at-{cid}")
    elif site == "filmgrab":
        url = str(clip.get("url") or "")
        if not url.startswith("https://film-grab.com/wp-content/uploads/"):
            raise ValueError("unknown Film-Grab still")
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", urllib.parse.unquote(url.rsplit("/", 1)[1]))[:80]
        return clips._save(url, folder / f"fg-{name}")
    else:
        raise ValueError("that clip cannot be saved")
    if got.suffix == ".webm":   # same reason as YouTube's VP9: editors do not open it
        out = got.with_suffix(".mp4")
        if not out.exists():
            _h264(got, out)
        got.unlink(missing_ok=True)
        return out
    return got


def _work(key: str, subject: str, clip: dict) -> None:
    job = _saves[key]
    try:
        with _saving:
            job["state"] = "saving"
            folder = LIBRARY / slugify(subject)
            folder.mkdir(parents=True, exist_ok=True)
            out = _keep(clip, folder)
            info = {} if out.suffix.lower() in clips._IMAGE else ffprobe(out)
            with _lock:
                index = _index()
                index[f"{folder.name}/{out.name}"] = {
                    "subject": subject, "title": str(clip.get("title") or "")[:200], "site": clip.get("site"),
                    "page": clip.get("page"), "start": clip.get("start"), "width": info.get("width"),
                    "height": info.get("height"), "duration": round(info.get("duration") or 0, 1),
                    "added": time.strftime("%Y-%m-%d %H:%M")}
                write_json(LIBRARY / "library.json", index)
        job.update(state="done", file=f"{folder.name}/{out.name}")
    except Exception as e:
        job.update(state="failed", error=str(e)[:200])


def save(subject: str, clip: dict) -> dict:
    """Keep a clip on this computer. It is fetched in the background; `saved` says how it is going."""
    if not isinstance(clip, dict) or not clip.get("key"):
        raise ValueError("that clip cannot be saved")
    if shutil.disk_usage(ROOT).free < MIN_FREE:
        raise ValueError("Under 2 GB is free on this drive. Make room, then save the clip.")
    key = str(clip["key"])[:80]
    with _lock:
        job = _saves.get(key)
        if job and job["state"] in ("queued", "saving", "done"):
            return {"key": key, **job}
        _saves[key] = job = {"state": "queued", "title": str(clip.get("title") or "")[:200]}
    threading.Thread(target=_work, args=(key, subject.strip()[:80] or "footage", clip), daemon=True).start()
    return {"key": key, **job}


def saved_file(rel: str) -> Path | None:
    """A saved clip by its place in the library, or None. The name comes from the page."""
    target = (LIBRARY / rel).resolve()
    return target if LIBRARY.resolve() in target.parents and target.is_file() else None


def saved() -> dict:
    """Every clip kept so far, newest first, and the ones still on their way."""
    items = []
    with _lock:
        index = _index()
    for rel, m in index.items():
        f = LIBRARY / rel
        if f.is_file():
            items.append({**m, "file": rel, "name": f.name, "size_mb": round(f.stat().st_size / 2**20, 1),
                          "still": f.suffix.lower() in clips._IMAGE, "at": f.stat().st_mtime})
    items.sort(key=lambda i: -i["at"])
    free = shutil.disk_usage(ROOT).free
    return {"items": items, "jobs": {k: dict(j) for k, j in _saves.items()}, "folder": str(LIBRARY),
            "free_gb": round(free / 2**30, 1)}


def forget(rel: str) -> dict:
    """Delete a saved clip."""
    target = saved_file(rel)
    if not target:
        raise ValueError("that clip is not in the library")
    target.unlink()
    with _lock:
        index = _index()
        index.pop(rel, None)
        write_json(LIBRARY / "library.json", index)
        for key in [k for k, j in _saves.items() if j.get("file") == rel]:
            _saves.pop(key)
    return saved()
