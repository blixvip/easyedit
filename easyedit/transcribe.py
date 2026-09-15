"""Word-level transcription with faster-whisper (GPU when available)."""
from __future__ import annotations

import os
from pathlib import Path

from .util import log, read_json, write_json

_model = None


def _load(name: str):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        try:
            _model = WhisperModel(name, device="cuda", compute_type="float16")
            log(f"whisper: {name} on cuda")
        except Exception:
            _model = WhisperModel(name, device="cpu", compute_type="int8")
            log(f"whisper: {name} on cpu (slow)")
    return _model


def release() -> None:
    """Free VRAM before face detection / Chrome rendering."""
    global _model
    if _model is not None:
        _model = None
        try:
            import gc
            import torch
            gc.collect()
            torch.cuda.empty_cache()
        except Exception:
            pass


def transcribe(media: Path, cache: Path, language: str | None = None) -> list[dict]:
    if cache.exists():
        return read_json(cache)
    name = os.environ.get("EASYEDIT_WHISPER", "distil-large-v3" if language in (None, "en") else "large-v3")
    model = _load(name)
    segments, _ = model.transcribe(
        str(media), language=language or "en", word_timestamps=True, vad_filter=True,
        condition_on_previous_text=False, beam_size=5,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"text": text, "start": round(w.start, 3), "end": round(w.end, 3),
                              "prob": round(w.probability, 3)})
    write_json(cache, words)
    log(f"whisper: {len(words)} words from {media.name}")
    return words
