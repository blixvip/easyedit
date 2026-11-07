"""Pick a track for an edit the way the creator's reels sound: searched by the profile's own queries,
then chosen by tempo (nearest the creator's typical bpm, half and double tempo allowed) and by how hard
the drop hits. The user's own track always wins (--music <file>); this is for when none was given."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import beats as beatmod
from . import fetch
from .util import log, read_json, write_json

DEFAULT_QUERIES = ["phonk edit audio instrumental", "dark phonk instrumental drop", "edit audio instrumental hard drop"]


def _drop_strength(a: dict) -> float:
    """How much louder the music gets at its drop, 0..1."""
    loud = np.array(a.get("loud") or [])
    if len(loud) < 16:
        return 0.0
    i = beatmod.find_drop(a, 8.0)
    before = loud[max(0, i - 8): i].mean() if i > 0 else loud[:8].mean()
    after = loud[i: i + 16].mean()
    return float(max(0.0, (after - before) / (after + before + 1e-6)))


def _tempo_fit(bpm: float, want: float) -> float:
    """1 when the tempos match (also at half or double tempo), 0 when far."""
    best = min(abs(np.log2(bpm / (want * k))) for k in (0.5, 1.0, 2.0))
    return float(max(0.0, 1.0 - best / 0.35))


def pick(job: Path, profile: dict | None, dest: Path, queries: list[str] | None = None,
         cookies: str | None = None, per_query: int = 2) -> Path:
    """Download a few candidates and keep the one that fits the profile; remembered in job/music-pick.json."""
    memo = job / "music-pick.json"
    if memo.exists():
        got = read_json(memo)
        if Path(got["path"]).exists():
            log(f"music: {Path(got['path']).name} (picked before: {got['why']})")
            return Path(got["path"])
    want = float(((profile or {}).get("all") or {}).get("rhythm", {}).get("bpm") or 120.0)
    queries = queries or (profile or {}).get("music_queries") or DEFAULT_QUERIES
    cands: list[tuple[float, Path, str]] = []
    for q in queries:
        try:
            hits = fetch.search(q, per_query, 60, 420)
        except Exception as e:
            log(f"music search failed for {q!r}: {e}")
            continue
        for h in hits:
            try:
                p = fetch.download(h["url"], dest, True, cookies)
                a = beatmod.analyze(p, job / "work" / f"beats-{p.stem}.json")
            except Exception as e:
                log(f"music candidate skipped ({h['title'][:40]}): {e}")
                continue
            fit, drop = _tempo_fit(a["bpm"], want), _drop_strength(a)
            title = h["title"].lower()
            kind = 0.0
            if any(k in title for k in ("instrumental", "type beat", "phonk", "beat")):
                kind += 0.1          # an instrumental made for edits
            if any(k in title for k in ("lyrics", "remix", "cover", "sped up", "slowed", "official video", '"')):
                kind -= 0.2          # someone's song, reworked
            score = 0.6 * fit + 0.4 * drop + kind
            why = f"{a['bpm']:.0f} bpm (want {want:.0f}, fit {fit:.2f}), drop {drop:.2f}, kind {kind:+.1f}"
            log(f"music: {h['title'][:50]!r}: {why} -> {score:.2f}")
            cands.append((score, p, why))
    if not cands:
        raise RuntimeError("no music candidate could be fetched; pass --music <file|url>")
    score, path, why = max(cands, key=lambda c: c[0])
    write_json(memo, {"path": str(path), "why": why, "score": round(score, 3)})
    log(f"music: picked {path.name}: {why}")
    return path
