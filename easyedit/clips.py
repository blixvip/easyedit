"""Clip library: find clips on the free clip sites, bring them into an edit, and keep track of
where every source in the edit came from.

    python -m easyedit.clips <slug> <montage|speech> '<item json>'

An edit's sources stay in sources.json, exactly as the pipeline reads them. clips.json sits next
to it and remembers what each added file is: the site, the page it came from, its title.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .util import JOBS, log, probe, read_json, write_json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
ROLES = ("montage", "speech", "music")
STILL_SECONDS = 8.0
_IMAGE = (".jpg", ".jpeg", ".png", ".webp")
NOT_READY = "Make this edit once before adding clips to it."
BLOCKED = ("Yarn and PlayPhrase block downloads from apps. Use them to find the line, then get the "
           "scene from clip.cafe or YouTube.")

# `open` sites cannot be searched from here (they turn away anything that is not a person in a
# browser), so the page offers a link that opens the search there instead
SITES = [
    {"id": "twixtor", "name": "Twixtor", "roles": ["montage", "speech"], "anime": True,
     "about": "Raw anime footage the way editors share it: twixtor clips and scenepacks, 4K first. "
              "Type a character, a fight or an arc."},
    {"id": "themes", "name": "Openings", "roles": ["montage", "music"], "anime": True,
     "about": "Openings and endings from AnimeThemes, creditless Blu-ray when there is one. "
              "Cut shots from one, or make it the song."},
    {"id": "youtube", "name": "YouTube", "roles": ["montage", "speech", "music"],
     "about": "Scenepacks and full scenes, up to 4K. The sharpest free footage."},
    {"id": "clipcafe", "name": "clip.cafe", "roles": ["speech", "montage"],
     "about": "Search by the words said. Sharpness varies from clip to clip: read the numbers once it is in."},
    {"id": "sakuga", "name": "Sakugabooru", "roles": ["montage"], "anime": True,
     "about": "Anime animation cuts. Search a show, a film or an animator."},
    {"id": "filmgrab", "name": "Film-Grab", "roles": ["montage"],
     "about": "Film stills. Each one is added as a slow push-in shot."},
    {"id": "yarn", "name": "Yarn", "open": "https://getyarn.io/yarn-find?text={q}",
     "about": "Finds a line by its words. Opens in your browser."},
    {"id": "playphrase", "name": "PlayPhrase", "open": "https://www.playphrase.me/#/search?q={q}",
     "about": "Finds which film says a phrase. Opens in your browser."},
]
_NAMES = {s["id"]: s["name"] for s in SITES} | {"web": "Link", "file": "This computer", "still": "Picture"}


# ---------- the sites ----------
def _request(url: str, referer: str | None = None) -> urllib.request.Request:
    url = urllib.parse.quote(url, safe="%/:=&?~#+!$,;'@()*[]")   # file names on these sites have spaces in them
    return urllib.request.Request(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})})


_tls = None


def _open(req: urllib.request.Request, timeout: float):
    """Open a request. Certificates are checked against certifi's list when it is installed: the list
    Windows hands Python can hold an expired link in a site's chain that browsers no longer follow."""
    global _tls
    if _tls is None:
        import ssl
        try:
            import certifi
            _tls = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _tls = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=timeout, context=_tls)


def _fetch(url: str, referer: str | None = None, timeout: float = 25) -> bytes:
    with _open(_request(url, referer), timeout) as r:
        return r.read()


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _youtube(query: str, movie: str, ani: dict | None) -> list[dict]:
    from . import fetch
    hits = fetch.search(query or f"{movie} scenepack 4K", 14, 8, 3600)
    # reactions, reviews and other people's edits sink; the order YouTube gave is kept otherwise
    hits.sort(key=lambda h: -min(0.0, fetch._title_score(h["title"], "montage", query or movie)))
    return [{"site": "youtube", "id": h["id"], "title": h["title"], "sub": "", "kind": "video",
             "thumb": f"https://i.ytimg.com/vi/{h['id']}/mqdefault.jpg", "page": h["url"],
             "duration": h["duration"]} for h in hits]


def _clipcafe(query: str, movie: str, ani: dict | None) -> list[dict]:
    page = _fetch("https://clip.cafe/?" + urllib.parse.urlencode({"s": query or movie, "usersearch": 1}),
                  "https://clip.cafe/").decode("utf-8", "replace")
    out = []
    for block in page.split('class="searchResultClip">')[1:]:
        block = block[:8000]
        link = re.search(r'<a href="([a-z0-9-]+)/([a-z0-9-]+)/"', block)
        if not link:
            continue
        slug = re.search(r'data-slug="([a-z0-9-]+)"', block)
        dur = re.search(r'class="videoDuration">\s*(?:(\d+):)?(\d+):(\d+)', block)
        film = re.search(r'class="clipMovie">(.*?)</div>', block, re.S)
        said = re.search(r'class="clipTrans">(.*?)</div>', block, re.S)
        words = re.sub(r"^Transcript:\s*", "", _text(said.group(1))) if said else ""
        words = re.sub(r"^([A-Z][\w .'-]{0,28}):(?=\S)", r"\1: ", words)   # "Cobb:You're" -> "Cobb: You're"
        clip = slug.group(1) if slug else link.group(2)
        out.append({"site": "clipcafe", "id": clip, "kind": "video",
                    "title": words[:160] or link.group(2).replace("-", " "),
                    "sub": _text(film.group(1)) if film else link.group(1).replace("-", " "),
                    "thumb": f"https://clip.cafe/img800/{clip}.jpg",
                    "page": f"https://clip.cafe/{link.group(1)}/{link.group(2)}/",
                    "duration": (int(dur.group(1) or 0) * 3600 + int(dur.group(2)) * 60 + int(dur.group(3)))
                    if dur else None})
    # this film's own clips first
    key = re.sub(r"[^a-z0-9]+", "-", movie.lower()).strip("-")
    out.sort(key=lambda r: key not in r["page"])
    for r in out:   # clip.cafe only serves its videos to its own pages, so the viewer plays them through the app
        r["preview"] = f"clips/peek?id={r['id']}"
    return out


def peek_url(clip: str) -> tuple[str, str] | None:
    """The clip.cafe video behind a preview, and the page to say it is asked from."""
    if not re.fullmatch(r"[a-z0-9-]{1,200}", clip):
        return None
    return f"https://clip.cafe/videos/{clip}.mp4", "https://clip.cafe/"


def _twixtor(query: str, movie: str, ani: dict | None) -> list[dict]:
    """YouTube, asked the way anime editors label raw footage, and ranked by what the titles promise."""
    from . import anime, fetch
    if query and re.search(r"twixtor|scene ?pack|clips|raw|4k", query, re.I):
        asks, known = [query], query
    elif ani:   # typed words are a character, a fight or an arc of this anime
        asks, known = anime.footage_queries(ani, query)[:2], anime.names(ani) + " " + query
    else:
        asks, known = [f"{query or movie} twixtor 4K", f"{query or movie} scenepack 4K"], query or movie
    found: dict[str, dict] = {}
    for ask in asks:
        for h in fetch.search(ask, 12, 8, 3600):
            found.setdefault(h["id"], h)
    hits = sorted(found.values(), key=lambda h: -fetch._title_score(h["title"], "montage", known, anime=True))
    return [{"site": "twixtor", "id": h["id"], "title": h["title"], "sub": "", "kind": "video",
             "thumb": f"https://i.ytimg.com/vi/{h['id']}/mqdefault.jpg", "page": h["url"],
             "duration": h["duration"]} for h in hits[:18]]


def _themes(query: str, movie: str, ani: dict | None) -> list[dict]:
    """An anime's openings and endings. Typed words name another anime."""
    from . import anime
    if query or not ani:
        ani = next(iter(anime.lookup(query or movie, 1)), None)
    if not ani:
        return []
    return [{"site": "themes", "id": t["video"], "audio": t["audio"], "kind": "video", "label": t["slug"],
             "title": f"{t['slug']} · {t['song']}" if t["song"] else t["slug"],
             "sub": " · ".join(x for x in (t["artists"], f"{t['height']}p" if t["height"] else "",
                                           "creditless" if t["creditless"] else "", ani["title"]) if x),
             "thumb": None, "preview": f"https://v.animethemes.moe/{t['video']}",
             "page": f"https://v.animethemes.moe/{t['video']}", "duration": None} for t in anime.themes(ani)]


def sakuga_posts(tries: list[str], order: str = "score", limit: int = 30) -> tuple[str, list[dict]]:
    """Sakugabooru's posts under the first of these tag names it knows: (the tag, its posts)."""
    base = "https://www.sakugabooru.com"
    tags, name = [], ""
    for name in tries[:5]:
        tags = [t for t in json.loads(_fetch(f"{base}/tag.json?" + urllib.parse.urlencode(
            {"name": name, "order": "count", "limit": 8}))) if t.get("count")]
        if tags:
            break
    if not tags:
        return "", []
    tag = next((t for t in tags if t["name"] == name), tags[0])["name"]
    return tag, json.loads(_fetch(f"{base}/post.json?" + urllib.parse.urlencode(
        {"tags": f"{tag} order:{order}", "limit": limit})))


def _sakuga(query: str, movie: str, ani: dict | None) -> list[dict]:
    from . import anime
    base = "https://www.sakugabooru.com"
    # an anime is filed under its romaji name there, so the edit's own anime is asked for by every name it has
    slug = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")  # noqa: E731
    tag, posts = sakuga_posts([slug(query)] if query else anime.sakuga_tags(ani) if ani else [slug(movie)])
    out = []
    for p in posts:
        if p.get("file_ext") not in ("mp4", "webm") or not p.get("file_url"):
            continue
        names = [t for t in p.get("tags", "").split() if t not in ("animated", tag)]
        out.append({"site": "sakuga", "id": str(p["id"]), "kind": "video",
                    "title": ", ".join(t.replace("_", " ") for t in names[:4]) or tag.replace("_", " "),
                    "sub": f"{tag.replace('_', ' ')} · {p.get('width')}×{p.get('height')}",
                    "thumb": p.get("preview_url"), "preview": p["file_url"],
                    "page": f"{base}/post/show/{p['id']}", "duration": None})
    return out


def _filmgrab(query: str, movie: str, ani: dict | None) -> list[dict]:
    posts = json.loads(_fetch("https://film-grab.com/wp-json/wp/v2/posts?" + urllib.parse.urlencode(
        {"search": query or movie, "per_page": 5, "_fields": "id,link,title"})))
    if not posts:
        return []
    want = (query or movie).lower()
    post = min(posts, key=lambda p: (html.unescape(p["title"]["rendered"]).lower() != want,
                                     len(p["title"]["rendered"])))
    if not post["link"].startswith("https://film-grab.com/"):
        return []
    page = _fetch(post["link"]).decode("utf-8", "replace")
    title = html.unescape(post["title"]["rendered"])
    out = []
    for full in dict.fromkeys(re.findall(
            r'class="bwg-a[^"]*"[^>]*href="(https://film-grab\.com/wp-content/uploads/photo-gallery/[^"?]+)', page)):
        name = full.rsplit("/", 1)[1]
        out.append({"site": "filmgrab", "id": name, "kind": "still", "title": title, "sub": name,
                    "thumb": full.replace("/photo-gallery/", "/photo-gallery/thumb/"), "url": full,
                    "page": post["link"], "duration": None})
    return out[:90]


_SEARCH = {"youtube": _youtube, "clipcafe": _clipcafe, "sakuga": _sakuga, "filmgrab": _filmgrab,
           "twixtor": _twixtor, "themes": _themes}


def search(job: Path, site: str, query: str) -> dict:
    """What a site has for these words. The film's own title is searched when nothing is typed."""
    find = _SEARCH.get(site)
    if not find:
        raise ValueError("that site cannot be searched from here")
    plan = job / "plan.json"
    movie = read_json(plan).get("title", job.name) if plan.exists() else job.name.replace("-", " ")
    try:
        from . import anime
        results = find(query.strip()[:120], movie, anime.of(job))
    except Exception as e:  # a site that is down, slow or has changed its pages must not take the editor with it
        raise ValueError(f"{_NAMES[site]} did not answer ({str(e)[:80]})")
    have = {(m.get("site"), m.get("id")) for m in _manifest(job).values()}
    for r in results:
        r["added"] = (r["site"], r["id"]) in have
    return {"results": results}


# ---------- what an edit holds ----------
def _manifest(job: Path) -> dict:
    f = job / "clips.json"
    return read_json(f) if f.exists() else {}


def whole(job: Path, media: Path) -> bool:
    """A clip that was added as a clip is footage from its first frame to its last. Only uploads
    found by search get their channel intro and end screen trimmed."""
    return bool(_manifest(job).get(str(media), {}).get("whole"))


def extras(job: Path, role: str, have: list[Path]) -> list[Path]:
    """Clips added by hand, which a fresh search for sources would otherwise forget."""
    return [Path(f) for f, m in _manifest(job).items()
            if role in m.get("roles", []) and Path(f).exists() and Path(f) not in have]


_probed: dict = {}


def _facts(media: Path) -> dict:
    """Size of the picture and how much data it carries: what tells a sharp source from a soft one."""
    if not media.is_file():
        return {"exists": False}
    st = media.stat()
    key = (str(media), st.st_mtime, st.st_size)
    if key not in _probed:
        try:
            info = probe(media)
        except (subprocess.SubprocessError, OSError, ValueError):
            info = {"duration": 0}
        dur = info.get("duration") or 0
        _probed[key] = {"exists": True, "width": info.get("width"), "height": info.get("height"),
                        "duration": round(dur, 1), "size_mb": round(st.st_size / 2**20, 1),
                        "kbps": round(st.st_size * 8 / dur / 1000) if dur else 0}
    return _probed[key]


def _yt(job: Path, media: Path) -> bool:
    """A download the pipeline made from YouTube: it is named by the video's id."""
    return bool(re.fullmatch(r"[\w-]{11}", media.stem)) and (job / "downloads") in media.parents


def _titles(job: Path, ids: list[str]) -> dict:
    """What the pipeline's own YouTube downloads are called. Asked once, then remembered."""
    cache = job / "work" / "titles.json"
    known = read_json(cache) if cache.exists() else {}
    for vid in ids:
        if vid in known:
            continue
        try:
            known[vid] = json.loads(_fetch("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
                f"https://www.youtube.com/watch?v={vid}", safe=""), timeout=6)).get("title", "")
        except (urllib.error.URLError, TimeoutError, ValueError):
            known[vid] = ""   # private, deleted or offline: the file name stands in
        write_json(cache, known)
    return {k: v for k, v in known.items() if v}


def library(job: Path) -> dict:
    """Every source in the edit, what it is used for, and where it came from."""
    src_file = job / "sources.json"
    src = read_json(src_file) if src_file.exists() else {}
    man = _manifest(job)
    quote = job / "quote.json"
    said = read_json(quote).get("clip", 0) if quote.exists() else 0
    rows: dict[str, dict] = {}
    names = _titles(job, [Path(f).stem for r in ("speech", "montage", "music") for f in src.get(r, [])
                          if _yt(job, Path(f)) and not man.get(f, {}).get("title")])
    for role in ("speech", "montage", "music"):
        for i, f in enumerate(src.get(role, [])):
            media, m = Path(f), man.get(f, {})
            row = rows.get(f)
            if not row:
                site = "youtube" if _yt(job, media) and m.get("site") in (None, "file") else m.get("site") or "file"
                row = rows[f] = {
                    "path": f, "name": media.name, "sub": m.get("sub", ""),
                    "title": m.get("title") or names.get(media.stem) or media.stem,
                    "site": site, "siteName": _NAMES.get(site, site), "added": bool(m),
                    "page": m.get("page") or (f"https://www.youtube.com/watch?v={media.stem}"
                                              if site == "youtube" else None),
                    "roles": [], **_facts(media)}
            row["roles"].append({"role": role, "index": i})
            if role == "speech" and i == said:
                row["captions"] = True
            if role == "montage":
                cache = job / "work" / f"shots-{media.stem}.json"
                row["shots"] = len(read_json(cache).get("shots", [])) if cache.exists() else 0
    from . import anime
    home = (job / "downloads").resolve()
    for row in rows.values():
        row["mine"] = home in Path(row["path"]).resolve().parents   # a file easyedit put there, so one it may delete
    kept = [{"path": f, "name": Path(f).name, "title": m.get("title") or Path(f).stem, "site": m.get("site"),
             "siteName": _NAMES.get(m.get("site"), "This computer"), "was": m.get("was") or "montage", **_facts(Path(f))}
            for f, m in man.items() if not m.get("roles") and f not in rows and Path(f).is_file()]
    return {"clips": list(rows.values()), "kept": kept, "sites": SITES, "anime": anime.of(job),
            "free_gb": round(shutil.disk_usage(job).free / 2**30, 1)}


# ---------- bringing a clip in ----------
def _save(url: str, dest: Path, referer: str | None = None) -> Path:
    if dest.exists() and dest.stat().st_size:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    log(f"download: {dest.name}")
    with _open(_request(url, referer), 60) as r, part.open("wb") as fh:
        shutil.copyfileobj(r, fh, 512 * 1024)
    part.replace(dest)
    return dest


def _still_clip(picture: Path, out: Path) -> Path:
    """A still as footage: a few seconds of slow push-in, so it cuts like any other shot."""
    if out.exists():
        return out
    info = probe(picture)
    w = min(1920, info["width"]) // 2 * 2
    h = int(round(w * info["height"] / info["width"] / 2)) * 2
    n = int(STILL_SECONDS * 30)
    out.parent.mkdir(parents=True, exist_ok=True)
    # the picture is enlarged before the move: zoompan steps in whole pixels, and at full size that shows as a shake
    vf = (f"scale={w * 2}:{h * 2}:flags=lanczos,zoompan=z='1+0.07*on/{n}':x='iw/2-(iw/zoom/2)':"
          f"y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps=30,format=yuv420p")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(picture), "-vf", vf, "-frames:v", str(n),
                    "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-an", str(out)], check=True, timeout=300)
    return out


def _from_link(link: str) -> dict:
    """Work out what a pasted link or file path is."""
    link = link.strip().strip('"')
    if not re.match(r"https?://", link):
        p = Path(link)
        if not p.is_file():
            raise ValueError("No file at that path. Paste a link, or the full path of a video on this computer.")
        return {"site": "still" if p.suffix.lower() in _IMAGE else "file", "url": str(p.resolve()),
                "title": p.stem}
    url = urllib.parse.urlparse(link)
    host = url.netloc.lower().removeprefix("www.")
    if host.endswith(("getyarn.io", "yarn.co", "playphrase.me")):
        raise ValueError(BLOCKED)
    if host == "clip.cafe":
        if url.path.startswith("/videos/"):
            return {"site": "clipcafe", "id": Path(url.path).stem, "title": Path(url.path).stem.replace("-", " ")}
        page = _fetch(link, "https://clip.cafe/").decode("utf-8", "replace")
        video = re.search(r'property="og:video" content="https://clip\.cafe/videos/([a-z0-9-]+)\.mp4"', page)
        if not video:
            raise ValueError("That clip.cafe page has no clip on it. Open one clip, then paste its link.")
        title = re.search(r'property="og:title" content="([^"]*)"', page)
        return {"site": "clipcafe", "id": video.group(1), "page": link,
                "title": html.unescape(title.group(1)) if title else video.group(1).replace("-", " ")}
    if host == "sakugabooru.com" and (m := re.search(r"/post/show/(\d+)", url.path)):
        return {"site": "sakuga", "id": m.group(1), "page": link}
    if url.path.lower().endswith(_IMAGE):
        return {"site": "still", "url": link, "page": link, "title": Path(url.path).stem}
    return {"site": "web", "url": link, "page": link}


def _bring(job: Path, item: dict, role: str) -> tuple[Path, dict]:
    """Get the item onto this computer. Returns the file and what to remember about it."""
    if item.get("site") == "link":
        item = _from_link(str(item.get("url") or ""))
    site, dl = item.get("site"), job / "downloads" / role
    meta = {"site": site, "id": str(item.get("id") or ""), "title": str(item.get("title") or "")[:200],
            "sub": str(item.get("sub") or "")[:120], "page": item.get("page"), "whole": True}
    if site == "file":
        return Path(item["url"]), meta
    if site == "themes":
        audio = str(item.get("audio") or "")
        if not re.fullmatch(r"[\w.-]{1,120}\.webm", meta["id"]):
            raise ValueError("unknown opening")
        meta["page"] = f"https://v.animethemes.moe/{meta['id']}"
        if role == "music" and re.fullmatch(r"[\w.-]{1,120}\.ogg", audio):
            meta["id"] = audio
            return _save(f"https://a.animethemes.moe/{audio}", dl / f"at-{audio}"), meta
        return _save(f"https://v.animethemes.moe/{meta['id']}", dl / f"at-{meta['id']}"), meta
    if site in ("youtube", "twixtor", "web"):
        from . import fetch
        url = str(item["url"]) if site == "web" else f"https://www.youtube.com/watch?v={meta['id']}"
        if site != "web" and not re.fullmatch(r"[\w-]{11}", meta["id"]):
            raise ValueError("unknown YouTube video")
        if not meta["title"]:
            try:
                with fetch._ydl({}) as y:
                    meta["title"] = (y.extract_info(url, download=False).get("title") or "")[:200]
            except Exception:
                pass
        meta["page"] = meta["page"] or url
        return fetch.download(url, dl, audio_only=role == "music"), meta
    if role == "music":
        raise ValueError("the song comes from YouTube, an opening, a link or a file")
    if site == "clipcafe":
        if not re.fullmatch(r"[a-z0-9-]{1,200}", meta["id"]):
            raise ValueError("unknown clip.cafe clip")
        return _save(f"https://clip.cafe/videos/{meta['id']}.mp4", dl / f"cc-{meta['id'][:70]}.mp4",
                     "https://clip.cafe/"), meta
    if site == "sakuga":
        if not meta["id"].isdigit():
            raise ValueError("unknown Sakugabooru post")
        posts = json.loads(_fetch(f"https://www.sakugabooru.com/post.json?tags=id:{meta['id']}"))
        if not posts or posts[0].get("file_ext") not in ("mp4", "webm"):
            raise ValueError("that Sakugabooru post is not a video")
        post = posts[0]
        if not meta["title"]:
            meta["title"] = ", ".join(t.replace("_", " ") for t in post.get("tags", "").split()[:4])
        meta["page"] = f"https://www.sakugabooru.com/post/show/{meta['id']}"
        return _save(post["file_url"], dl / f"sk-{meta['id']}.{post['file_ext']}"), meta
    if site in ("filmgrab", "still"):
        url = str(item.get("url") or "")
        if site == "filmgrab" and not url.startswith("https://film-grab.com/wp-content/uploads/"):
            raise ValueError("unknown Film-Grab still")
        if role != "montage":
            raise ValueError("a still has no sound, so it cannot be the speech")
        if re.match(r"https?://", url):
            name = re.sub(r"[^A-Za-z0-9._-]+", "-", urllib.parse.unquote(url.split("?")[0].rsplit("/", 1)[1]))[:80]
            picture = _save(url, job / "downloads" / "stills" / name)
        else:
            picture = Path(url)
        meta["id"] = meta["id"] or picture.name
        prefix = "fg" if site == "filmgrab" else "still"
        return _still_clip(picture, dl / f"{prefix}-{picture.stem[:70]}.mp4"), meta
    raise ValueError("that clip cannot be added")


def _in_use(job: Path, media: Path) -> bool:
    src = read_json(job / "sources.json")
    return any(str(media) in src.get(r, []) for r in ("speech", "montage", "music"))


def add(job: Path, item: dict, role: str) -> Path:
    """Bring a clip in, read it (shots or words), then make it one of the edit's sources."""
    if role not in ROLES:
        raise ValueError("a clip is added for the shots, for the speech, or as the song")
    src_file = job / "sources.json"
    if not src_file.exists():
        raise ValueError(NOT_READY)
    log(f"job: {job}")
    media, meta = _bring(job, item, role)
    media = media.resolve()
    info = probe(media)
    if role == "music":
        return _song(job, media, meta, info)
    if not info.get("width"):
        raise ValueError("that file has no picture in it")
    if role == "speech" and not info["has_audio"]:
        raise ValueError("this clip has no sound, so it cannot be the speech")
    # a long upload from a search has a channel intro and an end screen; a clip is footage end to end
    meta["whole"] = meta["site"] not in ("youtube", "twixtor", "web", "file") or info["duration"] < 90
    if str(media) in read_json(src_file)[role]:
        log("done: that clip is already in this edit")
        return media
    cache = job / "work" / f"{'shots' if role == 'montage' else 'words'}-{media.stem}.json"
    if role == "montage":
        from . import shots
        from .vision import FaceDetector
        log(f"clip: reading the shots in {media.name}")
        ok = shots.analyze(media, cache, FaceDetector(), whole=meta["whole"])["shots"]
        why = "no usable shots in that clip (it is shorter than a second, or nothing but black)"
    else:
        from . import transcribe
        log(f"clip: reading the words in {media.name}")
        ok = transcribe.transcribe(media, cache)
        why = "no speech was heard in that clip"
    if not ok:  # leave nothing behind: a kept cache would answer "nothing here" to the next try too
        cache.unlink(missing_ok=True)
        if not _in_use(job, media) and (job / "downloads").resolve() in media.parents:
            media.unlink(missing_ok=True)
        raise ValueError(why)
    src = read_json(src_file)
    src[role].append(str(media))
    write_json(src_file, src)
    quote = job / "quote.json"
    if role == "speech" and quote.exists():  # the chosen quote stays chosen: it only gained a clip to pick from
        q = read_json(quote)
        q["files"] = src["speech"]
        write_json(quote, q)
    man = _manifest(job)
    entry = man.get(str(media), {})  # a clip already here in another role keeps what is known about it
    man[str(media)] = {**meta, **{k: v for k, v in entry.items() if v and k != "whole"},
                       "added": entry.get("added") or time.strftime("%Y-%m-%d %H:%M"),
                       "roles": sorted({*entry.get("roles", []), role})}
    write_json(job / "clips.json", man)
    log(f"done: added {meta['title'][:60] or media.name}")
    return media


def _song(job: Path, media: Path, meta: dict, info: dict) -> Path:
    """Make this the edit's song. There is one song: the old one steps out."""
    if not info["has_audio"]:
        raise ValueError("there is no sound in that file")
    if info["duration"] < 40:
        raise ValueError("that is too short to be the song (under 40 seconds)")
    from . import beats
    log(f"clip: reading the beat of {media.name}")
    beats.analyze(media, job / "work" / f"beats-{media.stem}.json")
    src = read_json(job / "sources.json")
    old, src["music"] = [f for f in src.get("music", []) if f != str(media)], [str(media)]
    write_json(job / "sources.json", src)
    man = _manifest(job)
    for f in old:   # a song that was added here goes with its file; the one the pipeline found is left alone
        entry = man.get(f)
        if not entry:
            continue
        entry["roles"] = [r for r in entry.get("roles", []) if r != "music"]
        if not entry["roles"]:
            man.pop(f)
            if (job / "downloads").resolve() in Path(f).resolve().parents:
                Path(f).unlink(missing_ok=True)
    entry = man.get(str(media), {})
    man[str(media)] = {**meta, **{k: v for k, v in entry.items() if v and k != "whole"},
                       "added": entry.get("added") or time.strftime("%Y-%m-%d %H:%M"),
                       "roles": sorted({*entry.get("roles", []), "music"})}
    write_json(job / "clips.json", man)
    log(f"done: the song is now {meta['title'][:60] or media.name}")
    return media


def remove(job: Path, path: str, role: str, delete: bool = False) -> dict:
    """Take a source out of the edit. Its file stays on this computer, ready to be added back,
    unless `delete` asks for a downloaded file to go too."""
    src_file = job / "sources.json"
    src = read_json(src_file)
    if role not in ("montage", "speech") or path not in src.get(role, []):
        raise ValueError("that clip is not in this edit")
    files, media = src[role], Path(path)
    if len(files) < 2:
        raise ValueError(f"An edit needs at least one clip for its {'speech' if role == 'speech' else 'shots'}. "
                         "Add another before removing this one.")
    idx = files.index(path)
    if role == "speech":
        quote = job / "quote.json"
        q = read_json(quote) if quote.exists() else {}
        if q.get("clip", 0) == idx:
            raise ValueError("The captions are cut from this clip. Pick the speech from another clip first, "
                             "in the Speech tab.")
        files.pop(idx)
        if q:
            q["files"] = files
            if q.get("clip", 0) > idx:
                q["clip"] -= 1
            write_json(quote, q)
    else:
        files.pop(idx)
        curate = job / "curate.json"
        if curate.exists():  # choices that named this clip's shots mean nothing now
            cur, mine = read_json(curate), f"{media.stem}@"
            for key in ("pin", "exclude"):
                cur[key] = [i for i in cur.get(key, []) if not i.startswith(mine)]
            if str(cur.get("hero") or "").startswith(mine):
                cur["hero"] = None
            cur["shots"] = {i: o for i, o in (cur.get("shots") or {}).items() if not i.startswith(mine)}
            write_json(curate, {k: v for k, v in cur.items() if v})
    write_json(src_file, src)
    man = _manifest(job)
    still_used = [r for r in ("speech", "montage", "music") if path in src.get(r, [])]
    if still_used:
        if path in man:
            man[path]["roles"] = [r for r in man[path].get("roles", []) if r in still_used]
    elif delete or not media.is_file():
        man.pop(path, None)
        _erase(job, media)
    else:   # kept: what was read from it stays too, so adding it back is instant
        man[path] = {**man.get(path, {"site": "youtube" if _yt(job, media) else "file", "title": ""}),
                     "roles": [], "was": role}
    write_json(job / "clips.json", man)
    return library(job)


def _erase(job: Path, media: Path) -> None:
    """Delete a source's file (only one easyedit downloaded, never a file of the user's own) and what was read from it."""
    if (job / "downloads").resolve() in media.resolve().parents:
        for f in (media, media.parent / "proxy" / f"{media.stem}.mp4"):
            f.unlink(missing_ok=True)
    for f in (job / "work" / f"shots-{media.stem}.json", job / "work" / f"words-{media.stem}.json"):
        f.unlink(missing_ok=True)


def discard(job: Path, path: str) -> dict:
    """Delete the file of a clip that was removed from the edit earlier and kept."""
    man = _manifest(job)
    if path not in man or man[path].get("roles") or _in_use(job, Path(path)):
        raise ValueError("that clip is still in the edit: remove it first")
    man.pop(path)
    _erase(job, Path(path))
    write_json(job / "clips.json", man)
    return library(job)


def restore(job: Path) -> int:
    """Download again every source that is no longer on this computer, into the place it had, so the
    edit's shots and choices still mean the same thing."""
    from . import fetch
    src_file = job / "sources.json"
    src, man = read_json(src_file), _manifest(job)
    log(f"job: {job}")
    lost = sorted({f for r in ("speech", "montage", "music") for f in src.get(r, []) if not Path(f).exists()})
    if not lost:
        log("done: nothing is missing")
        return 0
    back, failed = 0, []
    for f in lost:
        media, m = Path(f), man.get(f, {})
        song = f in src.get("music", []) and f not in src.get("speech", []) + src.get("montage", [])
        site, clip = m.get("site"), str(m.get("id") or "")
        try:
            if site == "themes" and clip:
                got = _save(f"https://{'a' if clip.endswith('.ogg') else 'v'}.animethemes.moe/{clip}", media)
            elif site == "clipcafe" and clip:
                got = _save(f"https://clip.cafe/videos/{clip}.mp4", media, "https://clip.cafe/")
            elif site in ("sakuga", "filmgrab"):
                got, _ = _bring(job, {**m, "url": "https://film-grab.com/wp-content/uploads/photo-gallery/" + clip},
                                "montage")
            elif site == "web" and m.get("page"):
                got = fetch.download(m["page"], media.parent, audio_only=song)
            elif re.fullmatch(r"[\w-]{11}", media.stem):   # the pipeline names a YouTube download by the video's id
                got = fetch.download(f"https://www.youtube.com/watch?v={media.stem}", media.parent, audio_only=song)
            else:
                raise ValueError("it was a file on this computer, not a download")
            got = got.resolve()
            if str(got) != f:   # the site now serves another container: every list that named the old file follows
                for r in ("speech", "montage", "music"):
                    src[r] = [str(got) if x == f else x for x in src.get(r, [])]
                if f in man:
                    man[str(got)] = man.pop(f)
            back += 1
        except Exception as e:
            log(f"could not get {media.name} again: {str(e)[:120]}")
            failed.append(m.get("title") or media.name)
    write_json(src_file, src)
    write_json(job / "clips.json", man)
    quote = job / "quote.json"
    if quote.exists():
        q = read_json(quote)
        q["files"] = src["speech"]
        write_json(quote, q)
    if failed:
        raise ValueError(f"{len(failed)} of {len(lost)} could not be downloaded again: {', '.join(failed)[:160]}")
    log(f"done: {back} file{'s' if back != 1 else ''} downloaded again")
    return back


def main(argv=None) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    slug, role, *item = (argv or sys.argv[1:])[:3]
    try:
        if role == "restore":
            restore(JOBS / slug)
        else:
            add(JOBS / slug, json.loads(item[0]), role)
    except Exception as e:  # the page shows this line; a traceback would tell the user nothing
        print(f"error: {str(e)[:300]}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
