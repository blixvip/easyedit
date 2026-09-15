"""Frame sampling, letterbox detection, face detection (OpenCV YuNet)."""
from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from .util import CACHE, log

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")


def frames(path: Path, fps: float, width: int, start: float = 0.0, duration: float | None = None,
           height: int | None = None, crop: tuple | None = None):
    """Yield (time, HxWx3 BGR uint8) sampled at `fps`, scaled to `width` px wide."""
    from .util import probe
    info = probe(path)
    cw, ch = (crop[0], crop[1]) if crop else (info["width"], info["height"])
    height = height or int(round(width * ch / cw / 2)) * 2
    vf = []
    if crop:
        vf.append("crop=%d:%d:%d:%d" % crop)
    vf += [f"fps={fps}", f"scale={width}:{height}:flags=area"]
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(path)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-an", "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = width * height * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()


def letterbox(path: Path, duration: float) -> tuple[int, int, int, int] | None:
    """Detect black bars. Returns ffmpeg crop (w,h,x,y) or None.

    Uses the median content box across frames sampled from the middle of the clip, so
    full-frame channel intros or bright titles don't hide real bars."""
    from .util import probe
    info = probe(path)
    W, H = info["width"], info["height"]
    lo, hi = duration * 0.1, duration * 0.9
    step = max((hi - lo) / 30, 0.5)
    boxes = []
    for _, img in frames(path, 1 / step, 320, lo, hi - lo):
        g = img.max(axis=2)
        rows = np.where(np.percentile(g, 98, axis=1) > 40)[0]
        cols = np.where(np.percentile(g, 98, axis=0) > 40)[0]
        if len(rows) > 10 and len(cols) > 10:
            h, w = g.shape
            boxes.append((rows[0] / h, (rows[-1] + 1) / h, cols[0] / w, (cols[-1] + 1) / w))
    if len(boxes) < 4:
        return None
    b = np.array(boxes)
    y0, y1 = np.percentile(b[:, 0], 30), np.percentile(b[:, 1], 70)
    x0, x1 = np.percentile(b[:, 2], 30), np.percentile(b[:, 3], 70)
    if y0 < 0.03 and y1 > 0.97:
        y0, y1 = 0.0, 1.0
    if x0 < 0.03 and x1 > 0.97:
        x0, x1 = 0.0, 1.0
    if (y0, y1, x0, x1) == (0.0, 1.0, 0.0, 1.0):
        return None
    pad = 0.006  # eat the soft edge of the bars
    Y0 = int((y0 + (pad if y0 else 0)) * H) // 2 * 2 + (2 if y0 else 0)
    Y1 = int((y1 - (pad if y1 < 1 else 0)) * H) // 2 * 2
    X0 = int((x0 + (pad if x0 else 0)) * W) // 2 * 2 + (2 if x0 else 0)
    X1 = int((x1 - (pad if x1 < 1 else 0)) * W) // 2 * 2
    return (X1 - X0, Y1 - Y0, X0, Y0)


class FaceDetector:
    def __init__(self):
        import cv2
        model = CACHE / "face_detection_yunet_2023mar.onnx"
        if not model.exists():
            model.parent.mkdir(parents=True, exist_ok=True)
            log("downloading YuNet face model")
            urllib.request.urlretrieve(YUNET_URL, model)
        self.cv2 = cv2
        self.net = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.72, 0.3, 50)

    def detect(self, img: np.ndarray) -> list[tuple[float, float, float, float]]:
        """Return faces as (cx, cy, size, score) normalized 0..1 to the image."""
        h, w = img.shape[:2]
        self.net.setInputSize((w, h))
        _, faces = self.net.detect(img)
        out = []
        for f in faces if faces is not None else []:
            x, y, fw, fh, score = f[0], f[1], f[2], f[3], f[-1]
            out.append(((x + fw / 2) / w, (y + fh * 0.45) / h, fh / h, float(score)))
        return out


def cover_map(src_w: int, src_h: int, out_w: int, out_h: int):
    """Map normalized source coords -> output px under object-fit: cover."""
    scale = max(out_w / src_w, out_h / src_h)
    dw, dh = src_w * scale, src_h * scale
    ox, oy = (out_w - dw) / 2, (out_h - dh) / 2
    return lambda nx, ny: (ox + nx * dw, oy + ny * dh)
