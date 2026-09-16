"""Local web UI: browse finished edits, start new ones, watch progress live.

    python -m easyedit.web          # http://127.0.0.1:4331

Standard library only - no Flask, no build step.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import doctor
from .util import JOBS, ROOT, probe, read_json, slugify

PORT = int(os.environ.get("EASYEDIT_PORT", "4331"))
WEB = Path(__file__).parent / "web"
mimetypes.add_type("image/svg+xml", ".svg")  # missing from the Windows registry on some machines

# log line -> (stage label, fraction of the run that is done once it appears)
STAGES = [
    (re.compile(r"^\s*\[.*?\] job:"), "starting", 0.02),
    (re.compile(r"plan:|sources: reusing"), "planning", 0.06),
    (re.compile(r"download:"), "downloading footage", 0.12),
    (re.compile(r"whisper:"), "transcribing", 0.26),
    (re.compile(r"quote \("), "choosing the quote", 0.34),
    (re.compile(r"shots:.*candidates"), "scoring shots", 0.44),
    (re.compile(r"beats:"), "finding the beat", 0.48),
    (re.compile(r"timeline:"), "cutting to the beat", 0.52),
    (re.compile(r"faces:"), "tracking faces", 0.6),
    (re.compile(r"footage:|build: reusing"), "footage ready", 0.64),
    (re.compile(r"render:.*section"), "rendering", 0.68),
    (re.compile(r"done:"), "done", 1.0),
]

_runs: dict[str, dict] = {}
_lock = threading.Lock()
_setup: dict = {"report": None, "checked": 0.0, "busy": False}


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
    out = output_of(job)
    if not out:
        return None
    thumb = job / "work" / "thumb.jpg"
    if thumb.exists() and thumb.stat().st_mtime >= out.stat().st_mtime:
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", str(out), "-frames:v", "1",
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


def job_info(job: Path) -> dict:
    out = output_of(job)
    plan = read_json(job / "plan.json") if (job / "plan.json").exists() else {}
    run = _runs.get(job.name)
    running = bool(run and run["proc"].poll() is None)
    info = {
        "slug": job.name,
        "title": plan.get("title") or job.name.replace("-", " ").title(),
        "scene": captioned_line(job) or plan.get("speech_scene", ""),
        "music": plan.get("music_query", ""),
        "updated": job.stat().st_mtime,
        "running": running,
        "video": f"/media/{job.name}/{out.name}" if out else None,
        "thumb": f"/thumb/{job.name}.jpg" if out else None,
        "folder": str(job),
    }
    if out:
        try:
            meta = probe(out)
            info["duration"] = round(meta["duration"], 1)
            info["size"] = round(out.stat().st_size / 2**20)
        except Exception:
            pass
    if running:  # a finished job shows its video, not a stale progress bar
        info.update(progress_of(job.name, job / "run.log"))
    return info


def start_job(body: dict) -> dict:
    movie = (body.get("movie") or "").strip()
    if not movie:
        raise ValueError("a movie name is required")
    slug = slugify(movie)
    with _lock:
        run = _runs.get(slug)
        if run and run["proc"].poll() is None:
            return {"slug": slug, "already": True}
        argv = [sys.executable, "-u", "-m", "easyedit", movie]
        for flag in ("speech", "music"):
            if body.get(flag):
                argv += [f"--{flag}", str(body[flag])]
        for m in body.get("montage") or []:
            if m.strip():
                argv += ["--montage", m.strip()]
        for flag, key in (("--fps", "fps"), ("--montage-length", "montage_length"),
                          ("--hero-length", "hero_length"), ("--llm", "llm")):
            if body.get(key):
                argv += [flag, str(body[key])]
        for key, flag in (("draft", "--draft"), ("fresh", "--fresh")):
            if body.get(key):
                argv.append(flag)
        job = JOBS / slug
        job.mkdir(parents=True, exist_ok=True)
        log = job / "run.log"
        fh = log.open("w", encoding="utf-8")
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        _runs[slug] = {"proc": proc, "fh": fh, "started": time.time(), "argv": argv}
    return {"slug": slug, "started": True}


def stop_job(slug: str) -> bool:
    run = _runs.get(slug)
    if not run or run["proc"].poll() is not None:
        return False
    # kill the pipeline and any hyperframes renderers it spawned
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(run["proc"].pid)], capture_output=True)
    else:
        run["proc"].terminate()
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
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
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
        if path == "/favicon.ico":
            return self._file(WEB / "assets" / "icon.svg")
        if path == "/api/setup":
            return self._json(setup_report(force="refresh" in (urlparse(self.path).query or "")))
        if path == "/api/jobs":
            return self._json({"jobs": [job_info(j) for j in job_dirs()]})
        if path.startswith("/api/log/"):
            log = JOBS / path[len("/api/log/"):] / "run.log"
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            slug = path[len("/api/log/"):]
            return self._json({"log": text[-8000:], **progress_of(slug, log),
                               "running": bool(_runs.get(slug) and _runs[slug]["proc"].poll() is None)})
        if path.startswith("/thumb/"):
            job = JOBS / path[len("/thumb/"):].removesuffix(".jpg")
            thumb = thumb_of(job) if job.is_dir() else None
            if not thumb:
                return self._send(404, b"no thumbnail", "text/plain")
            return self._file(thumb)
        if path.startswith("/media/"):
            rel = path[len("/media/"):].split("/", 1)
            if len(rel) == 2:
                target = (JOBS / rel[0] / rel[1]).resolve()
                if target.is_file() and JOBS.resolve() in target.parents:
                    return self._file(target)
            return self._send(404, b"not found", "text/plain")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0)
        body = json.loads(raw or b"{}")
        try:
            if path == "/api/new":
                return self._json(start_job(body))
            if path == "/api/connect":
                return self._json(connect(body.get("provider", "")))
            if path == "/api/stop":
                return self._json({"stopped": stop_job(body.get("slug", ""))})
            if path == "/api/reveal":
                job = JOBS / body.get("slug", "")
                if job.is_dir() and os.name == "nt":
                    subprocess.Popen(["explorer", str(job)])
                return self._json({"ok": job.is_dir()})
            if path == "/api/delete":
                job = (JOBS / body.get("slug", "")).resolve()
                if job.is_dir() and JOBS.resolve() in job.parents:
                    stop_job(job.name)
                    shutil.rmtree(job, ignore_errors=True)
                    return self._json({"deleted": True})
                return self._json({"deleted": False}, 400)
        except Exception as e:
            return self._json({"error": str(e)}, 400)
        return self._json({"error": "unknown endpoint"}, 404)


def main() -> None:
    if not (WEB / "index.html").exists():
        raise SystemExit(f"missing {WEB / 'index.html'}")
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
