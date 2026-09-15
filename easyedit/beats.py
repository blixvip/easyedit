"""Beat tracking and 'drop' detection with numpy only (spectral flux + DP beat tracker)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .util import log, read_json, write_json

SR, HOP, NFFT = 22050, 512, 2048


def load_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def onset_envelope(y: np.ndarray) -> np.ndarray:
    n = 1 + (len(y) - NFFT) // HOP
    if n <= 8:
        return np.zeros(8)
    win = np.hanning(NFFT).astype(np.float32)
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(y[idx] * win, axis=1))
    # mel-ish compression: log magnitude, emphasize lows where kicks live
    logspec = np.log1p(100 * spec[:, : NFFT // 4])
    flux = np.maximum(0, np.diff(logspec, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    flux = np.maximum(flux, 0)
    return flux / (flux.max() + 1e-9)


def tempo(env: np.ndarray) -> float:
    fps = SR / HOP
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    bpms = np.arange(70, 181)
    lags = fps * 60 / bpms
    strength = np.interp(lags, np.arange(len(ac)), ac)
    prior = np.exp(-0.5 * (np.log2(bpms / 120) / 0.9) ** 2)
    return float(bpms[np.argmax(strength * prior)])


def track(env: np.ndarray, bpm: float, tightness: float = 100) -> np.ndarray:
    """Ellis (2007) dynamic-programming beat tracker. Returns beat times in seconds."""
    fps = SR / HOP
    period = fps * 60 / bpm
    score = env.copy()
    back = np.full(len(env), -1)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    for i in range(lo, len(env)):
        prev = np.arange(max(0, i - hi), i - lo + 1)
        if not len(prev):
            continue
        penalty = -tightness * np.log((i - prev) / period) ** 2
        cand = score[prev] + penalty
        j = int(np.argmax(cand))
        score[i] = env[i] + cand[j]
        back[i] = prev[j]
    tail = max(0, len(score) - int(period))
    i = tail + int(np.argmax(score[tail:]))
    beats = []
    while i >= 0:
        beats.append(i)
        i = back[i]
    beats = np.array(beats[::-1], dtype=np.float32)
    return beats / fps


def analyze(path: Path, cache: Path) -> dict:
    if cache.exists():
        return read_json(cache)
    y = load_audio(path)
    env = onset_envelope(y)
    bpm = tempo(env)
    beats = track(env, bpm)
    fps = SR / HOP
    # RMS loudness per beat, for drop detection
    rms_frames = np.sqrt(np.convolve(y ** 2, np.ones(HOP) / HOP, mode="same")[::HOP] + 1e-12)
    loud = [float(rms_frames[int(b * fps): int(b * fps) + int(fps * 0.5)].mean()) if int(b * fps) < len(rms_frames) else 0
            for b in beats]
    strength = [float(env[max(0, int(b * fps) - 2): int(b * fps) + 3].max()) if int(b * fps) < len(env) else 0
                for b in beats]
    out = {"duration": len(y) / SR, "bpm": bpm, "beats": [round(float(b), 3) for b in beats],
           "loud": loud, "strength": strength}
    write_json(cache, out)
    log(f"beats: {bpm:.0f} bpm, {len(beats)} beats over {out['duration']:.0f}s")
    return out


def find_drop(a: dict, need: float, min_t: float = 0.0) -> int:
    """Index of the beat where the music hits hardest, leaving `need` seconds after it."""
    beats, loud, strength = a["beats"], np.array(a["loud"]), np.array(a["strength"])
    if len(beats) < 8:
        return 0
    per = np.median(np.diff(beats))
    look = max(4, int(round(6 / per)))
    best, best_i = -1e9, 0
    for i, t in enumerate(beats):
        if t < min_t or t + need > a["duration"]:
            continue
        before = loud[max(0, i - look): i].mean() if i > 0 else loud[:look].mean() * 0.5
        after = loud[i: i + look * 2].mean()
        score = (after - before) / (after + before + 1e-6) + 0.35 * after / (loud.max() + 1e-6) \
            + 0.15 * strength[i]
        if score > best:
            best, best_i = score, i
    return best_i
