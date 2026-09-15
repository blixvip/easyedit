"""Shared helpers: logging, subprocess, ffprobe, paths."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
CACHE = ROOT / ".cache"

_t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - _t0:6.1f}s] {msg}", flush=True)


def die(msg: str) -> "None":
    print(f"\nerror: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "edit"


def need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        die(f"'{binary}' not found on PATH")
    return path


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("check", True)
    return subprocess.run([str(c) for c in cmd], **kw)


def output(cmd: list, **kw) -> str:
    return subprocess.run(
        [str(c) for c in cmd], check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", **kw,
    ).stdout


def probe(path: Path) -> dict:
    data = json.loads(output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,nb_frames",
        "-of", "json", path,
    ]))
    info = {"duration": float(data["format"].get("duration") or 0), "has_audio": False}
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and "width" not in info:
            num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
            info.update(width=int(s["width"]), height=int(s["height"]),
                        fps=float(num) / float(den or 1) if float(den or 1) else 0.0)
        elif s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                               default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                    encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")
