"""Render the HyperFrames composition in sections, then mux the soundtrack."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from .util import CACHE, ROOT, log, probe, run

FONTS = {
    "Montserrat.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
    "Anton.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
}
CLI = ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
PARALLEL = int(os.environ.get("EASYEDIT_PARALLEL", "2"))


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


def encoder() -> list[str]:
    """NVENC when the GPU has it (seconds instead of minutes), else x264."""
    if os.environ.get("EASYEDIT_ENCODER") == "x264":
        return ["-c:v", "libx264", "-preset", "slow", "-crf", "19"]
    try:
        codecs = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                                text=True, timeout=30).stdout
        if "h264_nvenc" in codecs:
            return ["-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "25", "-b:v", "0"]
    except Exception:
        pass
    return ["-c:v", "libx264", "-preset", "slow", "-crf", "19"]


def section_html(render_dir: Path, start: float, dur: float, fps: int, name: str) -> None:
    html = (ROOT / "template" / "index.html").read_text(encoding="utf-8")
    html = (html.replace("__DURATION__", f"{dur:.4f}").replace("__FPS__", str(fps))
            .replace("__MEDIA_START__", f"{start:.4f}").replace("__SEGMENT_START__", f"{start:.4f}"))
    (render_dir / name).write_text(html, encoding="utf-8")


def render(job: Path, edit: dict, out: Path, quality: str = "high",
           only: tuple[float, float] | None = None) -> Path:
    """Split the timeline into PARALLEL sections and render them concurrently.

    Each hyperframes process runs in low-memory mode: one Chrome, frames streamed straight into
    the encoder. Nothing large touches the temp dir (disk capture needs ~9 MB per 1080p frame),
    and running sections side by side gets the parallelism back."""
    if not CLI.exists():
        raise SystemExit("hyperframes not installed: run `npm install` in the easyedit folder")
    rd = job / "render"
    prepare(rd)
    fps, frames = edit["fps"], edit["frames"]
    a0, b0 = (int(only[0] * fps), min(frames, int(only[1] * fps))) if only else (0, frames)
    n = max(1, min(PARALLEL, (b0 - a0) // (fps * 2)))
    marks = [a0 + round(i * (b0 - a0) / n) for i in range(n + 1)]
    section_html(rd, 0, frames / fps, fps, "index.html")  # full composition for `hyperframes preview`
    work = job / "work" / "sections"
    work.mkdir(parents=True, exist_ok=True)
    fresh_after = max((rd / "edit.js").stat().st_mtime, (ROOT / "template" / "film.js").stat().st_mtime)
    env = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1"}
    parts, jobs = [], []
    for k, (a, b) in enumerate(zip(marks, marks[1:])):
        part = work / f"section-{a}-{b}.mp4"
        parts.append(part)
        if part.exists() and part.stat().st_mtime > fresh_after:
            log(f"render: reuse section {a / fps:.1f}-{b / fps:.1f}s")
            continue
        name = f"section-{a}-{b}.html"
        section_html(rd, a / fps, (b - a) / fps, fps, name)
        logf = work / f"section-{a}-{b}.log"
        cmd = ["node", str(CLI), "render", ".", "--composition", name, "--fps", str(fps), "--quality", quality,
               "--low-memory-mode", "--workers", "1", "--frames-cache-dir", str(work / f"cache-{k}"),
               "--output", str(part), "--quiet"]
        fh = logf.open("w", encoding="utf-8")
        jobs.append((subprocess.Popen(cmd, cwd=rd, env=env, stdout=fh, stderr=subprocess.STDOUT),
                     fh, logf, part, name, k))
    if jobs:
        log(f"render: {len(jobs)} section(s) in parallel, {(b0 - a0)} frames @ {fps}fps")
    t0 = time.time()
    failed = None
    for proc, fh, logf, part, name, k in jobs:
        code = proc.wait()
        fh.close()
        shutil.rmtree(work / f"cache-{k}", ignore_errors=True)
        (rd / name).unlink(missing_ok=True)
        if code != 0:
            part.unlink(missing_ok=True)
            failed = failed or logf.read_text(encoding="utf-8", errors="replace")[-1500:]
    if failed:
        raise SystemExit(f"hyperframes render failed:\n{failed}")
    if jobs:
        log(f"render: frames done in {time.time() - t0:.0f}s ({(b0 - a0) / (time.time() - t0):.1f} fps)")
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    sound = job / "work" / "soundtrack.m4a"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
           "-i", sound, "-map", "0:v", "-map", "1:a", "-shortest", "-c:a", "aac", "-b:a", "256k"]
    if only:
        cmd += ["-af", f"atrim=start={a0 / fps:.3f},asetpts=PTS-STARTPTS"]
    # the section files are near-lossless; re-encode once for a shareable file
    cmd += encoder() + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    run(cmd)
    log(f"done: {out} ({probe(out)['duration']:.2f}s)")
    return out
