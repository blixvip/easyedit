"""Local web UI: browse finished edits, start new ones, watch progress live.

    python -m easyedit.web          # http://127.0.0.1:4331

Standard library only - no Flask, no build step.
"""
from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import anime, clips, doctor, editor
from . import footage as finder
from .util import CACHE, JOBS, ROOT, probe, read_json, slugify

PORT = int(os.environ.get("EASYEDIT_PORT", "4331"))
WEB = Path(__file__).parent / "web"
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
mimetypes.add_type("image/svg+xml", ".svg")  # missing from the Windows registry on some machines


def picture_bytes(raw: bytes, content_type: str) -> bytes:
    """The uploaded picture, whether the browser sent a form or raw image bytes."""
    if "multipart/form-data" not in (content_type or "").lower():
        return raw
    match = re.search(r"boundary=([^;]+)", content_type, re.I)
    if not match:
        raise ValueError("choose a PNG, JPEG, or WebP picture")
    boundary = match.group(1).strip().strip('"').encode()
    for part in raw.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        header, body = part.split(b"\r\n\r\n", 1)
        if b'name="picture"' not in header and b"name=picture" not in header:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        return body
    raise ValueError("choose a PNG, JPEG, or WebP picture")


def safe_job(slug: str) -> Path | None:
    """A job directory inside jobs/, or None. Slugs come from the page and the URL."""
    if not isinstance(slug, str):
        return None
    slug = slug.strip()
    if not slug or len(slug) > 60 or not _SLUG.fullmatch(slug):
        return None
    root = JOBS.resolve()
    job = (root / slug).resolve()
    if root not in job.parents:
        return None
    return job

# log line -> (stage label, fraction of the run that is done once it appears)
STAGES = [
    (re.compile(r"^\s*\[.*?\] job:"), "starting", 0.02),
    (re.compile(r"plan:|sources: reusing"), "planning", 0.06),
    (re.compile(r"download:| source: "), "downloading footage", 0.12),
    (re.compile(r"whisper:"), "transcribing", 0.26),
    (re.compile(r"clip: reading the (shots|words|beat)"), "reading the clip", 0.4),
    (re.compile(r"quote \("), "choosing the quote", 0.34),
    (re.compile(r"shots:.*candidates"), "scoring shots", 0.44),
    (re.compile(r"beats:"), "finding the beat", 0.48),
    (re.compile(r"timeline:"), "cutting to the beat", 0.52),
    (re.compile(r"faces:"), "tracking faces", 0.6),
    (re.compile(r"footage:|build: reusing"), "footage ready", 0.64),
    (re.compile(r"depth:"), "reading depth", 0.66),
    (re.compile(r"render:.*section"), "rendering", 0.68),
    (re.compile(r"\] done:|skipping render"), "done", 1.0),
]

_runs: dict[str, dict] = {}
_lock = threading.Lock()
_setup: dict = {"report": None, "checked": 0.0, "busy": False}


class ApiError(ValueError):
    def __init__(self, message: str, code: int = 400, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def setup_report(force: bool = False) -> dict:
    """The doctor report, refreshed in the background (the tool checks take a few seconds)."""
    def work():
        try:
            _setup["report"] = doctor.report()
            _setup["checked"] = time.time()
        finally:
            _setup["busy"] = False
    stale = time.time() - _setup["checked"] > 60
    if (force or stale or _setup["report"] is None) and not _setup["busy"]:
        _setup["busy"] = True
        threading.Thread(target=work, daemon=True).start()
    return {"report": _setup["report"], "checking": _setup["busy"]}


LOGIN = {"claude": ["claude", "auth", "login"], "codex": ["codex", "login"]}


def connect(provider: str) -> dict:
    """Open a terminal window running the provider's own sign-in; the browser flow happens there."""
    argv = LOGIN.get(provider)
    if not argv:
        raise ValueError(f"unknown account: {provider}")
    if not shutil.which(argv[0]):
        raise ValueError(f"{argv[0]} isn't installed yet")
    cmd = " ".join(argv)
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "easyedit sign-in", "cmd", "/k", cmd])
    elif sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"'])
    else:
        term = next((t for t in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm") if shutil.which(t)), None)
        if not term:
            raise ValueError(f"no terminal found; run `{cmd}` yourself")
        subprocess.Popen([term, "-e", cmd])
    return {"opened": True, "command": cmd}


def job_dirs() -> list[Path]:
    if not JOBS.exists():
        return []
    return sorted((p for p in JOBS.iterdir() if p.is_dir()), key=lambda p: -p.stat().st_mtime)


def output_of(job: Path) -> Path | None:
    vids = [p for p in job.glob("*.mp4") if "preview" not in p.name]
    return max(vids, key=lambda p: p.stat().st_mtime) if vids else None


def thumb_of(job: Path) -> Path | None:
    """A still for the project's card: from the export, or from the cut footage when nothing is exported yet."""
    out, at = output_of(job), "3"
    if not out:
        out = job / "render" / "footage.mp4"
        if not out.exists():
            return None
        at = f"{_length(out) * 0.62:.2f}"   # past the speech, into the montage
    thumb = job / "work" / "thumb.jpg"
    if thumb.exists() and thumb.stat().st_mtime >= out.stat().st_mtime:
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", at, "-i", str(out), "-frames:v", "1",
                        "-vf", "scale=640:-2", str(thumb)], check=True, timeout=60)
    except Exception:
        return None
    return thumb if thumb.exists() else None


def progress_of(slug: str, log: Path) -> dict:
    stage, pct = "queued", 0.0
    text = ""
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            for pattern, label, frac in STAGES:
                if pattern.search(line):
                    stage, pct = label, max(pct, frac)
        # while rendering, the section logs give real frame counts
        if stage == "rendering":
            done, total = frames_done(slug)
            if total:
                pct = 0.68 + 0.3 * min(1.0, done / total)
                stage = f"rendering {done}/{total} frames"
    err = [l for l in text.splitlines() if "error" in l.lower() or "Traceback" in l]
    return {"stage": stage, "progress": round(pct, 3), "error": err[-1][:300] if err else None}


def run_status(slug: str, log: Path) -> dict:
    status = progress_of(slug, log)
    run = _runs.get(slug)
    code = run["proc"].poll() if run else None
    status.update(running=bool(run and code is None), exitCode=code)
    if run and code is not None:
        run["fh"].close()
        if run.get("stopped"):
            status.update(stage="stopped", error="The run was stopped.")
        elif code:
            status.update(stage="failed", error=status["error"] or f"The run failed (exit code {code}). Open the log for details.")
        else:
            status.update(stage="done", progress=1.0, error=None)
    return status


def frames_done(slug: str) -> tuple[int, int]:
    sections = JOBS / slug / "work" / "sections"
    if not sections.exists():
        return 0, 0
    done = total = 0
    for log in sections.glob("*.log"):
        m = re.match(r"section-(\d+)-(\d+)\.log", log.name)
        if not m:
            continue
        total += int(m.group(2)) - int(m.group(1))
        try:
            hits = re.findall(rb'"framesCompleted":(\d+)', log.read_bytes())
            done += int(hits[-1]) if hits else 0
        except OSError:
            pass
    return done, total


def captioned_line(job: Path) -> str:
    """The words actually on screen beat any planned description of the scene."""
    edit_js = job / "render" / "edit.js"
    if not edit_js.exists():
        return ""
    try:
        edit = json.loads(edit_js.read_text(encoding="utf-8").split("=", 1)[1].rstrip().rstrip(";"))
        words = [w["text"] for line in edit["captions"][:3] for w in line["words"]]
    except Exception:
        return ""
    text = " ".join(words)  # already upper-case, exactly as it reads on screen
    return f"“{text}…”" if text else ""


_lengths: dict = {}


def _length(media: Path) -> float:
    """Seconds of video in a file, remembered until the file changes (the page asks every few seconds)."""
    key = (str(media), media.stat().st_mtime)
    if key not in _lengths:
        try:
            _lengths[key] = probe(media)["duration"]
        except Exception:
            _lengths[key] = 0.0
    return _lengths[key]


def _ready(job: Path, src: dict) -> bool:
    """Can the editor open this project? It needs the plan, the quote, and every source read once."""
    if not src or not all((job / f).exists() for f in ("plan.json", "quote.json")):
        return False
    stems = [Path(f).stem for f in src.get("montage", [])]
    music = src.get("music") or []
    return bool(stems and music) and (job / "work" / f"beats-{Path(music[0]).stem}.json").exists() \
        and all((job / "work" / f"shots-{stem}.json").exists() for stem in stems)


def job_info(job: Path) -> dict:
    out = output_of(job)
    plan = read_json(job / "plan.json") if (job / "plan.json").exists() else {}
    src = read_json(job / "sources.json") if (job / "sources.json").exists() else {}
    run = _runs.get(job.name)
    running = bool(run and run["proc"].poll() is None)
    if run and not running:
        run["fh"].close()   # a finished run must let go of its log, or the project's folder cannot be deleted
    footage = job / "render" / "footage.mp4"
    cut = footage.exists() and (job / "render" / "edit.js").exists()
    ready = _ready(job, src)
    ani = anime.of(job)
    files = [Path(f) for k in ("speech", "montage", "music") for f in src.get(k, [])]
    # when the project was last worked on: its choices, its cut or its export, whichever is newest
    touched = [f.stat().st_mtime for f in (job / "plan.json", job / "quote.json", job / "curate.json",
                                           job / "settings.json", job / "sources.json", footage, out) if f and f.exists()]
    info = {
        "slug": job.name,
        "title": plan.get("title") or job.name.replace("-", " ").title(),
        "year": plan.get("year"),
        "scene": captioned_line(job) or plan.get("speech_scene", ""),
        "music": plan.get("music_query", ""),
        "updated": max(touched) if touched else job.stat().st_mtime,
        "running": running,
        "ready": ready,
        "cut": cut,
        "missing": bool(files) and not all(f.exists() for f in files),
        "sources": len(set(files)),
        "kind": "anime" if ani else "film",
        "cover": ani.get("cover") if ani else None,
        "video": f"/media/{job.name}/{out.name}" if out else None,
        "thumb": f"/thumb/{job.name}.jpg" if out or cut else None,
        "folder": str(job),
    }
    if out:
        info["duration"] = round(_length(out), 1)
        info["size"] = round(out.stat().st_size / 2**20)
    elif cut:
        info["duration"] = round(_length(footage), 1)
    if running:  # a finished job shows its video, not a stale progress bar
        info.update(run_status(job.name, job / "run.log"))
    elif run and run["proc"].poll():
        info["failed"] = run_status(job.name, job / "run.log")["error"]
    elif not ready and (job / "run.log").exists():  # it never got as far as the editor: say why
        last = progress_of(job.name, job / "run.log")
        if last["stage"] != "done":
            info["failed"] = (last["error"] or f"stopped while {last['stage']}").removeprefix("error: ")
    return info


def job_row(job: Path) -> dict:
    """job_info, but one project with a broken file cannot take the whole list down with it."""
    try:
        return job_info(job)
    except Exception as e:
        return {"slug": job.name, "title": job.name.replace("-", " ").title(), "year": None, "scene": "", "music": "",
                "updated": job.stat().st_mtime, "running": False, "ready": False, "cut": False, "missing": False,
                "sources": 0, "kind": "film", "cover": None, "video": None, "thumb": None, "folder": str(job),
                "failed": f"One of its files could not be read ({e})"[:300]}


def start_job(body: dict) -> dict:
    body = dict(body)
    movie = body.get("movie")
    if not isinstance(movie, str):
        raise ValueError("a movie name is required")
    movie = movie.strip()
    if not movie:
        raise ValueError("a movie name is required")
    montage = body.get("montage") or []
    if not isinstance(montage, list) or any(not isinstance(m, str) for m in montage):
        raise ValueError("montage must be a list of clip links or file paths")
    for key in ("speech", "music"):
        if body.get(key) is not None and not isinstance(body[key], str):
            raise ValueError(f"{key} must be a clip link, file path, or search")
    if body.get("fps") is not None:
        if str(body["fps"]) not in ("24", "30", "60"):
            raise ValueError("fps must be 24, 30, or 60")
        body["fps"] = int(body["fps"])
    if body.get("llm") is not None and body["llm"] not in ("auto", "claude", "codex", "none"):
        raise ValueError("choose auto, claude, codex, or none for the writer")
    for key in ("montage_length", "hero_length"):
        if body.get(key) is not None:
            try:
                value = float(body[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a positive number of seconds") from None
            if isinstance(body[key], bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a positive number of seconds")
    slug = slugify(movie)
    resume = body.get("resume")
    if resume is not None:
        job = safe_job(resume)
        if job is None or not job.is_dir():
            raise ApiError("unknown edit", 404)
        slug = job.name
    if safe_job(slug) is None:
        raise ValueError("choose a shorter movie name")
    with _lock:
        run = _runs.get(slug)
        if run and run["proc"].poll() is None:
            return {"slug": slug, "already": True}
        job = safe_job(slug)
        if job.is_dir() and any(job.iterdir()) and resume is None and body.get("fresh") is not True:
            raise ApiError("That project already exists. Open it to edit or export.", 409, slug=slug, exists=True)
        if resume is not None and (job / "sources.json").exists():
            argv = editor.run_argv(job, "full")
            _launch(slug, argv)
            return {"slug": slug, "started": True}
        argv = [sys.executable, "-u", "-m", "easyedit", slug if resume is not None else movie]
        for flag in ("speech", "music"):
            if body.get(flag):
                argv += [f"--{flag}", str(body[flag])]
        for m in montage:
            if m.strip():
                argv += ["--montage", m.strip()]
        for flag, key in (("--fps", "fps"), ("--montage-length", "montage_length"),
                          ("--hero-length", "hero_length"), ("--llm", "llm")):
            if body.get(key):
                argv += [flag, str(body[key])]
        if isinstance(body.get("anime"), bool):
            argv.append("--anime" if body["anime"] else "--no-anime")
        for key, flag in (("draft", "--draft"), ("fresh", "--fresh")):
            if body.get(key):
                argv.append(flag)
        _launch(slug, argv)
    return {"slug": slug, "started": True}


def _launch(slug: str, argv: list[str]) -> None:
    job = safe_job(slug)
    if job is None:
        raise ValueError("unknown edit")
    previous = _runs.get(slug)
    if previous:
        if previous["proc"].poll() is None:
            raise ValueError("Wait for the current run to finish first.")
        previous["fh"].close()
    job.mkdir(parents=True, exist_ok=True)
    fh = (job / "run.log").open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except Exception:
        fh.close()
        raise
    _runs[slug] = {"proc": proc, "fh": fh, "started": time.time(), "argv": argv}


CHOICES = ("plan.json", "quote.json", "curate.json", "settings.json", "timeline.json")   # everything the editor saves
_STAMP = re.compile(r"^\d{8}-\d{6}(?:-\d{4})?$")
WHY = {"build": "Before Apply changes", "full": "Before an export", "draft": "Before a draft export",
       "refetch": "Before Better footage", "back": "Before going back to an earlier version"}


def snapshot(job: Path, why: str) -> None:
    """Keep a copy of the edit's choices, so a run or a bad idea can be undone later. The last 20 are kept."""
    now = {f: (job / f).read_bytes() for f in CHOICES if (job / f).exists()}
    past = sorted(p for p in (job / "history").glob("*") if p.is_dir() and _STAMP.fullmatch(p.name))
    if past and {f: (past[-1] / f).read_bytes() for f in CHOICES if (past[-1] / f).exists()} == now:
        return   # nothing has changed since the last copy
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = job / "history" / stamp
    suffix = 0
    while True:
        try:
            dest.mkdir(parents=True)
            break
        except FileExistsError:
            suffix += 1
            dest = job / "history" / f"{stamp}-{suffix:04d}"
    for f, data in now.items():
        (dest / f).write_bytes(data)
    (dest / "why.txt").write_text(why, encoding="utf-8")
    for old in past[:-19]:
        shutil.rmtree(old, ignore_errors=True)


def history(job: Path) -> dict:
    rows = []
    for p in sorted((p for p in (job / "history").glob("*") if p.is_dir() and _STAMP.fullmatch(p.name)), reverse=True):
        why = (p / "why.txt").read_text(encoding="utf-8") if (p / "why.txt").exists() else ""
        rows.append({"id": p.name, "when": time.mktime(time.strptime(p.name[:15], "%Y%m%d-%H%M%S")), "why": why})
    return {"versions": rows}


def go_back(job: Path, stamp: str) -> dict:
    """Put an earlier version of the edit's choices back. The current ones are kept as a version first."""
    old = job / "history" / stamp
    if not _STAMP.fullmatch(stamp) or not old.is_dir():
        raise ValueError("that version is no longer kept")
    with _lock:
        if _busy(job):
            raise ValueError("Wait for the current run to finish first.")
        restored = {f: (old / f).read_bytes() for f in CHOICES if (old / f).exists()}
        snapshot(job, WHY["back"])
        speech = read_json(job / "sources.json").get("speech", [])
        for f in CHOICES:
            if f == "quote.json" and f in restored and json.loads(restored[f]).get("files") != speech:
                continue   # the speech clips have changed since: that quote points at clips that are not here now
            if f in restored:
                (job / f).write_bytes(restored[f])
            elif f in ("curate.json", "timeline.json"):   # that version had none: the beat cut again
                (job / f).unlink(missing_ok=True)
    return _with_export(job, editor.state(job))


def _with_export(job: Path, state: dict) -> dict:
    """Say whether the exported video still shows the edit as it now stands."""
    out = output_of(job)
    if out:
        newest = max(((job / f).stat().st_mtime for f in (*CHOICES, "sources.json") if (job / f).exists()), default=0)
        state["built"]["exportOld"] = out.stat().st_mtime < newest - 1
    return state


def restore_footage(job: Path) -> dict:
    with _lock:
        if _busy(job):
            return {"slug": job.name, "already": True}
        _launch(job.name, [sys.executable, "-u", "-m", "easyedit.clips", job.name, "restore"])
    return {"slug": job.name, "started": True}


UPLOADS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".opus",
           ".jpg", ".jpeg", ".png", ".webp"}


def rebuild_job(job: Path, mode: str) -> dict:
    """Rebuild a job from the choices saved by the editor: cut only, draft render, or full render."""
    with _lock:
        if mode not in ("build", "draft", "full", "refetch"):
            raise ValueError(f"unknown mode: {mode}")
        run = _runs.get(job.name)
        if run and run["proc"].poll() is None:
            return {"slug": job.name, "already": True}
        argv = editor.run_argv(job, mode)
        snapshot(job, WHY[mode])
        _launch(job.name, argv)
    return {"slug": job.name, "started": True}


def _busy(job: Path) -> bool:
    run = _runs.get(job.name)
    return bool(run and run["proc"].poll() is None)


def add_clip(job: Path, item: dict, role: str) -> dict:
    """Bring a clip into the edit in the background, like any other run: the page follows its log."""
    if role not in clips.ROLES or not isinstance(item, dict):
        raise ValueError("a clip is added for the shots, for the speech, or as the song")
    with _lock:
        if _busy(job):
            return {"slug": job.name, "already": True}
        _launch(job.name, [sys.executable, "-u", "-m", "easyedit.clips", job.name, role, json.dumps(item)])
    return {"slug": job.name, "started": True}


def remove_clip(job: Path, path: str, role: str, delete: bool = False) -> dict:
    with _lock:
        if _busy(job):
            raise ValueError("Wait for the current run to finish, then remove the clip.")
        return clips.remove(job, path, role, delete)


def preview_file(job: Path, name: str) -> Path | None:
    """The files the editor's live preview loads: the built footage plus the current film layer."""
    if name in ("player.html", "live.html"):
        return WEB / name
    if name == "film.js":
        return ROOT / "template" / "film.js"
    if name in ("edit.js", "footage.mp4", "depth.mp4"):
        return job / "render" / name
    if name == "soundtrack.m4a":
        return job / "work" / name
    if name.startswith("fonts/"):
        return font_file(name.split("/", 1)[1])
    return None


# the interface's own type (design.md): only the pages use these, the film layer does not
UI_FONTS = {
    "Geist.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/geist/Geist%5Bwght%5D.ttf",
    "GeistMono.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/geistmono/GeistMono%5Bwght%5D.ttf",
}


def font_file(name: str) -> Path | None:
    """One of the two fonts the film layer uses, downloaded once. The editor page uses them too."""
    from .render import FONTS
    urls = {**FONTS, **UI_FONTS}
    if name not in urls:
        return None
    font = CACHE / "fonts" / name
    if not font.exists():
        import urllib.request
        font.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(urls[name], font)
    return font


def stop_job(slug: str) -> bool:
    run = _runs.get(slug)
    if not run or run["proc"].poll() is not None:
        return False
    # kill the pipeline and any hyperframes renderers it spawned
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(run["proc"].pid)], capture_output=True)
    else:
        run["proc"].terminate()
    run["stopped"] = True
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "easyedit"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data).encode(), "application/json")

    def _file(self, path: Path):
        """Serve a file, honouring Range so the video element can seek."""
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if m and m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            elif m and m.group(2) and int(m.group(2)) > 0:
                start = max(0, size - int(m.group(2)))
            else:
                start = size
            if start >= size or start > end:
                return self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
        length = max(0, end - start + 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(256 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def _relay(self, url: str, referer: str):
        """Pass a clip through from its site, so the viewer can play it before it is added."""
        req = clips._request(url, referer)
        if self.headers.get("Range"):
            req.add_header("Range", self.headers["Range"])
        try:
            up = clips._open(req, 30)
        except urllib.error.HTTPError as e:
            return self._send(e.code, b"", "text/plain")
        except OSError:
            return self._send(502, b"the site did not answer", "text/plain")
        with up:
            self.send_response(up.status)
            for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if up.headers.get(name):
                    self.send_header(name, up.headers[name])
            self.end_headers()
            if self.command == "HEAD":
                return
            while chunk := up.read(256 * 1024):
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8",
                              {"Cache-Control": "no-store"})
        if path.startswith("/assets/"):
            target = (WEB / path.lstrip("/")).resolve()
            if target.is_file() and (WEB / "assets").resolve() in target.parents:
                return self._file(target)
            return self._send(404, b"not found", "text/plain")
        if path.startswith("/fonts/"):
            font = font_file(path[len("/fonts/"):])
            return self._file(font) if font else self._send(404, b"not found", "text/plain")
        if path == "/favicon.ico":
            return self._file(WEB / "assets" / "icon.svg")
        if path == "/api/setup":
            return self._json(setup_report(force="refresh" in (urlparse(self.path).query or "")))
        if path == "/api/jobs":
            return self._json({"jobs": [job_row(j) for j in job_dirs()]})
        if path.startswith("/api/log/"):
            job = safe_job(path[len("/api/log/"):])
            if not job or not job.is_dir():
                return self._json({"error": "unknown edit"}, 404)
            log = job / "run.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            return self._json({"log": text[-8000:], **run_status(job.name, log)})
        if path.startswith("/editor/"):
            job = safe_job(path[len("/editor/"):])
            if not job or not job.is_dir():
                return self._send(404, b"unknown edit", "text/plain")
            return self._send(200, (WEB / "editor.html").read_bytes(), "text/html; charset=utf-8",
                              {"Cache-Control": "no-store"})
        if path.startswith("/render/"):
            rel = path[len("/render/"):].split("/", 1)
            job = safe_job(rel[0]) if len(rel) == 2 else None
            target = preview_file(job, rel[1]) if job and job.is_dir() else None
            if not target or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            if target.suffix in (".html", ".js"):
                return self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "text/plain",
                                  {"Cache-Control": "no-store"})
            return self._file(target)
        if path.startswith("/api/edit/"):
            return self._edit_get(path[len("/api/edit/"):], parse_qs(urlparse(self.path).query))
        if path.startswith("/api/footage/"):
            return self._footage_get(path[len("/api/footage/"):], parse_qs(urlparse(self.path).query))
        if path.startswith("/footage/file/"):
            target = finder.saved_file(path[len("/footage/file/"):])
            return self._file(target) if target else self._send(404, b"not found", "text/plain")
        if path.startswith("/thumb/"):
            job = safe_job(path[len("/thumb/"):].removesuffix(".jpg"))
            thumb = thumb_of(job) if job and job.is_dir() else None
            if not thumb:
                return self._send(404, b"no thumbnail", "text/plain")
            return self._file(thumb)
        if path.startswith("/media/"):
            rel = path[len("/media/"):].split("/", 1)
            job = safe_job(rel[0]) if len(rel) == 2 else None
            name = Path(rel[1]).name if len(rel) == 2 else ""
            target = (job / name).resolve() if job and name else None
            if target and target.is_file() and job in target.parents:
                return self._file(target)
            return self._send(404, b"not found", "text/plain")
        return self._send(404, b"not found", "text/plain")

    def _edit_get(self, rest: str, query: dict):
        slug, _, what = rest.partition("/")
        job = safe_job(slug)
        if not job or not job.is_dir():
            return self._json({"error": "unknown edit"}, 404)
        try:
            if not what:
                return self._json(_with_export(job, editor.state(job)))
            if what == "history":
                return self._json(history(job))
            if what == "wave":
                return self._json(editor.wave(job, query.get("src", [""])[0] or None))
            if what == "media":   # every source as the live preview plays it
                from . import sequence
                return self._json(sequence.media(job))
            if what == "clips":
                return self._json(clips.library(job))
            if what == "anime/search":
                return self._json({"results": anime.lookup(query.get("q", [""])[0])})
            if what == "clips/peek":
                found = clips.peek_url(query.get("id", [""])[0])
                return self._relay(*found) if found else self._send(404, b"not found", "text/plain")
            if what == "clips/search":
                return self._json(clips.search(job, query.get("site", [""])[0], query.get("q", [""])[0]))
            kind, index = query.get("k", [""])[0], int(query.get("i", ["0"])[0])
            if what == "thumb":
                found = editor.thumb(job, kind, index, float(query.get("t", ["0"])[0]))
            elif what == "source":
                found = editor.source(job, kind, index, audio=query.get("audio", [""])[0] == "1",
                                      proxy=query.get("proxy", [""])[0] == "1")
            else:
                found = None
        except Exception as e:   # a project with a broken file says so, rather than dropping the connection
            return self._json({"error": str(e) or e.__class__.__name__}, 400)
        if not found:
            return self._send(404, b"not found", "text/plain")
        return self._file(found)

    def _footage_get(self, what: str, query: dict):
        one = lambda k, d="": query.get(k, [d])[0]  # noqa: E731
        try:
            if what == "search":
                return self._json(finder.search(one("q"), one("kind"), json.loads(one("pick") or "null"),
                                                int(one("seed", "0")), int(one("len", "12"))))
            if what == "probe":
                return self._json(finder.probe(one("id")))
            if what == "saved":
                return self._json(finder.saved())
        except Exception as e:  # a site that is down or has changed must not take the page with it
            return self._json({"error": str(e)[:300]}, 400)
        return self._json({"error": "unknown endpoint"}, 404)

    def _footage_post(self, what: str, body: dict):
        if what == "save":
            return self._json(finder.save(str(body.get("subject") or ""), body.get("clip")))
        if what == "forget":
            return self._json(finder.forget(str(body.get("file") or "")))
        if what == "reveal":
            target = finder.saved_file(str(body.get("file") or ""))
            finder.LIBRARY.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(target)] if target else ["explorer", str(finder.LIBRARY)])
            return self._json({"ok": True})
        return self._json({"error": "unknown endpoint"}, 404)

    def _edit_post(self, rest: str, body: dict):
        slug, _, what = rest.partition("/")
        job = safe_job(slug)
        if not job or not job.is_dir():
            return self._json({"error": "unknown edit"}, 404)
        if what == "save":
            with _lock:
                if _busy(job):
                    return self._json({"error": "Wait for the current run to finish first."}, 409)
                return self._json(_with_export(job, editor.save(job, body)))
        if what == "history/back":
            return self._json(go_back(job, str(body.get("id", ""))))
        if what == "clips/restore":
            return self._json(restore_footage(job))
        if what == "clips/discard":
            with _lock:
                if _busy(job):
                    return self._json({"error": "Wait for the current run to finish first."}, 409)
                return self._json(clips.discard(job, str(body.get("path", ""))))
        if what == "requote":
            return self._json(editor.requote(job, int(body.get("clip", 0)), int(body["first"]), int(body["last"]),
                                             body.get("keep") or {}))
        if what == "run":
            return self._json(rebuild_job(job, str(body.get("mode", "build"))))
        if what == "seq/auto":   # cut it to the beat again, as a sequence the page can undo
            return self._json({"seq": editor.auto_seq(job, body)})
        if what == "seq/music":
            return self._json({"clip": editor.music_seq(job, body)})
        if what == "ask":
            return self._json(editor.ask(job, str(body.get("request", ""))))
        if what == "clips/add":
            return self._json(add_clip(job, body.get("item"), str(body.get("role", ""))))
        if what == "anime/set":   # link this edit to an anime (or to none), so the footage sites know what to look for
            linked = anime.by_id(int(body["id"])) if body.get("id") else None
            with _lock:
                if _busy(job):
                    return self._json({"error": "Wait for the current run to finish first."}, 409)
                anime.link(job, linked)
                return self._json(clips.library(job))
        if what == "clips/remove":
            return self._json(remove_clip(job, str(body.get("path", "")), str(body.get("role", "")),
                                          body.get("delete") is True))
        return self._json({"error": "unknown endpoint"}, 404)

    def _upload(self, slug: str, length: int):
        """Take a file dropped on the editor into the project's own folder."""
        job = safe_job(slug)
        name = re.sub(r"[^\w .()\[\]-]+", "_", Path(parse_qs(urlparse(self.path).query).get("name", [""])[0]).name).strip(" .")
        if name:
            name = Path(name).stem[:100] + Path(name).suffix.lower()
        if Path(name).stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            name = "_" + name
        problem = None
        if not job or not job.is_dir():
            problem = "unknown edit"
        elif Path(name).suffix.lower() not in UPLOADS:
            problem = (f"easyedit cannot use {Path(name).suffix or 'that kind of'} files. Use a video (mp4, mov, mkv, webm), "
                       "a song (mp3, m4a, wav) or a picture (jpg, png, webp).")
        elif not length:
            problem = "that file is empty"
        elif shutil.disk_usage(job).free < length + 2 * 2**30:
            problem = "There is not enough free space on this drive for that file."
        if problem:
            self.close_connection = True   # the body was not read, so this connection cannot be reused
            return self._json({"error": problem}, 400)
        folder = job / "downloads" / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        part = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix="upload-", suffix=".part", delete=False) as fh:
                part, left = Path(fh.name), length
                while left > 0:
                    chunk = self.rfile.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    fh.write(chunk)
                    left -= len(chunk)
            if left:
                return self._json({"error": "the file did not arrive whole; try again"}, 400)
            with _lock:
                dest = folder / name
                if dest.exists():
                    dest = folder / f"{Path(name).stem}-{part.stem.removeprefix('upload-')}{Path(name).suffix}"
                part.replace(dest)
            return self._json({"path": str(dest), "name": dest.name})
        finally:
            if part is not None:
                part.unlink(missing_ok=True)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                raise ValueError
        except ValueError:
            self.close_connection = True
            return self._json({"error": "invalid Content-Length"}, 400)
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self._json({"error": "send a Content-Length with the upload"}, 400)
        if path.startswith("/api/edit/") and path.endswith("/clips/upload"):
            try:
                return self._upload(path[len("/api/edit/"):-len("/clips/upload")], length)
            except (OSError, ValueError) as e:
                self.close_connection = True
                return self._json({"error": str(e)}, 400)
        if length > 26 * 1024 * 1024:
            self.close_connection = True
            return self._json({"error": "request is too large"}, 413)
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            self.close_connection = True
            return self._json({"error": "the request did not arrive whole; try again"}, 400)
        if path == "/api/design":
            try:
                from .design import design_picture
                png = design_picture(picture_bytes(raw, self.headers.get("Content-Type", "")))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._send(200, png, "image/png", {"Cache-Control": "no-store"})
        try:
            body = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json({"error": "invalid JSON"}, 400)
        if not isinstance(body, dict):
            return self._json({"error": "JSON body must be an object"}, 400)
        try:
            if path.startswith("/api/edit/"):
                return self._edit_post(path[len("/api/edit/"):], body)
            if path.startswith("/api/footage/"):
                return self._footage_post(path[len("/api/footage/"):], body)
            if path == "/api/new":
                return self._json(start_job(body))
            if path == "/api/connect":
                return self._json(connect(body.get("provider", "")))
            if path == "/api/stop":
                job = safe_job(body.get("slug", ""))
                return self._json({"stopped": bool(job) and stop_job(job.name)})
            if path == "/api/reveal":
                job = safe_job(body.get("slug", ""))
                if job and job.is_dir() and os.name == "nt":
                    subprocess.Popen(["explorer", str(job)])
                return self._json({"ok": bool(job and job.is_dir())})
            if path == "/api/delete":
                job = safe_job(body.get("slug", ""))
                with _lock:
                    if job and job.is_dir():
                        stop_job(job.name)
                        run = _runs.get(job.name)
                        if run:
                            try:
                                run["proc"].wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                return self._json({"error": "The run is still stopping. Try deleting it again in a moment."}, 409)
                            run["fh"].close()
                        shutil.rmtree(job)
                        _runs.pop(job.name, None)
                        return self._json({"deleted": True})
                return self._json({"deleted": False}, 400)
        except ApiError as e:
            return self._json({"error": str(e), **e.details}, e.code)
        except Exception as e:
            return self._json({"error": str(e)}, 400)
        return self._json({"error": "unknown endpoint"}, 404)


def main() -> None:
    if not (WEB / "index.html").exists():
        raise SystemExit(f"missing {WEB / 'index.html'}")
    ThreadingHTTPServer.request_queue_size = 128  # the editor asks for dozens of thumbnails at once
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"easyedit web: {url}", flush=True)
    setup_report()
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
