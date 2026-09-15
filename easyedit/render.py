"""Render the HyperFrames composition in sections, then mux the soundtrack."""
from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path

from .util import CACHE, ROOT, log, probe, run

FONTS = {
    "Montserrat.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
    "Anton.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
}
CLI = ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
SECTION = 16.0


def ensure_fonts(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, url in FONTS.items():
        cached = CACHE / "fonts" / name
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            log(f"downloading font {name}")
            urllib.request.urlretrieve(url, cached)
        shutil.copy2(cached, dest / name)


def prepare(render_dir: Path) -> None:
    ensure_fonts(render_dir / "fonts")
    shutil.copy2(ROOT / "template" / "film.js", render_dir / "film.js")


def section_html(render_dir: Path, start: float, dur: float, fps: int, name: str) -> None:
    html = (ROOT / "template" / "index.html").read_text(encoding="utf-8")
    html = (html.replace("__DURATION__", f"{dur:.4f}").replace("__FPS__", str(fps))
            .replace("__MEDIA_START__", f"{start:.4f}").replace("__SEGMENT_START__", f"{start:.4f}"))
    (render_dir / name).write_text(html, encoding="utf-8")


def render(job: Path, edit: dict, out: Path, quality: str = "high", workers: str = os.environ.get("EASYEDIT_WORKERS", "4"),
           only: tuple[float, float] | None = None) -> Path:
    if not CLI.exists():
        raise SystemExit("hyperframes not installed: run `npm install` in the easyedit folder")
    rd = job / "render"
    prepare(rd)
    fps, frames = edit["fps"], edit["frames"]
    total = frames / fps
    # section boundaries on whole frames
    per = int(SECTION * fps)
    marks = list(range(0, frames, per)) + [frames]
    if only:
        a, b = int(only[0] * fps), min(frames, int(only[1] * fps))
        marks = [a, b]
    section_html(rd, 0, total, fps, "index.html")  # full composition, handy for `hyperframes preview`
    # screenshot capture keeps 4 parallel workers; the experimental drawElement path pins to 1
    env = {"PRODUCER_FORCE_SCREENSHOT": "true", **os.environ, "HYPERFRAMES_NO_TELEMETRY": "1"}
    parts = []
    work = job / "work" / "sections"
    work.mkdir(parents=True, exist_ok=True)
    for k, (a, b) in enumerate(zip(marks, marks[1:])):
        name = f"section-{k}.html"
        section_html(rd, a / fps, (b - a) / fps, fps, name)
        part = work / f"section-{k}-{a}-{b}.mp4"
        parts.append(part)
        if part.exists() and part.stat().st_mtime > (rd / "edit.js").stat().st_mtime \
                and part.stat().st_mtime > (ROOT / "template" / "film.js").stat().st_mtime:
            log(f"render: reuse section {k + 1}/{len(marks) - 1}")
            continue
        log(f"render: section {k + 1}/{len(marks) - 1} ({a / fps:.1f}-{b / fps:.1f}s)")
        logf = work / f"section-{k}.log"
        with logf.open("w", encoding="utf-8") as fh:
            try:
                run(["node", CLI, "render", ".", "--composition", name, "--fps", fps, "--quality", quality,
                     "--workers", workers, "--output", part, "--quiet"],
                    cwd=rd, env=env, stdout=fh, stderr=fh)
            except Exception:
                tail = logf.read_text(encoding="utf-8", errors="replace")[-1500:]
                part.unlink(missing_ok=True)
                raise SystemExit(f"hyperframes render failed (section {k}):\n{tail}")
        (rd / name).unlink(missing_ok=True)
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    sound = job / "work" / "soundtrack.m4a"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing]
    if only:
        cmd += ["-ss", "0", "-i", sound, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                "-af", f"atrim=start={only[0]:.3f},asetpts=PTS-STARTPTS", "-shortest"]
    else:
        cmd += ["-i", sound, "-map", "0:v", "-map", "1:a", "-c", "copy", "-shortest"]
    run(cmd + ["-movflags", "+faststart", out])
    log(f"done: {out} ({probe(out)['duration']:.2f}s)")
    return out
