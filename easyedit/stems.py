"""Audio splitter: pull the voice out of a clip, or the singing out of a song.

    python -m easyedit.stems <file> [start] [seconds]     writes <file>.voice.wav and <file>.music.wav

A movie scene usually has score under the line and a song usually has singing over the beat; an
edit wants the line clean and the beat clear. The split is an MDX-Net vocal model (ONNX) fed with
this module's own short-time Fourier transform; "music" is whatever is left once the voice is taken out.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from . import models
from .util import log

RATE = 44100
N_FFT, HOP, BINS, FRAMES = 7680, 1024, 3072, 256   # what the vocal model was trained on
CHUNK = HOP * (FRAMES - 1)
TRIM = N_FFT // 2
GAIN = 1.009                                        # the model's published level correction
KINDS = ("voice", "music")


def part(src: Path, start: float, seconds: float, kind: str, work: Path) -> Path | None:
    """One stem of a stretch of `src`, as a wav that starts at zero. None when the split failed."""
    stat = src.stat()
    tag = hashlib.sha256(json.dumps([str(src), stat.st_size, int(stat.st_mtime), round(start, 2),
                                     round(seconds, 2)]).encode()).hexdigest()[:16]
    folder = work / "stems"
    out = {k: folder / f"{tag}.{k}.wav" for k in KINDS}
    if not all(p.exists() for p in out.values()):
        folder.mkdir(parents=True, exist_ok=True)
        log(f"split: separating voice and music in {src.name} ({seconds:.0f}s)")
        code = subprocess.run([sys.executable, "-u", "-m", "easyedit.stems", str(src), f"{start:.3f}",
                               f"{seconds:.3f}", str(out["voice"]), str(out["music"])],
                              env=models.gpu_env("split")).returncode
        if code != 0 or not all(p.exists() for p in out.values()):
            log("split: failed; using the sound as it is")
            return None
    return out[kind]


def stft(wave):
    """(channels, samples) -> complex (channels, N_FFT/2+1, frames), centred, periodic Hann window."""
    import numpy as np
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)
    x = np.pad(wave, ((0, 0), (TRIM, TRIM)), mode="reflect")
    count = 1 + (x.shape[1] - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(count)[:, None]
    return np.fft.rfft(x[:, idx] * window, axis=-1).transpose(0, 2, 1)


def istft(spec, length: int):
    """The inverse of stft: overlap-add, levelled by the summed squared window."""
    import numpy as np
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1) * window
    count = frames.shape[1]
    out = np.zeros((spec.shape[0], N_FFT + HOP * (count - 1)))
    norm = np.zeros(out.shape[1])
    for i in range(count):
        out[:, i * HOP:i * HOP + N_FFT] += frames[:, i]
        norm[i * HOP:i * HOP + N_FFT] += window ** 2
    out /= np.maximum(norm, 1e-8)
    return out[:, TRIM:TRIM + length]


def separate(mix, run):
    """Voice from a stereo mix. `run` maps a (n, 4, BINS, FRAMES) array to the model's answer."""
    import numpy as np
    n = mix.shape[1]
    step = CHUNK - 2 * TRIM   # each pass keeps only its middle; the edges are where the model is unsure
    pad = step - n % step
    wide = np.concatenate([np.zeros((2, TRIM)), mix, np.zeros((2, pad)), np.zeros((2, TRIM))], axis=1)
    voice = []
    for at in range(0, n + pad, step):
        spec = stft(wide[:, at:at + CHUNK])                          # (2, N_FFT/2+1, FRAMES)
        x = np.stack([spec.real, spec.imag], axis=1)[:, :, :BINS]    # (2, 2, BINS, FRAMES)
        y = run(x.reshape(1, 4, BINS, FRAMES).astype(np.float32))[0].reshape(2, 2, BINS, FRAMES)
        full = np.zeros((2, N_FFT // 2 + 1, FRAMES), np.complex128)
        full[:, :BINS] = y[:, 0] + 1j * y[:, 1]
        voice.append(istft(full, CHUNK)[:, TRIM:-TRIM])
    return np.concatenate(voice, axis=1)[:, :n] * GAIN


def split(src: Path, start: float, seconds: float | None, voice_out: Path, music_out: Path) -> None:
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}"]
    if seconds:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    mix = np.frombuffer(raw, np.float32).reshape(-1, 2).T.astype(np.float64)
    if mix.shape[1] < RATE // 10:
        raise SystemExit("split: no sound to split")
    session = models.session(models.get("vocals", "split"))
    feed = session.get_inputs()[0].name
    log(f"split: {mix.shape[1] / RATE:.1f}s on {session.get_providers()[0].replace('ExecutionProvider', '')}")
    voice = separate(mix, lambda x: session.run(None, {feed: x})[0])
    for data, out in ((voice, voice_out), (mix - voice, music_out)):
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.stem + ".part.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", "-",
                        "-c:a", "pcm_s16le", str(tmp)],
                       input=np.clip(data, -1, 1).T.astype(np.float32).tobytes(), check=True)
        tmp.replace(out)
    log("split: done")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    source = Path(a[0])
    if len(a) >= 5:
        split(source, float(a[1]), float(a[2]) or None, Path(a[3]), Path(a[4]))
    else:
        if len(a) < 2:   # run by hand: start again in a process that sees the GPU runtime first
            raise SystemExit(subprocess.run([sys.executable, "-u", "-m", "easyedit.stems", a[0], "0"],
                                            env=models.gpu_env("split")).returncode)
        split(source, float(a[1]), float(a[2]) if len(a) > 2 else None,
              source.with_suffix(".voice.wav"), source.with_suffix(".music.wav"))
