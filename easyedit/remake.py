"""Lay out the user's own footage the way a measured creator cuts: a sequence (timeline.json) the
ordinary sequence build then cuts, so the editor can still move every clip afterwards.

Two structures:
  voiceover  the narration runs from t=0 on A1, pictures cut underneath it, the song ducked,
             captions wherever the words are heard
  montage    the song's drop at t=0, beat cuts for the profile's typical length, an optional final shot

Two sources of rhythm:
  profile    the creator's average cut pattern walked over the song's beat grid
  template   one of the creator's reels, shot for shot: its cut times stretched to this edit's length,
             its card inserts, its opening hold and its closing card

In voiceover edits every cut is then nudged onto the nearest word start, and every sentence starts on a
cut, which is what makes a cut feel spoken rather than metronomic.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from . import assemble, sequence, shots
from .beats import find_drop
from .util import log

r3 = sequence.r3
MIN_SHOT = 0.2          # seconds; nothing shorter survives snapping
SNAP = 0.15             # a cut this close to a word start moves onto it
SENTENCE_GAP = 0.3      # a sentence start this far from any cut gets its own


def _weighted(rng: random.Random, weights: dict[str, float]) -> str | None:
    items = [(k, w) for k, w in weights.items() if w > 0]
    if not items:
        return None
    x = rng.random() * sum(w for _, w in items)
    for k, w in items:
        x -= w
        if x <= 0:
            return k
    return items[-1][0]


def _grid_slots(grid: list[float], total: float, rhythm: dict | None) -> list[float]:
    """Shot lengths walking a beat grid (edit seconds, first entry 0) for `total` seconds."""
    durations, _, _ = assemble.montage_slots(grid, 0, total, 0.0, "normal", rhythm)
    out, acc = [], 0.0
    for d in durations:
        if acc + d >= total - 0.3:   # this shot reaches the end: it takes whatever is left
            break
        out.append(d)
        acc += d
    out.append(total - acc)          # the last shot runs to the end (the grid may also have run out early)
    return out


def _template_slots(tpl: dict, total: float) -> list[dict]:
    """The template's body shots stretched to `total` seconds: [{len, move, ramp, card}]."""
    body = [s for s in tpl["shots"]]
    span = tpl["body"] or sum(s["len"] for s in body) or 1.0
    k = total / span
    out = []
    for s in body:
        out.append({"len": max(MIN_SHOT, s["len"] * k), "move": s.get("move"), "ramp": s.get("ramp"),
                    "card": bool(s.get("card"))})
    drift = total - sum(s["len"] for s in out)
    out[-1]["len"] = max(MIN_SHOT, out[-1]["len"] + drift)
    return out


def _snap(cuts: list[float], total: float, words: list[dict] | None) -> list[float]:
    """Cut times (edit seconds, excluding 0 and total) nudged onto word starts, with a cut at the start
    of every sentence."""
    if not words:
        return cuts
    starts = sorted(w["t"] for w in words if 0 < w["t"] < total)
    sentence = sorted(w["t"] for w in words if w.get("sentence") and 0.4 < w["t"] < total - 0.4)
    out = []
    for c in cuts:
        near = min(starts, key=lambda s: abs(s - c), default=None)
        out.append(near if near is not None and abs(near - c) <= SNAP else c)
    for s in sentence:
        if all(abs(s - c) > SENTENCE_GAP for c in out):
            out.append(s)
    out = sorted(set(round(c, 3) for c in out if MIN_SHOT < c < total - MIN_SHOT))
    cleaned = []
    for c in out:   # no two cuts closer than a minimum shot
        if not cleaned or c - cleaned[-1] >= MIN_SHOT:
            cleaned.append(c)
        elif c - cleaned[-1] < MIN_SHOT and cleaned[-1] not in sentence and c in sentence:
            cleaned[-1] = c
    return cleaned


def speech_words(raw: list[dict], speech_in: float, speed: float = 1.0, hold: float = 0.0) -> list[dict]:
    """Transcript words as edit-time starts (the narration starts after `hold`), flagged where a sentence begins."""
    out = []
    prev_end = ""
    for i, w in enumerate(raw):
        text = str(w.get("text", "")).strip()
        t = hold + (w["start"] - speech_in) / speed
        out.append({"t": round(t, 3), "text": text,
                    "sentence": i == 0 or bool(re.search(r"[.!?]$", prev_end))})
        prev_end = text
    return out


def generate(job: Path, *, sources: dict, analyses: list[dict], pool: list[dict], beats: dict,
             quote: dict | None, fps: int, applied: dict, settings: dict, mode: str,
             speech_in: float = 0.0, speech_len: float = 0.0, words: list[dict] | None = None,
             template: dict | None = None, cards: list[str] | None = None, cta: str | None = None,
             hook_hold: float = 0.0) -> dict:
    """The sequence the profile (or one template reel) makes of this footage.

    `cards`: source keys ("card:<name>") of insert clips, used in order wherever the template has a card
    (or, without a template, spread through the edit). `cta`: the source key of a closing card clip."""
    rhythm = applied.get("rhythm")
    ramps = applied.get("ramps") or [None]
    velocity = bool(settings.get("velocity", True))
    hero_len = float(settings.get("hero_length") or 0.0) if applied.get("hero") else 0.0
    bts = beats["beats"]
    if len(bts) < 4:
        raise ValueError("the song is too short or has no beat")
    cards = list(cards or [])
    outro_len = 0.0
    if template and template["outro"]["len"] and cta:
        outro_len = float(template["outro"]["len"])
        hero_len = 0.0

    # ---- how long, and where the song sits
    if mode == "voiceover":
        total = max(2.0, speech_len) + max(0.0, hook_hold)    # the hook holds, then the voice starts
        drop_frac = float(applied.get("drop_frac") or 0.0)
        if template and template.get("drop_t") and template["duration"]:
            drop_frac = min(0.6, template["drop_t"] / template["duration"])
        drop = find_drop(beats, max(1.0, total * (1 - drop_frac)) + 1)
        ms = bts[drop] - drop_frac * total        # song time at edit t=0
    else:
        total = float(settings.get("montage_length") or 21.0)
        if template:
            total = float(template["body"])
        drop = find_drop(beats, total + hero_len + outro_len + 1)
        ms = bts[drop]

    # ---- the cut list
    slot_meta: list[dict] = []
    if template:
        slot_meta = _template_slots(template, total)
        durations = [s["len"] for s in slot_meta]
    elif mode == "voiceover":
        grid = [b - ms for b in bts if b - ms >= -0.02]
        grid = [0.0] + [g for g in grid if g > 0.12]
        if len(grid) < 4:
            period = 60.0 / max(60.0, float(beats.get("bpm") or 120))
            grid = [k * period for k in range(int(total / period) + 8)]
        durations = _grid_slots(grid, total, rhythm)
    else:
        durations, _, hero_len = assemble.montage_slots(bts, drop, total, hero_len, "normal", rhythm)
    if mode == "voiceover" and words:
        cuts, acc = [], 0.0
        for d in durations[:-1]:
            acc += d
            cuts.append(acc)
        snapped = _snap(cuts, total, words)
        bounds_s = [0.0, *snapped, total]
        new_durations = [b - a for a, b in zip(bounds_s, bounds_s[1:])]
        if slot_meta:   # keep each template shot's character with its nearest new slot
            meta, merged = [], []
            acc, j = 0.0, 0
            for d in new_durations:
                mid = acc + d / 2
                while j < len(slot_meta) - 1 and sum(s["len"] for s in slot_meta[: j + 1]) < mid:
                    j += 1
                m = slot_meta[j]
                if meta and m is meta[-1] and m.get("card"):   # a card is one held picture: no cuts inside it
                    merged[-1] += d
                else:
                    meta.append(m)
                    merged.append(d)
                acc += d
            slot_meta, new_durations = meta, merged
        durations = new_durations

    # ---- which shot in which slot
    card_slots = [i for i, m in enumerate(slot_meta) if m.get("card")] if slot_meta else []
    card_slots = card_slots[: len(cards)]
    extra = len(cards) - len(card_slots)
    if extra > 0:   # more pictures than the template has card slots: spread the rest, footage between them
        n = len(durations)
        taken = set(card_slots)
        free = [i for i in range(1, n) if i not in taken and i - 1 not in taken and i + 1 not in taken]
        stride = max(2, round(len(free) / extra)) if free else 2
        picked = []
        for i in free[stride // 2::stride]:
            if len(picked) >= extra:
                break
            if all(abs(i - p) >= 2 for p in picked):   # a picture, then footage, then a picture
                picked.append(i)
        card_slots = sorted(taken | set(picked))
    card_slots = card_slots[: len(cards)]
    footage_slots = [d for i, d in enumerate(durations) if i not in set(card_slots)]
    slots, hero = shots.select(pool, footage_slots, max(hero_len, 0.5))
    if not slots or any(s is None for s in slots):
        raise ValueError("not enough usable shots in the footage for this length; add clips or shorten it")

    rng = random.Random(7)
    moves = applied.get("moves") or {}
    fx = applied.get("fx") or []
    v1, bounds = [], [0]
    acc = 0.0
    for d in durations:
        acc += d
        bounds.append(round(acc * fps))
    it = iter(slots)
    card_it = iter(cards)
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        if b <= a:
            continue
        meta = slot_meta[i] if i < len(slot_meta) else {}
        if i in card_slots:
            key = next(card_it)
            v1.append({"id": f"cd{i}", "src": key, "kind": "shot", "at": r3(a / fps), "dur": r3((b - a) / fps),
                       "in": 0.0, "opts": {"move": "punch", "fx": ["dissolve"]}})
            continue
        slot = next(it)
        opts = dict(slot.get("opts") or {})
        if meta.get("ramp") is not None or meta:
            opts.setdefault("ramp", meta.get("ramp") or "none")
        else:
            opts.setdefault("ramp", ramps[i % len(ramps)] or "none")
        if "move" not in opts:
            opts["move"] = meta.get("move") or (_weighted(rng, moves) if moves else None) or "cut"
        if fx and "fx" not in opts:
            opts["fx"] = list(fx)
        v1.append(sequence._shot_clip(f"sh{i}", {**slot, "opts": opts}, a, b, fps, analyses, i, velocity))
    end_f = bounds[-1]
    if outro_len > 0 and cta:
        n = round(outro_len * fps)
        v1.append({"id": "cta", "src": cta, "kind": "shot", "at": r3(end_f / fps), "dur": r3(n / fps), "in": 0.0,
                   "opts": {"move": "cut"}})
        end_f += n
    elif hero_len > 0 and hero:
        hero_f = round(hero_len * fps)
        v1.append(sequence._shot_clip("hero", hero, end_f, end_f + hero_f, fps, analyses, None, velocity))
        end_f += hero_f
    end = end_f / fps

    a1 = []
    if mode == "voiceover" and sources.get("speech"):
        key = f"speech:{Path(sources['speech'][(quote or {}).get('clip', 0)]).name}"
        a1.append({"id": "dl0", "src": key, "kind": "speech", "at": r3(max(0.0, hook_hold)),
                   "dur": r3(total - max(0.0, hook_hold)), "in": r3(speech_in)})
    at = max(0.0, -ms)
    a2 = [{"id": "song", "src": f"music:{Path(sources['music'][0]).name}", "kind": "music", "at": r3(at),
           "in": r3(max(0.0, ms)), "dur": r3(max(1.0, end - at)), "duck": True}]
    log(f"remake: {mode}{' from reel ' + template['reel'] if template else ''}, {len(durations)} cuts over "
        f"{total:.1f}s, {len(card_slots)} card inserts"
        f"{', closing card' if outro_len > 0 and cta else (' + final shot' if hero_len > 0 and hero else '')}, "
        f"song from {ms:.2f}s")
    return {"v": sequence.VERSION, "fps": fps, "video": [v1, []], "audio": [a1, a2], "off": []}
