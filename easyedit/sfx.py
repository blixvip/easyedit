"""Sound design made from scratch with numpy: a whoosh on every cut, a riser into the drop,
an impact on the drop and on the final shot. No sample packs, nothing to license."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def _noise(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n).astype(np.float32)


def _lp(x: np.ndarray, taps: int) -> np.ndarray:
    """A box low-pass: crude, cheap, and all a whoosh needs."""
    return np.convolve(x, np.ones(taps, np.float32) / taps, mode="same")


def _stereo(y: np.ndarray, pan: np.ndarray) -> np.ndarray:
    return np.stack([y * np.sqrt(1 - pan), y * np.sqrt(pan)], axis=1)


def whoosh(seed: int, dur: float = 0.44, left_to_right: bool = True) -> tuple[np.ndarray, float]:
    """Air rushing past, loudest and brightest 62% of the way in. Returns (stereo, seconds to the peak)."""
    n = int(dur * SR)
    x = _noise(n, seed)
    u = np.linspace(0, 1, n, dtype=np.float32)
    peak = 0.62
    env = np.exp(-((u - peak) / 0.19) ** 2)
    bright = np.exp(-((u - peak) / 0.26) ** 2)
    low, mid, high = _lp(x, 120) * 3.2, (_lp(x, 20) - _lp(x, 90)) * 2.2, _lp(x, 5) - _lp(x, 20)
    y = (low * (1 - bright) + mid * bright + high * bright * 0.7) * env
    y /= np.abs(y).max() + 1e-6
    pan = 0.2 + 0.6 * u if left_to_right else 0.8 - 0.6 * u
    return _stereo(y, pan), peak * dur


def impact(seed: int, dur: float = 1.0) -> np.ndarray:
    """A sub thump with a click on the front and a short rumble behind it."""
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    freq = 38 + 62 * np.exp(-t * 18)
    sub = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 5.2)
    x = _noise(n, seed)
    click = _lp(x, 8) * np.exp(-t * 70) * 0.7
    rumble = _lp(x, 220) * 6 * np.exp(-t * 6.5) * 0.35
    y = np.tanh(1.7 * (sub + click + rumble))
    return _stereo(y, np.full(n, 0.5, np.float32))


def riser(seed: int, dur: float = 1.8) -> np.ndarray:
    """Noise that brightens and swells, cut dead at the end so the drop lands in silence-then-hit."""
    n = int(dur * SR)
    u = np.linspace(0, 1, n, dtype=np.float32)
    x = _noise(n, seed)
    air = _lp(x, 70) * 3 * (1 - u) + (_lp(x, 6) - _lp(x, 36)) * u * 1.6
    sweep = np.sin(2 * np.pi * np.cumsum(180 + 1100 * u ** 2) / SR) * 0.18
    y = (air + sweep) * u ** 2.4
    y[-int(0.012 * SR):] *= np.linspace(1, 0, int(0.012 * SR), dtype=np.float32)
    y /= np.abs(y).max() + 1e-6
    wobble = 0.5 + 0.25 * np.sin(2 * np.pi * 3.1 * u)
    return _stereo(y, wobble.astype(np.float32))


def build(out: Path, total: float, drop: float, cuts: list[float], hero: float) -> Path:
    """Write the effects track for one edit. `cuts` are the montage cut times after the drop."""
    buf = np.zeros((int(total * SR) + SR, 2), np.float32)

    def place(sound: np.ndarray, at: float, gain: float) -> None:
        i = int(at * SR)
        if i < 0:
            sound, i = sound[-i:], 0
        j = min(len(buf), i + len(sound))
        if j > i:
            buf[i:j] += sound[: j - i] * gain

    if drop > 2.4:
        r = riser(7)
        place(r, drop - len(r) / SR, 0.5)
    place(impact(11), drop, 0.95)
    for k, t in enumerate(cuts):
        w, lead = whoosh(100 + k, left_to_right=k % 2 == 0)
        place(w, t - lead, 0.34)
    w, lead = whoosh(999, dur=0.7)
    place(w, hero - lead, 0.5)
    place(impact(13, dur=1.4), hero, 1.0)
    pcm = (np.clip(buf[: int(total * SR)], -1, 1) * 32767).astype(np.int16)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm.tobytes())
    return out
