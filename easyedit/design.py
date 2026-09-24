"""Design a poster from an imported picture. Bytes in, PNG bytes out.

The result uses the picture's own colors and where the light sits in the frame.
It is not a re-encode of the upload.
"""
from __future__ import annotations

import numpy as np

MAX_BYTES = 25 * 1024 * 1024
CANVAS = (1200, 1560)  # width, height


def design_picture(data: bytes) -> bytes:
    """Return a PNG poster designed from PNG, JPEG, or WebP bytes."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("choose a PNG, JPEG, or WebP picture")
    if len(data) > MAX_BYTES:
        raise ValueError("picture is too large")
    import cv2

    src = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if src is None or src.size == 0:
        raise ValueError("choose a PNG, JPEG, or WebP picture")
    poster = _compose(src)
    ok, encoded = cv2.imencode(".png", poster)
    if not ok:
        raise ValueError("could not encode the design")
    return encoded.tobytes()


def _compose(src: np.ndarray) -> np.ndarray:
    import cv2

    width, height = CANVAS
    colors = _palette(src)
    ink, paper, accent, spark = colors
    cx, cy = _light_center(src)
    canvas = _vertical_gradient(height, width, _shade(ink, 0.55), _shade(paper, 0.28))
    _glow(canvas, int(width * (0.22 + 0.56 * cx)), int(height * (0.18 + 0.4 * cy)), accent)
    _glow(canvas, int(width * (0.8 - 0.3 * cx)), int(height * 0.82), spark)

    frame_w, frame_h = 860, 980
    fx = int(np.clip(170 + (cx - 0.5) * 140, 80, width - frame_w - 80))
    fy = int(np.clip(150 + (cy - 0.5) * 90, 90, 250))
    photo = _cover(src, frame_w, frame_h)
    photo = _grade(photo, accent, spark)
    _rounded_paste(canvas, photo, fx, fy, 36)
    cv2.rectangle(canvas, (fx - 14, fy - 14), (fx + frame_w + 14, fy + frame_h + 14), _shade(paper, 1.15), 2)

    bar_y = fy + frame_h + 48
    swatch = 72
    gap = 18
    total = 4 * swatch + 3 * gap
    sx = (width - total) // 2
    for i, color in enumerate(colors):
        x = sx + i * (swatch + gap)
        cv2.rectangle(canvas, (x, bar_y), (x + swatch, bar_y + swatch), color, -1)
        cv2.rectangle(canvas, (x, bar_y), (x + swatch, bar_y + swatch), _shade(paper, 1.2), 1)

    rule = int(80 + cx * (width - 160))
    cv2.line(canvas, (80, 70), (rule, 70), accent, 4)
    cv2.line(canvas, (rule + 16, 70), (width - 80, 70), _shade(paper, 0.7), 2)
    label = _label(colors[0])
    cv2.putText(canvas, label, (80, height - 64), cv2.FONT_HERSHEY_SIMPLEX, 0.85, _readable(ink), 2, cv2.LINE_AA)
    cv2.putText(canvas, "DESIGNED FROM THIS PICTURE", (80, height - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, paper, 1, cv2.LINE_AA)
    return canvas


def _palette(src: np.ndarray) -> list[tuple[int, int, int]]:
    import cv2

    small = cv2.resize(src, (48, 48), interpolation=cv2.INTER_AREA)
    pixels = small.reshape(-1, 3).astype(np.float32)
    k = 4 if len(pixels) >= 4 else 1
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 24, 1.0)
    _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    order = list(np.argsort(-counts))
    colors = [tuple(int(v) for v in centers[i]) for i in order]
    while len(colors) < 4:
        colors.append(_shade(colors[-1], 1.25))
    return colors[:4]


def _light_center(src: np.ndarray) -> tuple[float, float]:
    import cv2

    gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY).astype(np.float64)
    weight = gray + 1.0
    h, w = gray.shape
    ys, xs = np.mgrid[0:h, 0:w]
    total = weight.sum()
    return float((xs * weight).sum() / total / w), float((ys * weight).sum() / total / h)


def _shade(color: tuple[int, int, int], scale: float) -> tuple[int, int, int]:
    return tuple(int(np.clip(c * scale, 0, 255)) for c in color)


def _readable(color: tuple[int, int, int]) -> tuple[int, int, int]:
    luma = 0.114 * color[0] + 0.587 * color[1] + 0.299 * color[2]
    return (18, 18, 20) if luma > 150 else (244, 242, 236)


def _label(color: tuple[int, int, int]) -> str:
    b, g, r = color
    return f"#{r:02X}{g:02X}{b:02X}"


def _vertical_gradient(height: int, width: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    col = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    line = np.array(top, np.float32) * (1 - col) + np.array(bottom, np.float32) * col
    return np.repeat(line, width, axis=1).astype(np.uint8)


def _glow(canvas: np.ndarray, cx: int, cy: int, color: tuple[int, int, int]) -> None:
    import cv2

    overlay = canvas.copy()
    cv2.circle(overlay, (cx, cy), 280, color, -1)
    cv2.addWeighted(overlay, 0.28, canvas, 0.72, 0, canvas)


def _cover(img: np.ndarray, tw: int, th: int) -> np.ndarray:
    import cv2

    h, w = img.shape[:2]
    scale = max(tw / w, th / h)
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    resized = cv2.resize(img, (max(tw, int(round(w * scale))), max(th, int(round(h * scale)))), interpolation=interp)
    y = max(0, (resized.shape[0] - th) // 2)
    x = max(0, (resized.shape[1] - tw) // 2)
    return resized[y:y + th, x:x + tw]


def _grade(photo: np.ndarray, accent: tuple[int, int, int], spark: tuple[int, int, int]) -> np.ndarray:
    import cv2

    tint = np.zeros_like(photo)
    tint[:] = tuple(int((a + s) / 2) for a, s in zip(accent, spark))
    graded = cv2.addWeighted(photo, 0.84, tint, 0.16, 0)
    return cv2.convertScaleAbs(graded, alpha=1.06, beta=4)


def _rounded_paste(canvas: np.ndarray, photo: np.ndarray, x: int, y: int, radius: int) -> None:
    import cv2

    h, w = photo.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (radius, 0), (w - radius, h), 255, -1)
    cv2.rectangle(mask, (0, radius), (w, h - radius), 255, -1)
    for center in ((radius, radius), (w - radius - 1, radius), (radius, h - radius - 1), (w - radius - 1, h - radius - 1)):
        cv2.circle(mask, center, radius, 255, -1)
    roi = canvas[y:y + h, x:x + w]
    alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
    canvas[y:y + h, x:x + w] = (photo.astype(np.float32) * alpha + roi.astype(np.float32) * (1 - alpha)).astype(np.uint8)
