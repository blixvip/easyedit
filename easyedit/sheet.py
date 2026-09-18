"""Contact sheets so a person or a bot can see an edit before and after rendering.

    python -m easyedit.sheet "Obsession"            # candidates + footage sheets
    python -m easyedit.sheet "Obsession" --final    # stills from the finished video
    python -m easyedit.sheet "Obsession" --report   # text QA report + draft curate.json (no images)

Writes to jobs/<movie>/qa/:
  report.txt      text summary of the quote and the shot pool (for agents, cheap to read)
  suggest-curate.json  a ready-to-copy curate.json draft (pin list, excludes, hero)
  candidates.jpg  every montage shot, labelled with its number (ids in candidates.txt, for curate.json)
  footage.jpg     the assembled footage before effects (speech, montage in order, hero)
  final.jpg       stills from the rendered video
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from . import shots
from .util import JOBS, probe, read_json, slugify

TW, TH, COLS = 320, 180, 6


def _grab(src: Path, t: float, crop=None) -> np.ndarray | None:
    vf = (f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else "") + \
         f"scale={TW}:{TH}:force_original_aspect_ratio=decrease,pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2"
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
                        "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
    if len(p.stdout) != TW * TH * 3:
        return None
    return np.frombuffer(p.stdout, np.uint8).reshape(TH, TW, 3).copy()


def _label(img: np.ndarray, text: str) -> np.ndarray:
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.rectangle(img, (0, 0), (w + 12, h + 12), (0, 0, 0), -1)
    cv2.putText(img, text, (6, h + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (122, 226, 255), 2, cv2.LINE_AA)
    return img


def _tile(frames: list[np.ndarray], out: Path) -> Path:
    rows = (len(frames) + COLS - 1) // COLS
    sheet = np.zeros((rows * TH, COLS * TW, 3), np.uint8)
    for i, f in enumerate(frames):
        r, c = divmod(i, COLS)
        sheet[r * TH:(r + 1) * TH, c * TW:(c + 1) * TW] = f
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out


def candidates(job: Path) -> Path | None:
    analyses = [read_json(f) for f in sorted((job / "work").glob("shots-*.json"))]
    if not analyses:
        return None
    pool = shots.rank(analyses)
    frames, lines = [], []
    for i, s in enumerate(pool):
        a = analyses[s["src"]]
        img = _grab(Path(a["source"]), (s["start"] + s["end"]) / 2, a.get("crop"))
        if img is None:
            continue
        frames.append(_label(img, str(i)))
        lines.append(f"{i:3d}  {shots.shot_id(analyses, s):24s} {s['end'] - s['start']:5.2f}s"
                     f"  score {s['score']:+.2f}  face {'yes' if s['face'] else 'no'}")
    (job / "qa" / "candidates.txt").parent.mkdir(parents=True, exist_ok=True)
    (job / "qa" / "candidates.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _tile(frames, job / "qa" / "candidates.jpg")


def report(job: Path) -> Path:
    """Text-only QA: the quote, the shot pool with flags, and a draft curate.json.

    Cheap for an agent to read (no vision tokens); look at the .jpg sheets only if the
    text leaves doubt about specific shots."""
    lines = []
    qfile = job / "quote.json"
    if qfile.exists():
        q = read_json(qfile)
        src = q.get("source", "?")
        lines.append(f"QUOTE ({src}): {q.get('reason', '')}")
        if src == "heuristic":
            lines.append("  !! AI writer unavailable - check this passage is the iconic one")
        for ln in q.get("lines", []):
            words = " ".join(w["text"] + (f"[{w['role'][0].upper()}]" if w.get("role") else "")
                             for w in ln)
            roles = sum(1 for w in ln if w.get("role"))
            lines.append(f"  {words}   ({roles}/{len(ln)} emphasized)")
    else:
        lines.append("QUOTE: no quote.json yet")

    analyses = [read_json(f) for f in sorted((job / "work").glob("shots-*.json"))]
    if not analyses:
        return _write_report(job, lines + ["SHOTS: no analysis yet"])
    pool = shots.rank(analyses)
    n_need = 24  # shots the default 21s montage + spares
    flags: dict[int, list[str]] = {}
    picked: list[dict] = []
    for i, s in enumerate(pool):
        f = []
        if s.get("gray", 0) > 0.45:
            f.append("title-card?")
        if s["end"] - s["start"] < 1.0:
            f.append("short")
        if any(shots._similar(s, t) for t in picked[:n_need]):
            f.append("repeat")
        flags[i] = f
        if not f and len(picked) < n_need:
            picked.append(s)

    lines.append(f"SHOTS: {len(pool)} candidates, top {min(n_need, len(picked))} clean ones marked *")
    lines.append("idx  id                        len   score face  flags")
    for i, s in enumerate(pool):
        mark = "*" if s in picked else ("x" if flags[i] else " ")
        lines.append(f"{i:3d}{mark} {shots.shot_id(analyses, s):24s} {s['end'] - s['start']:5.2f}"
                     f" {s['score']:+6.2f} {'Y' if s.get('face') else '-':4s} {','.join(flags[i])}")

    hero = next((s for s in picked if s.get("face") and s["end"] - s["start"] >= 3.4), None) \
        or next((s for s in picked if s["end"] - s["start"] >= 3.4), None)
    draft = {"pin": [shots.shot_id(analyses, s) for s in picked],
             "exclude": [shots.shot_id(analyses, s) for i, s in enumerate(pool)
                         if flags[i] and "repeat" not in flags[i]][:20],
             "hero": shots.shot_id(analyses, hero) if hero else None}
    if hero:
        lines.append(f"HERO suggestion: {draft['hero']} (face, {hero['end'] - hero['start']:.1f}s)")
    lines.append("Copy qa/suggest-curate.json to curate.json if the draft looks right,"
                 " or hand-edit it - then rebuild.")
    out = _write_report(job, lines)
    (job / "qa" / "suggest-curate.json").write_text(json.dumps(draft, indent=2), encoding="utf-8")
    return out


def _write_report(job: Path, lines: list[str]) -> Path:
    out = job / "qa" / "report.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def video_sheet(video: Path, out: Path, n: int = 36) -> Path | None:
    if not video.exists():
        return None
    dur = probe(video)["duration"]
    frames = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        img = _grab(video, t)
        if img is not None:
            frames.append(_label(img, f"{t:.1f}s"))
    return _tile(frames, out)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    job = JOBS / slugify(args[0])
    if not job.exists():
        raise SystemExit(f"no job at {job}")
    made = []
    if "--final" in sys.argv:
        vids = sorted((p for p in job.glob("*.mp4") if "preview" not in p.name), key=lambda p: p.stat().st_mtime)
        if vids:
            made.append(video_sheet(vids[-1], job / "qa" / "final.jpg", 24))
    elif "--report" in sys.argv:
        made.append(report(job))
    else:
        made.append(report(job))
        made.append(candidates(job))
        made.append(video_sheet(job / "render" / "footage.mp4", job / "qa" / "footage.jpg"))
    for m in filter(None, made):
        print(m)


if __name__ == "__main__":
    main()
