"""Measure a creator's reels into a style profile that the pipeline can render with.

    python -m easyedit.style pull nio_trade                 # how to fetch the reels (Instagram needs a browser)
    python -m easyedit.style analyze nio_trade              # styles/nio_trade/reels/*.mp4 -> style.json + report.txt
    python -m easyedit.style analyze C:\\refs\\folder        # any folder of mp4s -> styles/<folder>/
    python -m easyedit.style report nio_trade               # print the report again
    python -m easyedit.style compare <job> nio_trade        # the job's rendered video measured the same way

Only measurements leave this module: cut rhythm, beat alignment, speed ramps, grade statistics,
caption placement, flashes, shakes, faces, speech share, sound hits. No frame or sound of the
reference reels is ever copied into an edit.

Per reel (styles/<name>/work/reel-<id>.json) and aggregated (style.json). The aggregate also splits
the reels into modes: `voiceover` (someone talks over the pictures) and `montage` (music and cuts),
because creators cut those two very differently.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import beats as beatmod
from . import shots as shotmod
from .util import ROOT, log, probe, read_json, write_json

STYLES = ROOT / "styles"
REEL_VERSION = 3       # bump when analyze_reel's output changes
PROFILE_VERSION = 1
FAST_FPS, FAST_W = 24, 240       # pass A: cuts, motion, luma, saturation, shakes, moves
SLOW_FPS, SLOW_W = 2, 640        # pass B: text rows, faces, grade statistics
BEAT_TOL, SFX_TOL = 0.08, 0.06   # seconds
RAMP_KINDS = ("in", "out", "hit")
ASPECTS = {"16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0, "4:5": 0.8, "9:16": 9 / 16}


# ---------------------------------------------------------------- locating reels

def style_dir(spec: str) -> Path:
    """`nio_trade` -> styles/nio_trade; a folder path -> styles/<folder name> (its mp4s are the reels)."""
    p = Path(spec)
    if p.is_dir():
        return STYLES / p.name
    return STYLES / spec


def reel_files(spec: str) -> list[Path]:
    p = Path(spec)
    if p.is_dir():
        found = sorted(p.glob("*.mp4")) or sorted((p / "reels").glob("*.mp4"))
    elif p.is_file():
        found = [p]
    else:
        found = sorted((STYLES / spec / "reels").glob("*.mp4"))
    return found


def load(spec: str) -> dict:
    """A profile by name, or a path to a style.json."""
    p = Path(spec)
    if p.is_file():
        return read_json(p)
    f = style_dir(spec) / "style.json"
    if not f.exists():
        raise FileNotFoundError(f"no style profile at {f}; run: python -m easyedit.style analyze {spec}")
    return read_json(f)


# ---------------------------------------------------------------- per-reel measurement

def _aspect(w: int, h: int) -> str:
    r = w / h
    return min(ASPECTS, key=lambda k: abs(np.log(ASPECTS[k] / r)))


def _ramp_curves(n: int) -> dict[str, np.ndarray]:
    from .assemble import RAMPS
    u = (np.arange(n) + 0.5) / n
    return {k: np.array([f(x) for x in u], dtype=np.float32) for k, f in RAMPS.items()}


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / d) if d > 1e-6 else 0.0


def _text_bands(gray: np.ndarray) -> list[tuple[float, float, str]]:
    """Rows of text in a gray frame as (center, height, polarity) fractions of the frame height.

    Text is many bright/dark flips along a row (letters), in rows that are not mostly filled.
    `light` = light text on a dark picture (burned-in captions), `dark` = dark text on a light
    card (screenshots, articles, call-to-action cards)."""
    H, W = gray.shape
    out = []
    for name, mask in (("light", gray > 200), ("dark", gray < 60)):
        flips = np.abs(np.diff(mask.astype(np.int8), axis=1)).sum(axis=1)
        fill = mask.mean(axis=1)
        text = (flips >= max(10, W // 45)) & (fill > 0.01) & (fill < 0.45)
        if name == "dark":   # dark text needs a light row around it
            text &= (gray.mean(axis=1) > 150)
        rows = np.flatnonzero(text)
        if not len(rows):
            continue
        start = prev = rows[0]
        runs = []
        for r in rows[1:]:
            if r - prev > 3:
                runs.append((start, prev))
                start = r
            prev = r
        runs.append((start, prev))
        for a, b in runs:
            h = (b - a + 1) / H
            if 0.012 <= h <= 0.2:
                out.append(((a + b) / 2 / H, h, name))
    return out


def _bars(src: Path, dur: float, W: int, H: int) -> tuple[int, int, int, int] | None:
    """Black bars around a boxed picture, as an ffmpeg crop (w, h, x, y), or None.

    A row or column is a bar only if it stays near black (max < 40, grain and vignette allowed) in every
    sampled frame; the picture itself may be dark, but somewhere in the reel something lights every one
    of its rows."""
    from .vision import frames
    rows, cols = None, None
    for _, img in frames(src, max(0.5, 24 / max(dur, 1.0)), 160):
        lum = img.max(axis=2)
        r, c = lum.max(axis=1), lum.max(axis=0)
        rows = r if rows is None else np.maximum(rows, r)
        cols = c if cols is None else np.maximum(cols, c)
    if rows is None:
        return None

    def span(v: np.ndarray) -> tuple[float, float]:
        lit = np.flatnonzero(v >= 40)
        if not len(lit):
            return 0.0, 1.0
        return lit[0] / len(v), (lit[-1] + 1) / len(v)

    y0, y1 = span(rows)
    x0, x1 = span(cols)
    if y1 - y0 > 0.97 and x1 - x0 > 0.97:
        return None
    X0, X1 = int(x0 * W) // 2 * 2, int(x1 * W) // 2 * 2
    Y0, Y1 = int(y0 * H) // 2 * 2, int(y1 * H) // 2 * 2
    if X1 - X0 < 32 or Y1 - Y0 < 32:
        return None
    return (X1 - X0, Y1 - Y0, X0, Y0)


def _spans(times: list[float], flags: list[bool], step: float) -> list[list[float]]:
    """Runs of true flags as [start, end] seconds."""
    out, start = [], None
    for t, f in zip(times, flags):
        if f and start is None:
            start = t
        elif not f and start is not None:
            out.append([round(start, 2), round(t, 2)])
            start = None
    if start is not None and times:
        out.append([round(start, 2), round(times[-1] + step, 2)])
    return out


def _grade_stats(frames_bgr: list[np.ndarray]) -> dict:
    import cv2
    lum, con, sat, temp, fade, vig, grain, sharp = [], [], [], [], [], [], [], []
    lo_t, hi_t = [], []
    for img in frames_bgr:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gf = g.astype(np.float32)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        lum.append(gf.mean()); con.append(gf.std()); sat.append(hsv[..., 1].mean())
        lit = (gf > 40) & (gf < 220)
        if lit.sum() > 50:
            px = img[lit].astype(np.float32)
            temp.append(float((px[:, 2] - px[:, 0]).mean()))       # R - B
        fade.append(float(np.percentile(gf, 1)))
        h, w = g.shape
        cy, cx = h // 2, w // 2
        center = gf[cy - h // 6: cy + h // 6, cx - w // 6: cx + w // 6].mean()
        corners = np.mean([gf[: h // 6, : w // 6].mean(), gf[: h // 6, -w // 6:].mean(),
                           gf[-h // 6:, : w // 6].mean(), gf[-h // 6:, -w // 6:].mean()])
        vig.append(float(corners / (center + 1e-6)))
        blur = cv2.GaussianBlur(gf, (0, 0), 1.2)
        grad = cv2.Laplacian(blur, cv2.CV_32F)
        flat = np.abs(grad) < 2.0
        if flat.sum() > 500:
            grain.append(float((gf - blur)[flat].std()))
        sharp.append(float(cv2.Laplacian(gf, cv2.CV_32F).var()))
        shadows, highs = gf < 60, gf > 190
        if shadows.sum() > 50:
            px = img[shadows].astype(np.float32)
            lo_t.append(px.mean(axis=0) - px.mean())
        if highs.sum() > 50:
            px = img[highs].astype(np.float32)
            hi_t.append(px.mean(axis=0) - px.mean())
    med = lambda v: float(np.median(v)) if len(v) else 0.0
    lo = np.median(np.stack(lo_t), axis=0) if lo_t else np.zeros(3)
    hi = np.median(np.stack(hi_t), axis=0) if hi_t else np.zeros(3)
    # split toning: warm highlights (R>B) with cool shadows (B>R)
    split = float((hi[2] - hi[0]) - (lo[2] - lo[0]))
    stats = {"luma": med(lum), "contrast": med(con), "sat": med(sat), "temp": med(temp), "fade": med(fade),
             "vignette": med(vig), "grain": med(grain), "sharp": med(sharp),
             "lo_tint": [round(float(v), 1) for v in lo[::-1]],     # as RGB
             "hi_tint": [round(float(v), 1) for v in hi[::-1]], "split": split}
    stats["nearest"] = nearest_grade(stats)
    return stats


def nearest_grade(s: dict) -> str:
    """The film.js grade whose character the measurements resemble most (a label, not a fit)."""
    sat, temp, split, fade, con = s["sat"], s["temp"], s["split"], s["fade"], s["contrast"]
    if sat < 14:
        return "noir"
    if fade > 28 and con < 42:
        return "matte"
    if split > 14 and temp > -4:
        return "teal-orange"
    if temp > 14:
        return "warm"
    if temp < -14:
        return "ice" if sat < 40 else "cool"
    if sat > 75:
        return "vivid"
    if con > 52:
        return "cinematic"
    return "neutral"


def analyze_reel(src: Path, work: Path, faces=None, speech: bool = True, crop=None) -> dict:
    """Everything measurable about one reel. Cached in work/reel-<stem>.json.
    `crop`: the picture area (w, h, x, y) when the caller knows it; otherwise black bars are detected."""
    import cv2
    from .vision import FaceDetector, frames
    cache = work / f"reel-{src.stem}.json"
    if cache.exists():
        c = read_json(cache)
        if c.get("v") == REEL_VERSION and (c.get("speech") is not None or not speech) \
                and (crop is None or c.get("crop") == list(crop)):
            return c
    info = probe(src)
    dur, W, H = info["duration"], info["width"], info["height"]
    crop = tuple(crop) if crop else _bars(src, dur, W, H)     # bars around a boxed picture are not the picture
    if crop:
        W, H = crop[0], crop[1]
    out = {"v": REEL_VERSION, "id": src.stem, "file": str(src), "duration": round(dur, 2), "width": W, "height": H,
           "fps": round(info["fps"], 2), "aspect": _aspect(W, H), "crop": list(crop) if crop else None}

    # ---- pass A: every frame, small
    grays, thumbs, lum, sats = [], [], [], []
    for _, img in frames(src, FAST_FPS, FAST_W, crop=crop):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        grays.append(g)
        thumbs.append(cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32))
        lum.append(float(g.mean()))
        sats.append(float(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1].mean()))
    n = len(grays)
    if n < FAST_FPS:
        out.update(error="too short")
        write_json(cache, out)
        return out
    diffs = np.abs(np.diff(np.stack(thumbs), axis=0)).mean(axis=(1, 2, 3))
    cut_idx = shotmod._cuts(diffs, min_gap=2)
    cuts = [round((i + 1) / FAST_FPS, 3) for i in cut_idx]
    bounds = [0] + [i + 1 for i in cut_idx] + [n]
    shot_spans = [(a / FAST_FPS, b / FAST_FPS) for a, b in zip(bounds, bounds[1:])]
    shot_lens = [round(b - a, 3) for a, b in shot_spans]
    out["cuts"] = cuts
    out["shot_lens"] = shot_lens
    lum = np.array(lum)
    sats = np.array(sats)

    # mono (black and white) frames and where they fall; a tinted B&W (split-toned, sepia) still counts,
    # so the bar is relative to how colourful the reel is overall
    mono = sats < max(20.0, 0.45 * float(np.median(sats)))
    k = max(1, int(n * 0.15))
    out["mono"] = {"fraction": round(float(mono.mean()), 3), "intro": round(float(mono[:k].mean()), 3),
                   "outro": round(float(mono[-k:].mean()), 3)}

    # flashes: a frame much brighter than both neighbours two frames away, not explained by a cut
    cutset = set(cut_idx) | {i + 1 for i in cut_idx}
    flashes = [i for i in range(2, n - 2)
               if lum[i] - max(lum[i - 2], lum[i + 2]) > 40 and i not in cutset and i - 1 not in cutset]
    at_cut = sum(1 for i in flashes if any(abs(i - c) <= 3 for c in cut_idx))
    out["flash"] = {"per_min": round(len(flashes) / dur * 60, 2),
                    "at_cut_frac": round(at_cut / len(flashes), 2) if flashes else 0.0}

    # per-shot motion, shake, ramps, moves
    curves = None
    motion_all = np.abs(np.diff(np.stack(grays).astype(np.float32), axis=0)).mean(axis=(1, 2))
    shots_out = []
    jitters = []
    for (a, b), (t0, t1) in zip(zip(bounds, bounds[1:]), shot_spans):
        length = t1 - t0
        s = {"start": round(t0, 3), "end": round(t1, 3)}
        seg = motion_all[a: b - 1] if b - a > 2 else np.zeros(1)
        s["motion"] = round(float(seg.mean()), 2)
        # ramp: how the motion rises and falls through the shot
        if length >= 0.6 and len(seg) >= 8:
            sm = np.convolve(seg, np.ones(5) / 5, mode="same")
            m = sm / (sm.mean() + 1e-6)
            if curves is None or len(next(iter(curves.values()))) != len(m):
                curves = _ramp_curves(len(m))
            cs = {kd: _corr(m, curves[kd]) for kd in RAMP_KINDS}
            best = max(cs, key=cs.get)
            strength = float(np.percentile(sm, 90) / (np.percentile(sm, 10) + 1e-6))
            s["ramp"] = best if cs[best] > 0.5 and strength > 1.8 else None
            s["ramp_corr"] = round(cs[best], 2)
            s["ramp_strength"] = round(strength, 2)
        else:
            s["ramp"] = None
        # shake: high-frequency jitter of the frame translation
        if b - a > 8:
            dx = []
            prev = grays[a].astype(np.float32)
            for i in range(a + 1, min(b, a + 72)):
                cur = grays[i].astype(np.float32)
                (sx, sy), _ = cv2.phaseCorrelate(prev, cur)
                dx.append((sx, sy))
                prev = cur
            d = np.array(dx)
            if len(d) > 6:
                hp = d - np.stack([np.convolve(d[:, 0], np.ones(5) / 5, mode="same"),
                                   np.convolve(d[:, 1], np.ones(5) / 5, mode="same")], axis=1)
                jit = float(np.sqrt((hp ** 2).sum(axis=1)).std() / FAST_W * 100)
                s["jitter"] = round(jit, 2)
                jitters.append(jit)
        # move: the camera motion in the first quarter second (scale, pan)
        s["move"] = _move(grays[a], grays[min(b - 1, a + 6)])
        shots_out.append(s)
    out["shots"] = shots_out
    out["shake"] = {"jitter": round(float(np.median(jitters)), 2) if jitters else 0.0}
    ramped = [s for s in shots_out if "ramp_corr" in s]
    kinds = [s["ramp"] for s in ramped if s["ramp"]]
    out["ramps"] = {"fraction": round(len(kinds) / len(ramped), 2) if ramped else 0.0,
                    "kinds": {kd: round(kinds.count(kd) / len(kinds), 2) for kd in RAMP_KINDS} if kinds else {},
                    "sequence": [s["ramp"] for s in ramped]}
    moves = [s["move"] for s in shots_out if s["move"]]
    out["moves"] = {m: round(moves.count(m) / len(moves), 2) for m in sorted(set(moves))} if moves else {}

    # ---- pass B: a frame every half second, larger: text, faces, grade
    faces = faces or FaceDetector()
    text_frames, bands, face_hits, face_cy, face_size, big = [], [], 0, [], [], []
    band_masks = []
    times_b = []
    for t, img in frames(src, SLOW_FPS, SLOW_W, crop=crop):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        tb = _text_bands(g)
        times_b.append(t)
        text_frames.append(bool(tb))
        bands.extend((t, *b) for b in tb)
        big.append(img)
        band_masks.append(g)
        found = sorted(faces.detect(img), key=lambda f: -f[2])
        if found and found[0][2] > 0.06:
            face_hits += 1
            face_cy.append(found[0][1]); face_size.append(found[0][2])
    nb = len(big)
    out["faces"] = {"fraction": round(face_hits / nb, 2) if nb else 0.0,
                    "cy": round(float(np.median(face_cy)), 3) if face_cy else None,
                    "size": round(float(np.median(face_size)), 3) if face_size else None}
    out["grade"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in _grade_stats(big).items()}
    out["captions"] = _caption_stats(bands, band_masks, text_frames, nb)
    # a card = a lit, still, nearly gray frame (screenshot, article, call to action)
    card_flags = []
    for i, img in enumerate(big):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        grayish = float(((hsv[..., 1] < 45) & (hsv[..., 2] > 60)).mean())
        card_flags.append(grayish > 0.6 and text_frames[i])
    cards = sum(card_flags)
    out["cards"] = {"fraction": round(cards / nb, 2) if nb else 0.0,
                    "spans": _spans(times_b, card_flags, 1 / SLOW_FPS)}
    out["text"] = {"spans": _spans(times_b, text_frames, 1 / SLOW_FPS)}
    # a hook: text on screen from the first frame, held still for a while
    hook_hold = 0.0
    if text_frames and text_frames[0]:
        y0, y1 = 0, len(band_masks[0])
        prev = band_masks[0][y0:y1] > 200
        for i in range(1, len(band_masks)):
            if not text_frames[i]:
                break
            cur = band_masks[i][y0:y1] > 200
            union = (cur | prev).sum()
            if union > 20 and ((cur ^ prev).sum() / union) > 0.35:
                break
            prev = cur
            hook_hold = times_b[i]
    out["hook"] = {"text": bool(text_frames and text_frames[0]), "hold": round(hook_hold, 2)}

    # ---- audio: beats, drop, alignment, hits
    out["audio"] = _audio_stats(src, info, cuts, dur)

    # ---- intro and outro
    first, last = shots_out[0], shots_out[-1]
    first_text = any(t < 1.0 for t, *_ in bands)
    last_text = any(t > dur - 1.5 for t, *_ in bands)
    tail = big[-max(1, int(SLOW_FPS * 1.5)):]
    tail_g = float(np.mean([cv2.cvtColor(i, cv2.COLOR_BGR2GRAY).mean() for i in tail]))
    out["intro"] = {"shot_len": shot_lens[0], "text": first_text}
    out["outro"] = {"shot_len": shot_lens[-1], "text": last_text, "mono": out["mono"]["outro"] > 0.5,
                    "card": "white" if tail_g > 180 and last_text else ("black" if tail_g < 30 else None)}

    # ---- speech
    if speech:
        out["speech"] = _speech_stats(src, work, dur)
    write_json(cache, out)
    log(f"style: {src.stem}: {len(cuts)} cuts, {out['audio'].get('bpm', 0):.0f} bpm, align "
        f"{out['audio'].get('beat_align', 0):.2f}, speech {out.get('speech', {}).get('fraction', 0):.2f}, "
        f"captions {out['captions']['fraction']:.2f} @ y={out['captions']['y']}")
    return out


def _move(g0: np.ndarray, g1: np.ndarray) -> str | None:
    """Camera motion between a shot's first frame and a quarter second later, named like film.js moves."""
    import cv2
    orb = cv2.ORB_create(400)
    k0, d0 = orb.detectAndCompute(g0, None)
    k1, d1 = orb.detectAndCompute(g1, None)
    if d0 is None or d1 is None or len(k0) < 12 or len(k1) < 12:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d0, d1)
    if len(matches) < 12:
        return None
    p0 = np.float32([k0[m.queryIdx].pt for m in matches])
    p1 = np.float32([k1[m.trainIdx].pt for m in matches])
    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if M is None or inl is None or inl.sum() < 10:
        return None
    scale = float(np.sqrt(M[0, 0] ** 2 + M[0, 1] ** 2))
    h, w = g0.shape
    cx, cy = w / 2, h / 2
    tx = (M[0, 0] * cx + M[0, 1] * cy + M[0, 2] - cx) / w
    ty = (M[1, 0] * cx + M[1, 1] * cy + M[1, 2] - cy) / h
    rot = abs(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    if rot > 2.5:
        return "spin"
    if scale > 1.03:
        return "punch"
    if scale < 0.97:
        return "zoom-out"
    if abs(tx) > 0.03:
        return "slide-left" if tx < 0 else "slide-right"
    if abs(ty) > 0.03:
        return "whip"
    return "cut"


def _caption_stats(bands: list, masks: list[np.ndarray], text_frames: list[bool], nb: int) -> dict:
    """Where the burned-in text lives and how it behaves."""
    if not nb or not bands:
        return {"fraction": 0.0, "y": None, "height": None, "size_frac": None, "mode": "none", "polarity": None}
    light = [b for b in bands if b[3] == "light"]
    use = light if len(light) >= len(bands) * 0.4 else bands
    centers = np.array([b[1] for b in use])
    hist, edges = np.histogram(centers, bins=np.arange(0, 1.0001, 0.04))
    top = int(np.argmax(hist))
    lo, hi = edges[top] - 0.02, edges[top + 1] + 0.02
    inband = [b for b in use if lo <= b[1] <= hi]
    frames_with = len({round(b[0], 2) for b in inband})
    y = float(np.median([b[1] for b in inband]))
    h = float(np.median([b[2] for b in inband]))
    # does the text in the band change between samples (word by word) or stay (a hook line)?
    changes, pairs = 0, 0
    H = masks[0].shape[0]
    y0, y1 = max(0, int((y - h) * H)), min(H, int((y + h) * H) + 1)
    prev = None
    for i, g in enumerate(masks):
        if not text_frames[i]:
            prev = None
            continue
        m = g[y0:y1] > 200 if use is light else (g[y0:y1] > 200) | (g[y0:y1] < 60)
        if prev is not None:
            union = (m | prev).sum()
            if union > 20:
                pairs += 1
                changes += ((m ^ prev).sum() / union) > 0.35
        prev = m
    change_rate = changes / pairs if pairs else 0.0
    frac = frames_with / nb
    mode = "none" if frac < 0.15 else ("accumulate" if change_rate > 0.5 else ("static" if change_rate < 0.25 else "lines"))
    return {"fraction": round(frac, 2), "y": round(y, 3), "height": round(h, 3),
            "size_frac": round(h, 3), "mode": mode, "change_rate": round(change_rate, 2),
            "polarity": "light" if use is light else "dark"}


def _audio_stats(src: Path, info: dict, cuts: list[float], dur: float) -> dict:
    if not info.get("has_audio"):
        return {"music": False}
    y = beatmod.load_audio(src)
    if len(y) < beatmod.SR * 2:
        return {"music": False}
    env = beatmod.onset_envelope(y)
    bpm = beatmod.tempo(env)
    bts = beatmod.track(env, bpm)
    fps = beatmod.SR / beatmod.HOP
    rms = np.sqrt(np.convolve(y ** 2, np.ones(beatmod.HOP) / beatmod.HOP, mode="same")[::beatmod.HOP] + 1e-12)
    loud = [float(rms[int(b * fps): int(b * fps) + int(fps * 0.5)].mean()) if int(b * fps) < len(rms) else 0
            for b in bts]
    strength = [float(env[max(0, int(b * fps) - 2): int(b * fps) + 3].max()) if int(b * fps) < len(env) else 0
                for b in bts]
    a = {"duration": dur, "bpm": bpm, "beats": [float(b) for b in bts], "loud": loud, "strength": strength}
    period = float(np.median(np.diff(bts))) if len(bts) > 2 else 60 / bpm
    half = np.sort(np.concatenate([bts, bts[:-1] + np.diff(bts) / 2])) if len(bts) > 2 else bts

    def align(grid: np.ndarray) -> float:
        if not cuts or not len(grid):
            return 0.0
        return float(np.mean([np.min(np.abs(grid - c)) <= BEAT_TOL for c in cuts]))

    on_beat, on_half = align(bts), align(half)
    chance = min(0.95, 2 * BEAT_TOL / period)
    conf = (on_beat - chance) / (1 - chance)
    drop_i = beatmod.find_drop(a, need=min(6.0, dur / 3))
    drop_t = float(bts[drop_i]) if len(bts) else 0.0
    before = [c for c in cuts if c < drop_t]
    after = [c for c in cuts if c >= drop_t]
    # sound hits: onset peaks at cuts that are not on a beat (whooshes and impacts the editor added)
    off = [c for c in cuts if not len(bts) or np.min(np.abs(bts - c)) > BEAT_TOL]
    hits = 0
    for c in off:
        i0, i1 = int((c - SFX_TOL) * fps), int((c + SFX_TOL) * fps) + 1
        if 0 <= i0 < len(env) and env[i0:i1].max() > 0.45:
            hits += 1
    # riser into the drop: loudness climbing through the 1.8 s before it
    i_a, i_b = int(max(0, drop_t - 1.8) * fps), int(drop_t * fps)
    riser = False
    if i_b - i_a > 10:
        seg = rms[i_a:i_b]
        riser = bool(seg[-len(seg) // 4:].mean() > 1.5 * seg[: len(seg) // 4].mean())
    impact = bool(env[max(0, int(drop_t * fps) - 2): int(drop_t * fps) + 3].max() > 0.7) if len(env) > 5 else False
    # cut lengths in beats
    units = [round(l / period * 2) / 2 for l in np.diff([0.0, *cuts, dur]) if l > 0.05] if cuts else []
    return {"music": True, "bpm": round(bpm, 1), "period": round(period, 3), "beats": len(bts),
            "beat_align": round(on_beat, 2), "half_align": round(on_half, 2), "confidence": round(conf, 2),
            "drop_t": round(drop_t, 2), "drop_frac": round(drop_t / dur, 2),
            "density_before": round(len(before) / drop_t, 2) if drop_t > 1 else None,
            "density_after": round(len(after) / (dur - drop_t), 2) if dur - drop_t > 1 else None,
            "hits_at_offbeat_cuts": round(hits / len(off), 2) if off else None,
            "riser": riser, "impact": impact, "units": units}


def _speech_stats(src: Path, work: Path, dur: float) -> dict:
    from . import transcribe
    words = transcribe.transcribe(src, work / f"words-{src.stem}.json")
    if not words:
        return {"fraction": 0.0, "words": 0, "text": ""}
    voiced, prev_end = 0.0, None
    for w in words:
        if prev_end is not None and w["start"] - prev_end < 0.6:
            voiced += w["start"] - prev_end
        voiced += max(0.0, w["end"] - w["start"])
        prev_end = w["end"]
    return {"fraction": round(min(1.0, voiced / dur), 2), "words": len(words),
            "wpm": round(len(words) / max(voiced, 1) * 60),
            "text": " ".join(w["text"] for w in words)[:300]}


# ---------------------------------------------------------------- sheets

def sheets(src: Path, out_dir: Path, reel: dict) -> list[Path]:
    """A 30-frame contact sheet and a sheet of the first frame after every cut."""
    from .sheet import _grab, _label, _tile
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = reel["duration"]
    grid = out_dir / f"{src.stem}.jpg"
    if not grid.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                        f"fps={30 / dur:.4f},scale=320:-2,tile=6x5", "-frames:v", "1", str(grid)])
    cuts_img = out_dir / f"{src.stem}-cuts.jpg"
    if not cuts_img.exists() and reel.get("cuts"):
        imgs = []
        for t in [0.0, *reel["cuts"]][:36]:
            im = _grab(src, t + 0.05)
            if im is not None:
                imgs.append(_label(im, f"{t:.2f}"))
        if imgs:
            _tile(imgs, cuts_img)
    return [p for p in (grid, cuts_img) if p.exists()]


# ---------------------------------------------------------------- aggregate

def _q(values, p=50):
    v = [x for x in values if x is not None]
    return round(float(np.percentile(v, p)), 3) if v else None


def _mode_of(r: dict) -> str:
    sp = (r.get("speech") or {}).get("fraction", 0.0)
    if sp >= 0.45:
        return "voiceover"
    if sp < 0.2 and r["audio"].get("music"):
        return "montage"
    return "mixed"


def _pattern(reels: list[dict]) -> dict:
    """Cut lengths in beats from the reel that cuts most confidently on the beat."""
    best = None
    for r in reels:
        a = r["audio"]
        if not a.get("music") or len(r["cuts"]) < 6:
            continue
        score = a["confidence"] * len(r["cuts"])
        if best is None or score > best[0]:
            best = (score, r)
    if not best:
        return {"pattern": None, "unit_beats": None, "from": None}
    r = best[1]
    units = [u for u in r["audio"]["units"] if u > 0]
    if len(units) > 4:   # the opening hook and the closing card are not the cutting rhythm
        units = units[1:-1]
    if not units:
        return {"pattern": None, "unit_beats": None, "from": r["id"]}
    g = max(0.5, min(units))
    pat = [max(1, int(round(u / g))) for u in units][:20]
    return {"pattern": pat, "unit_beats": g, "from": r["id"]}


def _summary(reels: list[dict]) -> dict:
    lens = [l for r in reels for l in r["shot_lens"]]
    music = [r for r in reels if r["audio"].get("music")]
    conf = [r["audio"]["confidence"] for r in music]
    grid = "free"
    if music and _q(conf) is not None and _q(conf) > 0.35:
        grid = "half" if _q([r["audio"]["half_align"] - r["audio"]["beat_align"] for r in music]) > 0.2 else "beat"
    moves: dict[str, float] = {}
    for r in reels:
        for m, f in r["moves"].items():
            moves[m] = moves.get(m, 0.0) + f
    moves = {m: round(f / len(reels), 2) for m, f in sorted(moves.items(), key=lambda kv: -kv[1])}
    ramp_kinds: dict[str, float] = {}
    for r in reels:
        for kd, f in r["ramps"]["kinds"].items():
            ramp_kinds[kd] = ramp_kinds.get(kd, 0.0) + f * r["ramps"]["fraction"]
    tot = sum(ramp_kinds.values()) or 1.0
    seq = max(reels, key=lambda r: r["ramps"]["fraction"])["ramps"]["sequence"][:8] if reels else []
    cap_modes = [r["captions"]["mode"] for r in reels]
    cards = [r["outro"]["card"] for r in reels]
    return {
        "count": len(reels),
        "duration": {"median": _q([r["duration"] for r in reels]), "iqr": [_q([r["duration"] for r in reels], 25),
                                                                            _q([r["duration"] for r in reels], 75)]},
        "cuts_per_reel": _q([len(r["cuts"]) for r in reels]),
        "shot_len": {"median": _q(lens), "p10": _q(lens, 10), "p90": _q(lens, 90)},
        "rhythm": {"bpm": _q([r["audio"]["bpm"] for r in music]), "grid": grid,
                   "beat_align": _q([r["audio"]["beat_align"] for r in music]),
                   "confidence": _q(conf),
                   "drop_frac": _q([r["audio"]["drop_frac"] for r in music]),
                   "density_before": _q([r["audio"]["density_before"] for r in music]),
                   "density_after": _q([r["audio"]["density_after"] for r in music]),
                   **_pattern(music)},
        "ramps": {"fraction": _q([r["ramps"]["fraction"] for r in reels]),
                  "kinds": {kd: round(f / tot, 2) for kd, f in ramp_kinds.items()}, "auto": seq},
        "moves": moves,
        "mono": {k: _q([r["mono"][k] for r in reels]) for k in ("fraction", "intro", "outro")},
        "grade": {**{k: _q([r["grade"][k] for r in reels]) for k in
                     ("luma", "contrast", "sat", "temp", "fade", "vignette", "grain", "sharp", "split")},
                  "lo_tint": [_q([r["grade"]["lo_tint"][i] for r in reels]) for i in range(3)],
                  "hi_tint": [_q([r["grade"]["hi_tint"][i] for r in reels]) for i in range(3)],
                  "nearest": max(set(r["grade"]["nearest"] for r in reels),
                                 key=[r["grade"]["nearest"] for r in reels].count)},
        "captions": {"fraction": _q([r["captions"]["fraction"] for r in reels]),
                     "y": _q([r["captions"]["y"] for r in reels]),
                     "size_frac": _q([r["captions"]["size_frac"] for r in reels]),
                     "mode": max(set(cap_modes), key=cap_modes.count),
                     "modes": {m: round(cap_modes.count(m) / len(reels), 2) for m in sorted(set(cap_modes))},
                     "polarity": max(set(r["captions"]["polarity"] for r in reels),
                                     key=[r["captions"]["polarity"] for r in reels].count)},
        "cards": _q([r["cards"]["fraction"] for r in reels]),
        "flash": {"per_min": _q([r["flash"]["per_min"] for r in reels]),
                  "at_cut_frac": _q([r["flash"]["at_cut_frac"] for r in reels])},
        "shake": {"jitter": _q([r["shake"]["jitter"] for r in reels])},
        "faces": {"fraction": _q([r["faces"]["fraction"] for r in reels]),
                  "cy": _q([r["faces"]["cy"] for r in reels]), "size": _q([r["faces"]["size"] for r in reels])},
        "speech": {"fraction": _q([(r.get("speech") or {}).get("fraction") for r in reels]),
                   "wpm": _q([(r.get("speech") or {}).get("wpm") for r in reels])},
        "sfx": {"hits_at_offbeat_cuts": _q([r["audio"].get("hits_at_offbeat_cuts") for r in music]),
                "riser": _q([float(r["audio"]["riser"]) for r in music]),
                "impact": _q([float(r["audio"]["impact"]) for r in music])},
        "intro": {"shot_len": _q([r["intro"]["shot_len"] for r in reels]),
                  "text": _q([float(r["intro"]["text"]) for r in reels])},
        "outro": {"shot_len": _q([r["outro"]["shot_len"] for r in reels]),
                  "text": _q([float(r["outro"]["text"]) for r in reels]),
                  "mono": _q([float(r["outro"]["mono"]) for r in reels]),
                  "card": {c: round(cards.count(c) / len(reels), 2) for c in sorted(set(map(str, cards)))}},
    }


def aggregate(reels: list[dict], name: str) -> dict:
    reels = [r for r in reels if not r.get("error")]
    aspects = [r["aspect"] for r in reels]
    by_mode: dict[str, list[dict]] = {}
    for r in reels:
        by_mode.setdefault(_mode_of(r), []).append(r)
    import datetime
    return {"v": PROFILE_VERSION, "name": name, "measured_at": datetime.date.today().isoformat(),
            "reels": [r["id"] for r in reels],
            "frame": {"aspect": max(set(aspects), key=aspects.count),
                      "aspects": {a: round(aspects.count(a) / len(aspects), 2) for a in sorted(set(aspects))},
                      "width": int(np.median([r["width"] for r in reels])),
                      "height": int(np.median([r["height"] for r in reels])),
                      "fps": _q([r["fps"] for r in reels])},
            "modes": {m: round(len(v) / len(reels), 2) for m, v in by_mode.items()},
            "all": _summary(reels),
            **{m: _summary(v) for m, v in by_mode.items()}}


# ---------------------------------------------------------------- the LLM look-up

LABEL_SYSTEM = ("You are a video editor describing another editor's signature style so it can be rebuilt "
                "with a fixed toolkit. Reply with JSON only.")


def label(profile: dict, sheet_paths: list[Path], provider: str = "auto") -> dict:
    """Name the caption style, font, text motion, camera moves and effects from the toolkit's enums.
    The numbers in the profile are ground truth; this only picks names for what numbers can't say."""
    from . import editor
    from .llm import ask_json
    enums = {"capStyle": editor.CAP_STYLES, "capFont": editor.CAP_FONTS, "capMotion": editor.CAP_MOTIONS,
             "capAnim": editor.CAP_ANIMS, "capFx": editor.CAP_FX, "moves": editor.MOVES, "fx": editor.FX,
             "grade": editor.GRADES}
    facts = {k: profile["all"][k] for k in ("captions", "grade", "moves", "flash", "shake", "mono", "cards", "outro")}
    prompt = (
        "Contact sheets of a creator's reels are attached (one row of frames per reel, left to right in time), "
        "plus measurements taken from all their reels:\n" + json.dumps(facts, indent=1) +
        "\n\nPick, from these lists only, the names that best describe how this creator's text and camera look:\n"
        + json.dumps({k: list(v) for k, v in enums.items()}, indent=1) +
        "\n\nReturn JSON: {\"capStyle\": str, \"capFont\": str, \"capMotion\": str, \"capAnim\": str, "
        "\"capFx\": [str], \"moves\": [str], \"fx\": [str], \"grade\": str, \"capColor\": \"#rrggbb\", "
        "\"notes\": str}. `notes` is two sentences a human editor would say about the look. "
        "Use capFx [] and fx [] when the reels are plain. The captions' size and position are already measured; "
        "describe their typeface weight, case and box (if any) in notes.")
    reply = ask_json(prompt, LABEL_SYSTEM, provider, images=sheet_paths[:3]) if provider != "none" else None
    out = {"source": "fallback"}
    if isinstance(reply, dict):
        out = {"source": "llm"}
        for k, allowed in enums.items():
            v = reply.get(k)
            if isinstance(v, list):
                out[k] = [x for x in v if x in allowed]
            elif v in allowed:
                out[k] = v
        if isinstance(reply.get("notes"), str):
            out["notes"] = reply["notes"][:400]
        if isinstance(reply.get("capColor"), str) and len(reply["capColor"]) == 7:
            out["capColor"] = reply["capColor"]
    cap = profile["all"]["captions"]
    out.setdefault("capStyle", "subtitle" if (cap.get("size_frac") or 0) < 0.05 else "clean")
    out.setdefault("capFont", "display" if (cap.get("size_frac") or 0) > 0.06 else "caption")
    out.setdefault("capMotion", "pop-up" if cap.get("mode") == "accumulate" else "ease-up")
    out.setdefault("capAnim", "pop" if cap.get("mode") == "accumulate" else "rise")
    out.setdefault("capFx", [])
    out.setdefault("moves", [m for m in profile["all"]["moves"] if m in editor.MOVES][:3] or ["cut"])
    out.setdefault("fx", ["shake"] if (profile["all"]["shake"]["jitter"] or 0) > 1.5 else [])
    out.setdefault("grade", profile["all"]["grade"]["nearest"])
    return out


# ---------------------------------------------------------------- one reel as a template

def templates(spec: str) -> list[dict]:
    """Every measured reel of a style, briefly, so one can be picked as a template."""
    out = []
    for f in sorted((style_dir(spec) / "work").glob("reel-*.json")):
        r = read_json(f)
        if r.get("error"):
            continue
        out.append({"id": r["id"], "duration": r["duration"], "cuts": len(r["cuts"]), "mode": _mode_of(r),
                    "cards": len((r.get("cards") or {}).get("spans") or []),
                    "outro": (r.get("outro") or {}).get("card"), "hook": (r.get("hook") or {}).get("hold", 0)})
    return out


def template(spec: str, reel_id: str) -> dict:
    """One reel's structure, shot by shot, for rebuilding it with other material: when each cut falls,
    which shots are card inserts, the opening hold, the closing card, the caption behaviour."""
    f = style_dir(spec) / "work" / f"reel-{reel_id}.json"
    if not f.exists():
        raise FileNotFoundError(f"no measured reel {reel_id} in {f.parent}; run: python -m easyedit.style analyze {spec}")
    r = read_json(f)
    if r.get("v", 0) < 3:
        raise RuntimeError(f"reel {reel_id} was measured by an older analyzer; run analyze again")
    dur, cuts = r["duration"], r["cuts"]
    bounds = [0.0, *cuts, dur]
    card_spans = (r.get("cards") or {}).get("spans") or []
    shots = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        s = r["shots"][i] if i < len(r["shots"]) else {}
        mid = (a + b) / 2
        card = any(c0 - 0.3 <= mid <= c1 + 0.3 for c0, c1 in card_spans)
        shots.append({"start": round(a, 3), "end": round(b, 3), "len": round(b - a, 3),
                      "move": s.get("move"), "ramp": s.get("ramp"), "card": card})
    outro = r.get("outro") or {}
    last = shots[-1]
    outro_card = bool(outro.get("card")) and last["len"] >= 2.0
    body_end = last["start"] if outro_card else dur
    hook = r.get("hook") or {}
    return {"reel": reel_id, "duration": dur, "mode": _mode_of(r), "shots": shots[:-1] if outro_card else shots,
            "body": round(body_end, 3), "hook": {"text": hook.get("text", False), "hold": hook.get("hold", 0.0)},
            "outro": {"card": outro.get("card"), "len": round(last["len"], 3) if outro_card else 0.0},
            "captions": r["captions"], "speech": (r.get("speech") or {}).get("fraction"),
            "drop_t": r["audio"].get("drop_t"), "bpm": r["audio"].get("bpm"),
            "cards": sum(1 for s in shots if s["card"])}


# ---------------------------------------------------------------- from a profile to the pipeline's knobs

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def apply(profile: dict | None, mode: str, canvas: tuple[int, int], fit: str, picture: tuple[float, float]) -> dict:
    """Everything the pipeline needs to cut and render like the profile, for one edit.

    `mode`: voiceover | montage (the profile's own split; falls back to its "all" summary).
    `picture`: the picture box in canvas px (the whole canvas for cover, the fitted footage for box);
    caption size and position are measured as fractions of the creator's picture, not the canvas.
    Without a profile this is the house look."""
    from .plan import DEFAULT_PALETTE
    cw, ch = canvas
    pw, ph = picture
    top = (ch - ph) / 2 if fit == "box" else 0.0
    if not profile:
        return {"mode": mode, "grade": "neutral", "palette": dict(DEFAULT_PALETTE), "look": {}, "sfx_levels": None,
                "rhythm": None, "ramps": None, "moves": {}, "fx": [], "hero": True, "drop_frac": 0.0,
                "settings": {"montage_length": 21.0, "hero_length": 3.4, "velocity": True}}
    s = profile.get(mode) if profile.get(mode) and profile[mode]["count"] >= 3 else profile["all"]
    llm = profile.get("llm") or {}
    cap, g, rh = s["captions"], s["grade"], s["rhythm"]
    look = {}
    y = cap.get("y") or 0.82
    size = cap.get("size_frac") or 0.04
    look["capY"] = round(_clamp((top + y * ph) / ch, 0.12, 0.9), 3)
    look["capSize"] = round(_clamp(size / 0.72 * ph, 36.0, 120.0), 1)
    for k in ("capStyle", "capFont", "capMotion", "capAnim", "capFx"):
        if llm.get(k) is not None:
            look[k] = llm[k]
    look["capGlow"] = 0.25 if cap.get("polarity") == "light" and size < 0.05 else 1.0
    look["capDY"] = 0.0      # the measured y is where the text sits; no house drop below it
    look["vignette"] = round(_clamp((1 - (g.get("vignette") or 0.8)) * 1.2, 0.0, 0.9), 2)
    look["grain"] = round(_clamp((g.get("grain") or 0) / 8, 0.0, 0.25), 3)
    look["flash"] = round(_clamp((s["flash"]["per_min"] or 0) / 6, 0.0, 2.5), 2)
    look["shake"] = round(_clamp((s["shake"]["jitter"] or 0) / 1.5, 0.0, 2.0), 2)
    look["pulse"] = 1.0 if (rh.get("confidence") or 0) > 0.5 else 0.3
    grade = llm.get("grade") or g.get("nearest") or "neutral"
    palette = dict(DEFAULT_PALETTE)
    if llm.get("capColor"):   # one caption colour: emphasised words keep the creator's colour, not the house palette
        palette = {k: llm["capColor"] for k in palette}
    sfx = s["sfx"]
    outro = s["outro"]
    hero = (outro.get("mono") or 0) > 0.5 or (outro.get("card") or {}).get("black", 0) > 0.5
    sfx_levels = {"whoosh": round(_clamp((sfx.get("hits_at_offbeat_cuts") or 0) / 0.5 * 0.34, 0.0, 0.5), 3),
                  "impact": round(0.95 * (sfx.get("impact") or 0), 3),
                  "riser": round(0.5 * (sfx.get("riser") or 0), 3), "hero": 1.0 if hero else 0.0}
    pattern = rh.get("pattern")
    median_shot = s["shot_len"]["median"] or 1.0
    if pattern and rh.get("bpm"):   # pattern entries are multiples of `unit_beats` beats of the creator's songs
        target = (rh.get("unit_beats") or 1.0) * 60.0 / rh["bpm"]
    else:
        target = median_shot
    rhythm = {"pattern": pattern, "unit": [round(target * 0.8, 3), round(target * 1.25, 3)]}
    ramps_on = (s["ramps"]["fraction"] or 0) > 0.15
    ramps = [r if r in RAMP_KINDS else None for r in (s["ramps"].get("auto") or [])] if ramps_on else None
    moves = {m: w for m, w in (s.get("moves") or {}).items() if w > 0}
    for m in llm.get("moves") or []:      # the writer's picks weigh in beside the measurements
        moves[m] = moves.get(m, 0.0) + 0.2
    dur = s["duration"]["median"] or 20.0
    # effects go on every shot, so only the ones the measurements say are the norm, not the occasional insert
    fx = [f for f in (llm.get("fx") or [])
          if not (f == "mono" and (s["mono"]["fraction"] or 0) < 0.5)
          and not (f in ("shake", "wiggle", "twitch") and (s["shake"]["jitter"] or 0) < 1.5)
          and not (f == "strobe" and (s["flash"]["per_min"] or 0) < 20)]
    return {"mode": mode, "grade": grade, "palette": palette, "look": look, "sfx_levels": sfx_levels,
            "rhythm": rhythm, "ramps": ramps, "moves": moves, "fx": fx, "hero": hero,
            "drop_frac": rh.get("drop_frac") or 0.0,
            "settings": {"montage_length": round(_clamp(dur, 8.0, 40.0), 1),
                         "hero_length": round(_clamp(outro.get("shot_len") or 3.4, 2.0, 8.0), 2) if hero else 0.0,
                         "velocity": ramps_on}}


# ---------------------------------------------------------------- the report

def describe(profile: dict) -> str:
    L = []
    f = profile["frame"]
    L.append(f"STYLE {profile['name']}: {len(profile['reels'])} reels measured {profile['measured_at']}")
    L.append(f"Frame: {f['aspect']} ({f['width']}x{f['height']}, {f['fps']:.0f} fps); aspects seen {f['aspects']}")
    L.append("Modes: " + ", ".join(f"{m} {int(v * 100)}%" for m, v in profile["modes"].items())
             + "   (voiceover = talking over pictures, montage = music and cuts)")
    for m in ("all", "voiceover", "montage", "mixed"):
        s = profile.get(m)
        if not s:
            continue
        L.append("")
        L.append(f"== {m.upper()} ({s['count']} reels)")
        d = s["duration"]
        L.append(f"Length: median {d['median']}s (middle half {d['iqr'][0]}-{d['iqr'][1]}s), "
                 f"{s['cuts_per_reel']:.0f} cuts per reel, shots {s['shot_len']['p10']}-{s['shot_len']['p90']}s "
                 f"(median {s['shot_len']['median']}s)")
        r = s["rhythm"]
        if r["bpm"]:
            L.append(f"Music: {r['bpm']:.0f} bpm; {int((r['beat_align'] or 0) * 100)}% of cuts land on a beat "
                     f"(confidence {r['confidence']}, grid: {r['grid']}); drop at {int((r['drop_frac'] or 0) * 100)}% "
                     f"of the reel; cuts/s before {r['density_before']} after {r['density_after']}")
            if r["pattern"]:
                L.append(f"Cut pattern (in units of {r['unit_beats']} beat): {r['pattern']}  [from {r['from']}]")
        else:
            L.append("Music: none detected")
        rp = s["ramps"]
        L.append(f"Speed ramps: {int((rp['fraction'] or 0) * 100)}% of shots ramp; kinds {rp['kinds']}; "
                 f"sequence seen {rp['auto']}")
        L.append(f"Camera moves at cut (measured): {s['moves']}")
        g = s["grade"]
        L.append(f"Grade: nearest '{g['nearest']}'  luma {g['luma']:.0f}/255, contrast {g['contrast']:.0f}, "
                 f"saturation {g['sat']:.0f}, warmth {g['temp']:+.0f} (R-B), fade {g['fade']:.0f}, "
                 f"split {g['split']:+.0f}, vignette {g['vignette']:.2f}, grain {g['grain']:.2f}, sharp {g['sharp']:.0f}")
        c = s["captions"]
        L.append(f"Captions: on screen {int((c['fraction'] or 0) * 100)}% of the time, centre at y={c['y']} "
                 f"of the frame height, line height {c['size_frac']} of the frame, {c['polarity']} text, "
                 f"behaviour '{c['mode']}' {c['modes']}")
        L.append(f"Cards (screenshots / text cards): {int((s['cards'] or 0) * 100)}% of frames")
        L.append(f"Mono (black and white): {int((s['mono']['fraction'] or 0) * 100)}% of frames; intro "
                 f"{s['mono']['intro']}, outro {s['mono']['outro']}")
        L.append(f"Flashes: {s['flash']['per_min']} per minute ({int((s['flash']['at_cut_frac'] or 0) * 100)}% at cuts); "
                 f"shake jitter {s['shake']['jitter']} (0 = locked off, >2 = handheld)")
        L.append(f"Faces: in {int((s['faces']['fraction'] or 0) * 100)}% of frames, centre y={s['faces']['cy']}, "
                 f"size {s['faces']['size']}")
        L.append(f"Speech: {int((s['speech']['fraction'] or 0) * 100)}% of the time, {s['speech']['wpm']} wpm")
        L.append(f"Sound design: hits at {int((s['sfx']['hits_at_offbeat_cuts'] or 0) * 100)}% of off-beat cuts, "
                 f"riser before the drop in {int((s['sfx']['riser'] or 0) * 100)}%, impact on the drop in "
                 f"{int((s['sfx']['impact'] or 0) * 100)}%")
        L.append(f"Intro: first shot {s['intro']['shot_len']}s, text in the first second {int((s['intro']['text'] or 0) * 100)}%")
        L.append(f"Outro: last shot {s['outro']['shot_len']}s, text {int((s['outro']['text'] or 0) * 100)}%, "
                 f"mono {int((s['outro']['mono'] or 0) * 100)}%, card {s['outro']['card']}")
    if profile.get("llm"):
        L.append("")
        L.append("== LOOK (named from the toolkit, source: %s)" % profile["llm"].get("source"))
        for k in ("capStyle", "capFont", "capMotion", "capAnim", "capFx", "capColor", "moves", "fx", "grade"):
            if k in profile["llm"]:
                L.append(f"{k}: {profile['llm'][k]}")
        if profile["llm"].get("notes"):
            L.append("notes: " + profile["llm"]["notes"])
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- commands

def analyze(spec: str, speech: bool = True, provider: str = "auto", fresh: bool = False,
            limit: int | None = None) -> dict:
    files = reel_files(spec)
    if not files:
        raise SystemExit(f"no mp4 files for {spec} (expected styles/{spec}/reels/*.mp4 or a folder)")
    sdir = style_dir(spec)
    work, sheet_dir = sdir / "work", sdir / "sheets"
    work.mkdir(parents=True, exist_ok=True)
    if fresh:
        for f in work.glob("reel-*.json"):
            f.unlink()
    from .vision import FaceDetector
    faces = FaceDetector()
    reels, sheet_paths = [], []
    for f in files[:limit]:
        r = analyze_reel(f, work, faces, speech=speech)
        reels.append(r)
        if not r.get("error"):
            sheet_paths += sheets(f, sheet_dir, r)[:1]
    if speech:
        from . import transcribe
        transcribe.release()
    profile = aggregate(reels, sdir.name)
    # show the writer one sheet per mode, the longest reel of each
    picks = []
    for m in ("voiceover", "montage", "mixed"):
        cands = [r for r in reels if not r.get("error") and _mode_of(r) == m]
        if cands:
            best = max(cands, key=lambda r: r["duration"])
            picks.append(sheet_dir / f"{best['id']}.jpg")
    profile["llm"] = label(profile, [p for p in picks if p.exists()], provider)
    write_json(sdir / "style.json", profile)
    (sdir / "report.txt").write_text(describe(profile), encoding="utf-8")
    log(f"style: wrote {sdir / 'style.json'} and report.txt ({len(reels)} reels)")
    return profile


def compare(job_slug: str, spec: str) -> Path:
    """Measure a job's rendered video like a reel and print it beside the profile."""
    from .util import JOBS, slugify
    job = JOBS / slugify(job_slug)
    vids = sorted((p for p in job.glob("*.mp4") if "preview" not in p.name), key=lambda p: p.stat().st_mtime)
    if not vids:
        raise SystemExit(f"no rendered video in {job}")
    profile = load(spec)
    # measure inside the picture box the job drew, the way the creator's own frame was measured
    crop = None
    edit_js, settings = job / "render" / "edit.js", job / "settings.json"
    if edit_js.exists() and settings.exists() and read_json(settings).get("fit") == "box":
        import json
        edit = json.loads(edit_js.read_text(encoding="utf-8").removeprefix("window.EDIT=").rstrip().rstrip(";"))
        src = read_json(job / "sources.json")
        if src.get("montage"):
            info = probe(Path(src["montage"][0]))
            cw, ch = int(edit["width"]), int(edit["height"])
            scale = min(cw / info["width"], ch / info["height"])
            pw, ph = int(info["width"] * scale) // 2 * 2, int(info["height"] * scale) // 2 * 2
            crop = (pw, ph, (cw - pw) // 2, (ch - ph) // 2)
    r = analyze_reel(vids[-1], job / "qa", speech=False, crop=crop)
    plan_file = job / "plan.json"
    mode = read_json(plan_file).get("mode") if plan_file.exists() else None
    ref = profile[mode] if mode and profile.get(mode) and profile[mode]["count"] >= 3 else profile["all"]
    print(f"reference: {spec} {mode if ref is not profile['all'] else 'all'} reels")
    rows = [("shot len median", ref["shot_len"]["median"], float(np.median(r["shot_lens"])) if r["shot_lens"] else None),
            ("beat align", ref["rhythm"]["beat_align"], r["audio"].get("beat_align")),
            ("mono fraction", ref["mono"]["fraction"], r["mono"]["fraction"]),
            ("grade luma", ref["grade"]["luma"], r["grade"]["luma"]),
            ("grade sat", ref["grade"]["sat"], r["grade"]["sat"]),
            ("grade temp", ref["grade"]["temp"], r["grade"]["temp"]),
            ("caption y", ref["captions"]["y"], r["captions"]["y"]),
            ("caption size", ref["captions"]["size_frac"], r["captions"]["size_frac"]),
            ("flash/min", ref["flash"]["per_min"], r["flash"]["per_min"]),
            ("ramp fraction", ref["ramps"]["fraction"], r["ramps"]["fraction"])]
    lines = [f"{'measure':18s} {'profile':>10s} {'output':>10s} {'delta':>8s}"]
    for name, a, b in rows:
        d = f"{b - a:+.2f}" if a is not None and b is not None else "-"
        lines.append(f"{name:18s} {a if a is not None else '-':>10} {b if b is not None else '-':>10} {d:>8s}")
    out = job / "qa" / "style-compare.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))
    return out


PULL_HELP = """\
Instagram does not hand reels to yt-dlp any more, so pulling goes through a signed-in browser:

  1. In the Aside browser REPL (or any Playwright page that is logged in), run tools/ig_pull.js:
     it scrolls https://www.instagram.com/<handle>/reels/, collects every reel's shortcode, reads
     each reel page's video URL from its embedded JSON and PUTs the mp4 to http://127.0.0.1:4477/<code>.mp4.
  2. Receive the files with:  python -m easyedit.style pull <handle> --receive
     (a tiny local server writing into styles/<handle>/reels/; stop it with Ctrl+C when the REPL is done)
  3. python -m easyedit.style analyze <handle>

Or drop the mp4s into styles/<handle>/reels/ yourself; any folder of mp4s works with `analyze <folder>`.
"""


def receive(handle: str, port: int = 4477) -> None:
    import http.server
    import re
    root = style_dir(handle) / "reels"
    root.mkdir(parents=True, exist_ok=True)

    class H(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            name = Path(self.path).name
            if not re.fullmatch(r"[A-Za-z0-9_\-]+\.(mp4|json)", name):
                self.send_response(400); self.end_headers(); return
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            (root / name).write_bytes(data)
            log(f"received {name} ({len(data) // 1024} KB)")
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    log(f"receiving reels for {handle} on http://127.0.0.1:{port}/ into {root} (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="measure the reels into style.json + report.txt")
    a.add_argument("spec", help="style name (styles/<name>/reels) or a folder of mp4s")
    a.add_argument("--no-speech", action="store_true", help="skip transcription (faster, no voiceover split)")
    a.add_argument("--llm", default="auto", choices=["auto", "claude", "codex", "none"])
    a.add_argument("--fresh", action="store_true", help="ignore cached per-reel measurements")
    a.add_argument("--limit", type=int, help="only the first N reels (for a quick look)")
    r = sub.add_parser("report", help="print report.txt")
    r.add_argument("spec")
    tp = sub.add_parser("templates", help="list the measured reels, to pick one for --template")
    tp.add_argument("spec")
    c = sub.add_parser("compare", help="measure a job's render against a profile")
    c.add_argument("job")
    c.add_argument("spec")
    pl = sub.add_parser("pull", help="how to fetch a creator's reels; --receive runs the local receiver")
    pl.add_argument("handle")
    pl.add_argument("--receive", action="store_true")
    pl.add_argument("--port", type=int, default=4477)
    args = p.parse_args()
    if args.cmd == "analyze":
        analyze(args.spec, speech=not args.no_speech, provider=args.llm, fresh=args.fresh, limit=args.limit)
        print((style_dir(args.spec) / "report.txt").read_text(encoding="utf-8"))
    elif args.cmd == "report":
        print((style_dir(args.spec) / "report.txt").read_text(encoding="utf-8"))
    elif args.cmd == "templates":
        print(f"{'reel':14s} {'mode':10s} {'len':>6s} {'cuts':>5s} {'cards':>5s} {'hook':>5s} outro")
        for t in templates(args.spec):
            print(f"{t['id']:14s} {t['mode']:10s} {t['duration']:6.1f} {t['cuts']:5d} {t['cards']:5d} "
                  f"{t['hook']:5.1f} {t['outro'] or '-'}")
    elif args.cmd == "compare":
        compare(args.job, args.spec)
    elif args.cmd == "pull":
        if args.receive:
            receive(args.handle, args.port)
        else:
            print(PULL_HELP.replace("<handle>", args.handle))


if __name__ == "__main__":
    main()
