"""Subject mattes for the cut footage: render/matte.mp4, one grey frame per footage frame (white = subject).

Depth says how far away things are; a matte says what the subject is, whatever stands beside it.
The film layer reads it as a texture for the subject effects (outline, aura, echo, isolate, subject pop,
captions behind the subject) and to remove, blur or replace the background.

    python -m easyedit.matte <job folder> [anime]

Runs as its own process so it can use a GPU build of onnxruntime kept in .cache/.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import models
from .util import log, probe

IN = 1024                  # the matting model's square input
OUT_W, OUT_H = 640, 360
KEY_FPS = 15               # the subject is read this often and eased in between; never across a cut


def fresh(job: Path) -> bool:
    matte, footage = job / "render" / "matte.mp4", job / "render" / "footage.mp4"
    return matte.exists() and footage.exists() and matte.stat().st_mtime >= footage.stat().st_mtime


def ensure(job: Path, anime: bool = False) -> bool:
    """Build render/matte.mp4 if the footage is newer than it. True when a matte is ready."""
    if fresh(job):
        log("matte: reusing subject matte")
        return True
    argv = [sys.executable, "-u", "-m", "easyedit.matte", str(job)] + (["anime"] if anime else [])
    code = subprocess.run(argv, env=models.gpu_env("matte")).returncode
    if code != 0:
        log("matte: failed; the edit renders without the subject matte")
    return code == 0 and fresh(job)


def build(job: Path, anime: bool = False) -> Path:
    import numpy as np

    render = job / "render"
    footage = render / "footage.mp4"
    edit = json.loads((render / "edit.js").read_text(encoding="utf-8").removeprefix("window.EDIT=").rstrip().rstrip(";"))
    fps, total = edit["fps"], edit["frames"]
    # never ease a matte across a cut: the speech and every shot are read on their own
    segments = [(0, edit["montageStart"])] + [(s["start"], s["end"]) for s in edit["shots"]]
    step = fps / KEY_FPS
    keys_of = []
    for a, b in segments:
        ks = sorted({min(b - 1, a + round(k * step)) for k in range(int((b - a) / step) + 2)} | {b - 1})
        keys_of.append(ks)
    wanted = {k for ks in keys_of for k in ks}

    session = models.session(models.get("matte-anime" if anime else "matte", "matte"))
    feed = session.get_inputs()[0].name
    log(f"matte: {len(wanted)} subject frames for {total} video frames on {session.get_providers()[0].replace('ExecutionProvider', '')}")

    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(footage), "-vf", f"scale={IN}:{IN}",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size, matte, done = IN * IN * 3, {}, 0
    for n in range(total):
        raw = reader.stdout.read(size)
        if len(raw) < size:
            break
        if n not in wanted:
            continue
        x = np.frombuffer(raw, np.uint8).reshape(IN, IN, 3).astype(np.float32) / 255 - 0.5
        m = session.run(None, {feed: x.transpose(2, 0, 1)[None]})[0][0, 0]
        matte[n] = m[::2, ::2].astype(np.float32)   # half size is plenty for a 640 px wide matte
        done += 1
        if done % 25 == 0:
            log(f"matte: {done}/{len(wanted)}")
    reader.stdout.close()
    reader.wait()

    half = IN // 2
    out = render / "matte.mp4"
    tmp = render / "matte.part.mp4"
    writer = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{half}x{half}",
                               "-r", str(fps), "-i", "-", "-vf", f"scale={OUT_W}:{OUT_H}:flags=bicubic", "-c:v", "libx264",
                               "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-g", str(fps), str(tmp)],
                              stdin=subprocess.PIPE)
    flat = np.zeros((half, half), np.uint8)
    for (a, b), ks in zip(segments, keys_of):
        ks = [k for k in ks if k in matte]
        if not ks:
            for _ in range(a, b):
                writer.stdin.write(flat.tobytes())
            continue
        # one level for the whole shot; a shot with no clear subject stays dark instead of turning to noise
        lo, hi = 0.0, max(float(np.percentile(np.stack([matte[k] for k in ks]), 99.5)), 0.35)
        scale = 255.0 / (hi - lo)
        j = 0
        for f in range(a, b):
            while j + 1 < len(ks) - 1 and ks[j + 1] <= f:
                j += 1
            k0, k1 = ks[j], ks[min(j + 1, len(ks) - 1)]
            w = 0.0 if k1 == k0 else min(1.0, max(0.0, (f - k0) / (k1 - k0)))
            m = matte[k0] * (1 - w) + matte[k1] * w
            writer.stdin.write(np.clip((m - lo) * scale, 0, 255).astype(np.uint8).tobytes())
    writer.stdin.close()
    if writer.wait() != 0:
        raise SystemExit("matte: could not write matte.mp4")
    tmp.replace(out)
    log(f"matte: done ({probe(out)['duration']:.2f}s)")
    return out


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    build(Path(sys.argv[1]), "anime" in sys.argv[2:])
