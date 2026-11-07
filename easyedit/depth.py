"""Depth maps for the cut footage: render/depth.mp4, one grey frame per footage frame (white = near).

The film layer reads it as a second texture for background blur, subject pop, parallax and
captions that sit behind the subject.

    python -m easyedit.depth <job folder>

Runs as its own process so it can use a GPU build of onnxruntime kept in .cache/, apart from
the CPU build the rest of the pipeline already has loaded.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from .util import CACHE, log, probe

MODEL_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
IN_W, IN_H = 518, 294      # multiples of the model's 14 px patch, close to 16:9
OUT_W, OUT_H = 640, 360


def _sizes(width: int, height: int) -> tuple[int, int, int, int]:
    """Model input (multiples of 14 px) and depth map size for a canvas of this shape."""
    if height > width:
        return 294, 518, width // 3, height // 3
    if height == width:
        return 406, 406, width // 3, height // 3
    return 518, 294, width // 3, height // 3
KEY_FPS = 12               # depth is read this often and eased in between; never across a cut
GPU_RUNTIME = CACHE / "ort-dml"


def fresh(job: Path) -> bool:
    depth, footage = job / "render" / "depth.mp4", job / "render" / "footage.mp4"
    return depth.exists() and footage.exists() and depth.stat().st_mtime >= footage.stat().st_mtime


def _gpu_runtime() -> Path | None:
    """A DirectML build of onnxruntime in .cache/ (Windows). About five times faster than CPU here."""
    if os.name != "nt":
        return None
    if not (GPU_RUNTIME / "onnxruntime").exists():
        log("depth: getting the GPU runtime (one time, about 70 MB)")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--target",
                            str(GPU_RUNTIME), "onnxruntime-directml"], check=True, timeout=900)
        except (subprocess.SubprocessError, OSError) as e:
            log(f"depth: GPU runtime not installed ({e}); using the CPU, which is slow")
            return None
    return GPU_RUNTIME


def ensure(job: Path) -> bool:
    """Build render/depth.mp4 if the footage is newer than it. True when a depth map is ready."""
    if fresh(job):
        log("depth: reusing depth map")
        return True
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    runtime = _gpu_runtime()
    if runtime:
        env["PYTHONPATH"] = str(runtime) + os.pathsep + env.get("PYTHONPATH", "")
    code = subprocess.run([sys.executable, "-u", "-m", "easyedit.depth", str(job)], env=env).returncode
    if code != 0:
        log("depth: failed; the edit renders without depth effects")
    return code == 0 and fresh(job)


def _model() -> Path:
    path = CACHE / "depth_anything_v2_small.onnx"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log("depth: downloading the depth model (one time, about 100 MB)")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    return path


def build(job: Path) -> Path:
    import numpy as np
    import onnxruntime as ort

    render = job / "render"
    footage = render / "footage.mp4"
    edit = json.loads((render / "edit.js").read_text(encoding="utf-8").removeprefix("window.EDIT=").rstrip().rstrip(";"))
    fps, total = edit["fps"], edit["frames"]
    IN_W, IN_H, OUT_W, OUT_H = _sizes(int(edit.get("width") or 1920), int(edit.get("height") or 1080))
    # never ease depth across a cut: the speech and every shot are read on their own
    segments = [(0, edit["montageStart"])] + [(s["start"], s["end"]) for s in edit["shots"]]
    step = fps / KEY_FPS
    keys_of = []
    for a, b in segments:
        ks = sorted({min(b - 1, a + round(k * step)) for k in range(int((b - a) / step) + 2)} | {b - 1})
        keys_of.append(ks)
    wanted = {k for ks in keys_of for k in ks}

    have = ort.get_available_providers()
    providers = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider") if p in have] + ["CPUExecutionProvider"]
    session = ort.InferenceSession(str(_model()), providers=providers)
    feed = session.get_inputs()[0].name
    log(f"depth: {len(wanted)} depth frames for {total} video frames on {session.get_providers()[0].replace('ExecutionProvider', '')}")
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)

    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(footage), "-vf", f"scale={IN_W}:{IN_H}",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size, depth, done = IN_W * IN_H * 3, {}, 0
    for n in range(total):
        raw = reader.stdout.read(size)
        if len(raw) < size:
            break
        if n not in wanted:
            continue
        x = ((np.frombuffer(raw, np.uint8).reshape(IN_H, IN_W, 3).astype(np.float32) / 255 - mean) / std)
        depth[n] = session.run(None, {feed: x.transpose(2, 0, 1)[None]})[0][0].astype(np.float32)
        done += 1
        if done % 25 == 0:
            log(f"depth: {done}/{len(wanted)}")
    reader.stdout.close()
    reader.wait()

    out = render / "depth.mp4"
    tmp = render / "depth.part.mp4"
    writer = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{IN_W}x{IN_H}",
                               "-r", str(fps), "-i", "-", "-vf", f"scale={OUT_W}:{OUT_H}:flags=bicubic", "-c:v", "libx264",
                               "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-g", str(fps), str(tmp)],
                              stdin=subprocess.PIPE)
    flat = np.zeros((IN_H, IN_W), np.uint8)
    for (a, b), ks in zip(segments, keys_of):
        ks = [k for k in ks if k in depth]
        if not ks:
            for _ in range(a, b):
                writer.stdin.write(flat.tobytes())
            continue
        # one range for the whole shot, so near and far do not flicker from frame to frame
        stack = np.stack([depth[k] for k in ks])
        lo, hi = np.percentile(stack, 1), np.percentile(stack, 99)
        scale = 255.0 / max(hi - lo, 1e-6)
        j = 0
        for f in range(a, b):
            while j + 1 < len(ks) - 1 and ks[j + 1] <= f:
                j += 1
            k0, k1 = ks[j], ks[min(j + 1, len(ks) - 1)]
            w = 0.0 if k1 == k0 else min(1.0, max(0.0, (f - k0) / (k1 - k0)))
            d = depth[k0] * (1 - w) + depth[k1] * w
            writer.stdin.write(np.clip((d - lo) * scale, 0, 255).astype(np.uint8).tobytes())
    writer.stdin.close()
    if writer.wait() != 0:
        raise SystemExit("depth: could not write depth.mp4")
    tmp.replace(out)
    log(f"depth: done ({probe(out)['duration']:.2f}s)")
    return out


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    build(Path(sys.argv[1]))
