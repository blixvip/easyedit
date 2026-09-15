"""Find shots in montage sources and score how 'edit-worthy' each one is."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .util import log, probe, read_json, write_json
from .vision import FaceDetector, frames, letterbox

SAMPLE_FPS = 12
HEAD_SKIP, TAIL_SKIP = 6.0, 12.0  # channel intros / end screens


def _cuts(diffs: np.ndarray) -> list[int]:
    cuts = []
    for i, d in enumerate(diffs):
        lo, hi = max(0, i - 6), min(len(diffs), i + 7)
        local = np.median(diffs[lo:hi])
        if d > 22 and d > 3.2 * max(local, 2.0) and (not cuts or i - cuts[-1] > 4):
            cuts.append(i)
    return cuts


def analyze(src: Path, cache: Path, faces: FaceDetector) -> dict:
    if cache.exists():
        return read_json(cache)
    import cv2
    info = probe(src)
    dur = info["duration"]
    crop = letterbox(src, dur)
    times, thumbs, grays, sats = [], [], [], []
    for t, img in frames(src, SAMPLE_FPS, 160, crop=crop, height=90):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        times.append(t)
        grays.append(g)
        thumbs.append(cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32))
        sats.append(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 1].mean())
    if len(grays) < SAMPLE_FPS * 3:
        return {"source": str(src), "shots": [], "crop": crop}
    G = np.stack(grays).astype(np.float32)
    diffs = np.abs(np.diff(np.stack(thumbs), axis=0)).mean(axis=(1, 2, 3))
    bounds = [0] + [c + 1 for c in _cuts(diffs)] + [len(G)]

    shots = []
    for a, b in zip(bounds, bounds[1:]):
        t0, t1 = times[a] + 0.1, times[b - 1] - 0.1
        if t1 - t0 < 0.7 or t0 < HEAD_SKIP or t1 > dur - TAIL_SKIP:
            continue
        seg = G[a:b]
        bright, contrast = float(seg.mean()), float(seg.std())
        dark_frac = float((seg < 22).mean())
        if bright < 28 or contrast < 20 or dark_frac > 0.72 or bright > 225:
            continue
        motion = float(np.abs(np.diff(seg, axis=0)).mean()) if len(seg) > 1 else 0.0
        mid = (a + b) // 2
        sharp = float(cv2.Laplacian(G[mid], cv2.CV_32F).var())
        shots.append({"start": round(t0, 3), "end": round(t1, 3), "bright": bright, "contrast": contrast,
                      "motion": motion, "sharp": sharp, "sat": float(np.mean(sats[a:b])),
                      "thumb": [round(float(v), 1) for v in thumbs[mid].mean(axis=2).ravel()]})

    # face check on each shot's middle frame at higher res
    W = info["width"] if not crop else crop[0]
    H = info["height"] if not crop else crop[1]
    # one streaming pass at 2fps; detect only on frames nearest each shot's middle
    want = {}
    for s in shots:
        s["face"] = None
        want.setdefault(int(round((s["start"] + s["end"]) / 2 * 2)), []).append(s)
    for t, img in frames(src, 2, 480, crop=crop):
        hit = want.get(int(round(t * 2)))
        if not hit:
            continue
        found = sorted(faces.detect(img), key=lambda f: -f[2])
        if found and found[0][2] > 0.08:
            for s in hit:
                s["face"] = [round(found[0][0], 4), round(found[0][1], 4), round(found[0][2], 4)]

    out = {"source": str(src), "duration": dur, "crop": crop, "size": [W, H], "shots": shots}
    write_json(cache, out)
    log(f"shots: {len(shots)} usable of {len(bounds) - 1} in {src.name}")
    return out


def _z(values: list[float]) -> np.ndarray:
    v = np.array(values, dtype=np.float32)
    return (v - v.mean()) / (v.std() + 1e-6)


def rank(analyses: list[dict]) -> list[dict]:
    pool = []
    for si, a in enumerate(analyses):
        for s in a["shots"]:
            pool.append({**s, "src": si})
    if not pool:
        return []
    motion = _z([np.log1p(s["motion"]) for s in pool])
    sharp = _z([np.log1p(s["sharp"]) for s in pool])
    contrast = _z([s["contrast"] for s in pool])
    sat = _z([s["sat"] for s in pool])
    for i, s in enumerate(pool):
        face = 0.0 if not s["face"] else 0.4 + min(s["face"][2], 0.5)
        length = min(s["end"] - s["start"], 3.0) / 3.0
        s["score"] = float(0.35 * motion[i] + 0.25 * sharp[i] + 0.2 * contrast[i] + 0.15 * sat[i]
                           + face + 0.2 * length)
    pool.sort(key=lambda s: -s["score"])
    return pool


def _similar(a: dict, b: dict) -> bool:
    x, y = np.array(a["thumb"]), np.array(b["thumb"])
    x, y = x - x.mean(), y - y.mean()
    return float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-6)) > 0.93


def select(pool: list[dict], durations: list[float], hero_len: float) -> tuple[list[dict], dict | None]:
    """Choose shots for each montage slot (in slot order) plus one hero outro shot."""
    chosen: list[dict] = []
    hero = next((s for s in pool if s["end"] - s["start"] >= hero_len and s["face"]), None) \
        or next((s for s in pool if s["end"] - s["start"] >= hero_len), None)
    taken = [hero] if hero else []
    need = len(durations)
    for s in pool:
        if len(chosen) >= need * 2:
            break
        if any(s is t or _similar(s, t) for t in taken):
            continue
        chosen.append(s)
        taken.append(s)
    if not chosen:
        return [], hero
    # best `need` shots, then story order (source, time) so the montage flows
    picks = sorted(chosen[:need], key=lambda s: (s["src"], s["start"]))
    spare = chosen[need:]
    slots: list[dict] = []
    for i, d in enumerate(durations):
        shot = picks[i % len(picks)] if picks else None
        if shot and shot["end"] - shot["start"] < d:
            fit = next((s for s in spare if s["end"] - s["start"] >= d), None)
            if fit:
                spare.remove(fit)
                shot = fit
        slots.append(shot)
    return slots, hero
