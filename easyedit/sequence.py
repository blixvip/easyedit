"""The edit as a sequence: clips on video and audio tracks, laid out the way an editor lays them out.

A project starts as choices (the quote, the shots, the song) that easyedit cuts to the beat. The editor
writes that cut down as an explicit sequence (timeline.json): every clip says which file it plays, from
where, how fast, for how long and where it sits. From then on the editor edits the sequence, the preview
plays it as it stands, and an export cuts exactly it.

    {"v": 1, "fps": 60,
     "video": [[clip, ...] (V1), [clip, ...] (V2)],    # a higher track covers a lower one
     "audio": [[clip, ...] (A1), [clip, ...] (A2)],    # every audio track is heard
     "off": ["V2"]}                                    # tracks switched off

    clip = {"id", "src": "montage:abc.mp4", "kind": "speech" | "shot" | "hero" | "music" | "audio",
            "at": seconds on the timeline, "dur": seconds on the timeline, "in": seconds into the source,
            "speed": 1, "ramp": None | "in" | "out" | "hit", "rspan": [0, 1], "opts": {...},
            "link": id of the clip it moves with, "shot": the bin shot it came from, "gain": 1, "duck": True}

Times are seconds, not frames, so the same sequence exports at 60 fps or as a 30 fps draft.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from .util import log, probe, read_json, run, write_json

FILE = "timeline.json"
VERSION = 1
ROLES = ("speech", "montage", "music", "card")   # card: generated inserts and end cards
KINDS = ("speech", "shot", "hero", "music", "audio")
MAX_TRACKS, MAX_CLIPS = 6, 800
W, H = 1920, 1080
r3 = lambda x: round(float(x), 3)  # noqa: E731


# ---------- the files a sequence plays ----------
def files(job: Path) -> dict[str, Path]:
    """Every source of the edit by its key: "role:name"."""
    src = read_json(job / "sources.json")
    return {f"{role}:{Path(f).name}": Path(f) for role in ROLES for f in src.get(role, [])}


def _facts(job: Path, path: Path) -> dict:
    """Length, size and frame rate of a source, remembered until the file changes."""
    cache_file = job / "work" / "media.json"
    try:
        cache = read_json(cache_file) if cache_file.exists() else {}
    except ValueError:
        cache = {}
    st = path.stat()
    stamp = [round(st.st_mtime, 3), st.st_size]
    entry = cache.get(str(path))
    if not entry or entry.get("stamp") != stamp:
        info = probe(path)
        entry = {"stamp": stamp, "duration": round(info.get("duration") or 0, 3), "width": info.get("width"),
                 "height": info.get("height"), "fps": round(info.get("fps") or 0, 3), "hasAudio": info["has_audio"]}
        cache[str(path)] = entry
        write_json(cache_file, cache)
    return {k: v for k, v in entry.items() if k != "stamp"}


def crop_of(job: Path, role: str, path: Path):
    """The picture area of a source in its own pixels (w, h, x, y), or None: letterbox bars, and for
    speech clips burned-in subtitles, are cut away. Speech crops are read once and remembered."""
    if role == "montage":
        cache = job / "work" / f"shots-{path.stem}.json"
        crop = read_json(cache).get("crop") if cache.exists() else None
        return tuple(crop) if crop else None
    if role != "speech" or not path.exists():
        return None
    cache = job / "work" / f"crop-{path.stem}.json"
    st = path.stat()
    stamp = [round(st.st_mtime, 3), st.st_size]
    if cache.exists():
        got = read_json(cache)
        if got.get("stamp") == stamp:
            return tuple(got["crop"]) if got.get("crop") else None
    from .assemble import speech_crop
    crop = speech_crop(path)
    write_json(cache, {"stamp": stamp, "crop": list(crop) if crop else None})
    return crop


def media(job: Path) -> dict:
    """What the live preview needs to play every source: where to stream it, how long it is, and the
    part of its picture that is the film (as fractions, so a small working copy crops the same way)."""
    out = {}
    src = read_json(job / "sources.json")
    for role in ROLES:
        for i, f in enumerate(src.get(role, [])):
            p = Path(f)
            key = f"{role}:{p.name}"
            stream = f"/api/edit/{job.name}/source?" + urlencode({"k": role, "i": i, "n": p.name, "quality": "original"})
            m = {"key": key, "role": role, "index": i, "name": p.name, "exists": p.is_file(),
                 "audio": stream + "&audio=1"}
            if p.is_file():
                try:
                    m.update(_facts(job, p))
                except Exception as e:   # one unreadable file must not hide the others
                    m["error"] = str(e)[:120]
            if role != "music":
                m["video"] = stream
                crop = None
                if p.is_file() and m.get("width"):
                    try:
                        crop = crop_of(job, role, p)
                    except Exception as e:
                        log(f"crop: {p.name}: {e}")
                if crop:
                    w, h = m["width"], m["height"]
                    m["crop"] = [r3(crop[2] / w), r3(crop[3] / h), r3((crop[2] + crop[0]) / w), r3((crop[3] + crop[1]) / h)]
            out[key] = m
    return out


# ---------- from the beat cut to a sequence ----------
def generate(inp: dict, s: dict) -> dict:
    """The cut the saved choices make (speech, shots on the beat, the final shot, the song), written out
    as a sequence. Clip ids are their places, so asking twice gives the same ids."""
    from . import assemble, shots
    q, an, cur = inp["quote"], inp["analyses"], inp["curation"] or {}
    pool = shots.rank(an)
    if cur:
        pool = shots.curate(pool, an, cur)
    fps = s["fps"]
    tl = assemble.timeline(q, pool, inp["beats"], fps, s["montage_length"], s["hero_length"], s["cut_rate"],
                           cur.get("cuts"))
    if not tl["slots"] or any(x is None for x in tl["slots"]):
        raise ValueError("Not enough usable shots. Bring some removed shots back or shorten the montage.")
    src = inp["sources"]
    speech = f"speech:{Path(src['speech'][q.get('clip', 0)]).name}"
    M = tl["speech_f"] / fps
    v1 = [{"id": "sp0", "src": speech, "kind": "speech", "at": 0.0, "dur": r3(M), "in": r3(tl["speech_start"]),
           "link": "dl0"}]
    a1 = [{"id": "dl0", "src": speech, "kind": "speech", "at": 0.0, "dur": r3(M), "in": r3(tl["speech_start"]),
           "link": "sp0"}]
    for i, (slot, a, b) in enumerate(zip(tl["slots"], tl["bounds"], tl["bounds"][1:])):
        v1.append(_shot_clip(f"sh{i}", slot, a, b, fps, an, i, s["velocity"]))
    hero = tl["hero"] or tl["slots"][-1]
    v1.append(_shot_clip("hero", hero, tl["bounds"][-1], tl["total_f"], fps, an, None, s["velocity"]))
    total = tl["total_f"] / fps
    ms = tl["music_start"]
    at = max(0.0, -ms)
    a2 = [{"id": "song", "src": f"music:{Path(inp['music']).name}", "kind": "music", "at": r3(at),
           "in": r3(max(0.0, ms)), "dur": r3(total - at), "duck": True}]
    return {"v": VERSION, "fps": fps, "video": [v1, []], "audio": [a1, a2], "off": []}


def _shot_clip(cid: str, slot: dict, a: int, b: int, fps: int, analyses: list[dict], i: int | None,
               velocity: bool) -> dict:
    """One montage slot (or the final shot, i None) as a clip, playing exactly what the beat cut plays."""
    from . import assemble, shots
    n = b - a
    opts = slot.get("opts") or {}
    usable = slot["end"] - slot["start"]
    auto = ("in" if velocity else None) if i is None else (assemble.AUTO_RAMPS[i % len(assemble.AUTO_RAMPS)] if velocity else None)
    times, used = assemble._retime(opts, auto, usable, n / fps, n)
    lap = slot.get("lap", 0)
    slip = opts.get("slip", 0.25 if i is None else assemble.LAP_SLIPS[lap % len(assemble.LAP_SLIPS)])
    start = slot["start"] + max(0.0, usable - used) * float(slip)
    clip = {"id": cid, "src": f"montage:{Path(analyses[slot['src']]['source']).name}",
            "kind": "hero" if i is None else "shot", "at": r3(a / fps), "dur": r3(n / fps), "in": r3(start),
            "shot": shots.shot_id(analyses, slot)}
    speed = float(opts.get("speed") or 1.0)
    kind = opts.get("ramp") or auto
    if times is not None:
        ramp = kind if kind in assemble.RAMPS and n / fps >= 0.45 else None
        shape = assemble.RAMPS[ramp]((np.arange(n) + 0.5) / n) if ramp else np.ones(n)
        need = n / fps * float(shape.mean()) * speed
        speed = speed * (used / need if need > 0 else 1.0)   # the speeds were eased to fit a short shot
        if ramp:
            clip["ramp"] = ramp
        clip["speed"] = r3(speed)
    else:
        clip["speed"] = r3(assemble._speed(opts, usable, n / fps))
    film = assemble._film_opts(opts)
    if film:
        clip["opts"] = film
    return clip


def music_clip(job: Path, seq: dict, montage_len: float, hero_len: float) -> dict:
    """The song laid under the sequence so its drop lands where the shots start."""
    from .beats import find_drop
    src = read_json(job / "sources.json")
    song = Path(src["music"][0])
    beats = read_json(job / "work" / f"beats-{song.stem}.json")
    drop = find_drop(beats, montage_len + hero_len + 1)
    fps = seq.get("fps", 60)
    segs, end = flatten(seq, fps)
    m = next((g["start"] / fps for g in segs if g["clip"] and g["clip"]["kind"] in ("shot", "hero")), 0.0)
    ms = beats["beats"][drop] - m
    at = max(0.0, -ms)
    return {"id": "song", "src": f"music:{song.name}", "kind": "music", "at": r3(at), "in": r3(max(0.0, ms)),
            "dur": r3(max(1.0, end / fps - at)), "duck": True}


# ---------- keeping a sequence sound ----------
def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(x) else min(hi, max(lo, x))


def clean(job: Path, raw: dict) -> dict:
    """A sequence from the page, made safe: known files only, sane numbers, unique ids."""
    from . import assemble, editor
    if not isinstance(raw, dict):
        raise ValueError("that is not a sequence")
    have = files(job)
    songs = [k for k in have if k.startswith("music:")]
    fps = int(_num(raw.get("fps"), 1, 120, 60))
    fps = fps if fps in (24, 30, 60) else 60
    seen, count = set(), 0
    off = raw.get("off") if isinstance(raw.get("off"), list) else []
    track_ids = {f"{kind}{i}" for kind in ("V", "A") for i in range(1, MAX_TRACKS + 1)}
    out = {"v": VERSION, "fps": fps, "video": [], "audio": [],
           "off": list(dict.fromkeys(t for t in off if isinstance(t, str) and t in track_ids))}
    for kind in ("video", "audio"):
        tracks = raw.get(kind) if isinstance(raw.get(kind), list) else []
        for track in tracks[:MAX_TRACKS]:
            clean_track = []
            for c in track if isinstance(track, list) else []:
                if isinstance(c, dict) and str(c.get("src", "")).startswith("music:") and c["src"] not in have and len(songs) == 1:
                    c = {**c, "src": songs[0]}   # the song was changed: its clip plays the new one
                if not isinstance(c, dict) or not isinstance(c.get("src"), str) or c["src"] not in have or count >= MAX_CLIPS:
                    continue
                role = c["src"].split(":", 1)[0]
                if kind == "video" and role == "music":
                    continue
                cid = str(c.get("id") or "")[:24] or f"c{count}"
                while cid in seen:
                    cid += "x"
                seen.add(cid)
                k = c.get("kind") if c.get("kind") in KINDS else ("music" if role == "music" else "shot" if kind == "video" else "audio")
                clip = {"id": cid, "src": c["src"], "kind": k,
                        "at": r3(_num(c.get("at"), 0, 3600, 0)), "dur": r3(_num(c.get("dur"), 1 / fps, 3600, 1)),
                        "in": r3(_num(c.get("in"), 0, 36000, 0))}
                speed = r3(_num(c.get("speed"), 0.1, 8, 1))
                if abs(speed - 1) > 1e-3:
                    clip["speed"] = speed
                if isinstance(c.get("ramp"), str) and c["ramp"] in assemble.RAMPS:
                    clip["ramp"] = c["ramp"]
                    span = c.get("rspan")
                    if isinstance(span, list) and len(span) == 2:
                        u0, u1 = _num(span[0], 0, 1, 0), _num(span[1], 0, 1, 1)
                        if u1 > u0 and (u0, u1) != (0, 1):
                            clip["rspan"] = [r3(u0), r3(u1)]
                if kind == "video" and isinstance(c.get("opts"), dict):
                    opts = editor._clean_shot_opts({"x": c["opts"]}, {"x"}).get("x", {})
                    opts.pop("speed", None), opts.pop("ramp", None), opts.pop("slip", None)
                    if opts:
                        clip["opts"] = opts
                for key in ("link", "shot"):
                    if isinstance(c.get(key), str) and len(c[key]) <= 80:
                        clip[key] = c[key]
                if kind == "audio":
                    gain = r3(_num(c.get("gain"), 0, 4, 1))
                    if abs(gain - 1) > 1e-3:
                        clip["gain"] = gain
                    if k == "music":
                        clip["duck"] = c.get("duck") is not False
                if c.get("off") is True:
                    clip["off"] = True
                clean_track.append(clip)
                count += 1
            clean_track.sort(key=lambda x: x["at"])
            out[kind].append(clean_track)
    while len(out["video"]) < 2:
        out["video"].append([])
    while len(out["audio"]) < 2:
        out["audio"].append([])
    ids = {c["id"] for kind in ("video", "audio") for t in out[kind] for c in t}
    for kind in ("video", "audio"):
        for t in out[kind]:
            for c in t:
                if c.get("link") not in ids:
                    c.pop("link", None)
    return out


def load(job: Path) -> dict | None:
    f = job / FILE
    if not f.exists():
        return None
    try:
        return clean(job, read_json(f))
    except (ValueError, KeyError, OSError) as e:
        log(f"sequence: {FILE} could not be read ({e}); the beat cut stands in")
        return None


def save(job: Path, raw: dict) -> dict:
    seq = clean(job, raw)
    write_json(job / FILE, seq)
    return seq


# ---------- playing a sequence ----------
def frames(c: dict, fps: int) -> tuple[int, int]:
    a = round(c["at"] * fps)
    return a, max(a + 1, round((c["at"] + c["dur"]) * fps))


def times_of(c: dict, fps: int) -> np.ndarray | None:
    """Seconds after the clip's in-point that each of its frames shows, or None for real-time-or-faster
    footage that a plain cut handles. Speed ramps keep their shape across a split (rspan)."""
    from .assemble import RAMPS
    a, b = frames(c, fps)
    n = b - a
    speed = c.get("speed", 1.0)
    if c.get("ramp") in RAMPS:
        u0, u1 = c.get("rspan") or (0.0, 1.0)
        v = RAMPS[c["ramp"]](u0 + (np.arange(n) + 0.5) / n * (u1 - u0)) * speed
    elif speed < 0.99:
        v = np.full(n, speed)
    else:
        return None
    return (np.cumsum(v) - v / 2) / fps


def flatten(seq: dict, fps: int) -> tuple[list[dict], int]:
    """What the picture shows, frame by frame: the top clip wherever clips are stacked, black in gaps.
    Returns pieces {clip | None, start, end} in frames, and the sequence's length in frames."""
    off = set(seq.get("off") or [])
    clips = [(ti, c) for ti, track in enumerate(seq.get("video") or []) if f"V{ti + 1}" not in off
             for c in track if not c.get("off")]
    spans = [(ti, c, *frames(c, fps)) for ti, c in clips]
    audio_ends = [frames(c, fps)[1] for ti, track in enumerate(seq.get("audio") or [])
                  if f"A{ti + 1}" not in off for c in track if not c.get("off")]
    end = max([b for *_, b in spans] + audio_ends, default=0)
    if end <= 0:
        return [], 0
    marks = sorted({0, end} | {a for *_, a, _ in spans} | {b for *_, b in spans})
    pieces: list[dict] = []
    for a, b in zip(marks, marks[1:]):
        top = None
        for ti, c, ca, cb in spans:
            if ca <= a and cb >= b and (top is None or ti >= top[0]):
                top = (ti, c)
        clip = top[1] if top else None
        if pieces and pieces[-1]["clip"] is clip:
            pieces[-1]["end"] = b
        else:
            pieces.append({"clip": clip, "start": a, "end": b})
    return pieces, end


def captions(seq: dict, quote: dict, speech_key: str) -> list[dict]:
    """The caption lines where their words are heard: through every dialogue clip of the quote's
    speech clip, wherever it now sits. Words a clip leaves out are not shown."""
    off = set(seq.get("off") or [])
    dialogue = [c for ti, t in enumerate(seq.get("audio") or []) if f"A{ti + 1}" not in off
                for c in t if c["src"] == speech_key and not c.get("off") and c.get("gain", 1) > 0]
    lines = []
    for c in dialogue:
        sp = c.get("speed", 1.0)
        clip_end = c["at"] + c["dur"]
        for line in quote.get("lines") or []:
            words = []
            for w in line:
                if c["in"] - 1e-3 <= w["start"] < c["in"] + c["dur"] * sp:
                    words.append({"text": w["text"], "t": r3(c["at"] + (w["start"] - c["in"]) / sp),
                                  "end": min(clip_end, c["at"] + (w["end"] - c["in"]) / sp), "role": w.get("role")})
            if words:
                words.sort(key=lambda w: w["t"])
                lines.append({"start": words[0]["t"], "end": r3(words[-1]["end"]), "clipEnd": clip_end,
                              "words": [{"text": w["text"], "t": w["t"], "role": w["role"]} for w in words]})
    lines.sort(key=lambda line: line["start"])
    for a, b in zip(lines, lines[1:]):  # hold each line until the next arrives (max 1.2s)
        a["end"] = r3(min(a["clipEnd"], b["start"], a["end"] + 1.2))
    if lines:
        lines[-1]["end"] = r3(min(lines[-1]["clipEnd"], lines[-1]["end"] + 0.5))
    for line in lines:
        line.pop("clipEnd", None)
    return lines


def _hook_caption(plan: dict, total: float) -> list[dict]:
    """A hook line held on screen from the first frame: plan.hook = {"text": "...", "hold": seconds}
    (hold 0 or missing means the whole edit)."""
    hook = plan.get("hook") or {}
    text = str(hook.get("text") or "").strip()
    if not text:
        return []
    hold = float(hook.get("hold") or 0) or total
    words = [{"text": w, "t": 0.0, "role": None} for w in text.split()]
    return [{"start": 0.0, "end": r3(min(total, hold)), "words": words}]


def key(seq: dict, quote: dict, plan: dict, fps: int, sfx: float, analyses: list[dict],
        canvas=None, fit: str = "cover") -> str:
    """Everything an export of this sequence depends on: the same key means the cut footage still fits."""
    from .assemble import BUILD_VERSION
    quote = quote or {}
    parts = ["seq-v4", BUILD_VERSION, {k: v for k, v in seq.items() if k != "fps"}, quote.get("lines"),
             quote.get("clip"), plan, fps, round(sfx, 2), [(a["source"], a["crop"]) for a in analyses]]
    if canvas and tuple(canvas) != (W, H):   # left out by default so older edits keep their keys
        parts.append(["canvas", *canvas])
    if fit != "cover":
        parts.append(f"fit={fit}")
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# ---------- cutting a sequence ----------
def _black(out: Path, n: int, fps: int, canvas=None) -> None:
    cw, ch = canvas or (W, H)
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={cw}x{ch}:r={fps}", "-frames:v", n,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", "-r", fps, "-g", fps, out])


def _atempo(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def mix(seq: dict, have: dict[str, Path], total: float, out: Path, effects: Path | None, effects_level: float) -> None:
    """The soundtrack: every audio clip where it sits, the song ducked under dialogue, the effects on top."""
    off = set(seq.get("off") or [])
    clips = [c for ti, t in enumerate(seq.get("audio") or []) if f"A{ti + 1}" not in off for c in t if not c.get("off")]
    audible = {}
    for c in clips:
        path = have[c["src"]]
        if path not in audible:
            audible[path] = probe(path).get("has_audio", False)
    clips = [c for c in clips if audible[have[c["src"]]] and c.get("gain", 1) > 0]
    dialogue = [(c["at"], c["at"] + c["dur"]) for c in clips if c["kind"] == "speech"]
    inputs, chains, labels = [], [], []
    input_count = 0
    for k, c in enumerate(clips):
        sp = c.get("speed", 1.0)
        dur = min(c["dur"], max(0.05, total - c["at"]))
        if dur <= 0.02 or c["at"] >= total:
            continue
        n = input_count   # each clip reads its own stretch of its file: a seek, not a decode from the start
        inputs += ["-ss", f"{c['in']:.3f}", "-t", f"{dur * sp + 0.25:.3f}", "-i", str(have[c["src"]])]
        input_count += 1
        chain = (f"[{n}:a]asetpts=PTS-STARTPTS,atrim=duration={dur * sp:.3f},"
                 "aresample=48000,aformat=channel_layouts=stereo")
        if abs(sp - 1) > 1e-3:
            chain += "," + _atempo(sp)
        if dur >= 1.5:   # loudness is measured over a few seconds; a short piece just keeps its level
            chain += ",loudnorm=I=-15:TP=-2:LRA=9" if c["kind"] == "speech" else ",loudnorm=I=-14:TP=-1.5:LRA=11"
        fade_out = 1.8 if c["kind"] == "music" and c["at"] + dur >= total - 0.05 else min(0.2, dur / 3)
        chain += f",afade=t=in:d={min(0.04, dur / 4):.3f},afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}"
        if abs(c.get("gain", 1.0) - 1) > 1e-3:
            chain += f",volume={c['gain']:.3f}"
        if c["kind"] == "music" and c.get("duck", True) and dialogue:
            # the song drops to 15% while anyone speaks, with a short ramp either side
            at = c["at"]
            win = [f"clip((t+{at:.3f}-{a - 0.06:.3f})/0.06,0,1)*clip(({b + 0.06:.3f}-t-{at:.3f})/0.06,0,1)" for a, b in dialogue]
            expr = win[0]
            for w in win[1:]:
                expr = f"max({expr},{w})"
            chain += f",volume='1-0.85*{expr}':eval=frame"
        chain += f",adelay={c['at'] * 1000:.0f}:all=1[a{k}]"
        chains.append(chain)
        labels.append(f"[a{k}]")
    if effects and effects_level > 0:
        n = input_count
        inputs += ["-i", str(effects)]
        chains.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={effects_level:.3f}[fx]")
        labels.append("[fx]")
    if not labels:
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{total:.3f}",
             "-c:a", "aac", "-b:a", "256k", out])
        return
    graph = ";".join(chains) + ";" + "".join(labels) + (
        f"amix=inputs={len(labels)}:normalize=0:duration=longest,alimiter=limit=0.93,"
        f"apad=whole_dur={total:.3f},atrim=duration={total:.3f}[a]")
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[a]",
         "-c:a", "aac", "-b:a", "256k", "-ar", "48000", out])


def build(job: Path, seq: dict, *, plan: dict, quote: dict | None, analyses: list[dict], fps: int, sfx: float,
          canvas=None, fit: str = "cover") -> dict:
    """Cut the sequence: the footage frame by frame as the picture shows it, the soundtrack, the faces to
    follow, the captions where they are heard, and edit.js for the film layer.

    `canvas` (w, h) and `fit` (cover | box) say how the pictures land on the frame; the defaults are the
    1920x1080 cover crop every older edit used."""
    from . import assemble, shots as shot_mod
    from . import sfx as sound_fx
    from .vision import FaceDetector
    canvas = tuple(canvas or (W, H))
    quote = quote or {}
    have = files(job)
    pieces, end = flatten(seq, fps)
    if not pieces or end <= 0:
        raise RuntimeError("the sequence is empty: put something on a video track")
    work, render = job / "work", job / "render"
    (work / "segments").mkdir(parents=True, exist_ok=True)
    render.mkdir(parents=True, exist_ok=True)
    by_id = {}
    for a in analyses:
        for s in a["shots"]:
            by_id[f"{Path(a['source']).stem}@{s['start']:.2f}"] = (s, a)
    fd, faces, face_src, shots, listing = None, [], {}, [], []
    montage_index = 0
    for k, p in enumerate(pieces):
        n, c = p["end"] - p["start"], p["clip"]
        seg = work / "segments" / f"seg-{k:03d}.mp4"
        entry = {"index": montage_index, "start": p["start"], "end": p["end"]}
        if c is None:
            _black(seg, n, fps, canvas)
            entry.update(id=f"gap{k}", gap=True)
        else:
            path = have[c["src"]]
            role = c["src"].split(":", 1)[0]
            crop = crop_of(job, role, path)
            k0 = p["start"] - frames(c, fps)[0]
            times = times_of(c, fps)
            sp = c.get("speed", 1.0)
            hit = by_id.get(c.get("shot") or "") if c["kind"] != "speech" else None
            anchor = assemble.anchor_for(hit[0].get("face") if hit else None, canvas)
            if times is None:
                start = c["in"] + k0 / fps * sp
                assemble.cut(path, start, n, crop, fps, seg, sp, canvas, anchor, fit)
            else:
                part = times[k0:k0 + n]
                lead = float(part[0]) if k0 == 0 else float(part[1] - part[0]) / 2 if len(part) > 1 else 0.0
                start = c["in"] + float(part[0]) - lead
                assemble.cut_ramped(path, start, part - float(part[0]) + lead, crop, fps, seg, canvas, anchor, fit)
            entry["id"] = c["id"]
            entry.update(assemble._film_opts(c.get("opts") or {}))
            if c["kind"] == "speech":
                entry["speech"] = True
                if fd is None:
                    fd = FaceDetector()
                info = probe(path)
                size = (crop[0], crop[1]) if crop else (info["width"], info["height"])
                src_start = c["in"] + k0 / fps * sp
                got = assemble.track_faces(path, src_start, n / fps * sp, crop, size, fd, canvas, anchor, fit)
                faces += [[round(p["start"] / fps + t / sp, 2), x, y, s] for t, x, y, s in got]
                face_src.setdefault(c["src"], []).extend([[round(src_start + t, 2), x, y, s] for t, x, y, s in got])
            else:
                if c["kind"] == "hero":
                    entry["hero"] = True
                if hit:
                    entry["face"] = assemble._face_px(hit[0], hit[1], canvas, anchor, fit)
                montage_index += 1
        shots.append(entry)
        listing.append(f"file '{seg.as_posix()}'\n")
    (work / "segments.txt").write_text("".join(listing), encoding="utf-8")
    footage = render / "footage.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", work / "segments.txt", "-c", "copy", footage])
    total = end / fps

    montage = next((s["start"] for s in shots if not s.get("speech") and not s.get("gap")), end)
    hero_at = next((s["start"] for s in shots if s.get("hero")), None)
    hero = end if hero_at is None else hero_at
    cuts = [s["start"] / fps for s in shots if s["start"] > montage and not s.get("gap") and not s.get("speech")]
    effects = None
    if sfx > 0:
        effects = sound_fx.build(work / "sfx.wav", total, montage / fps, [t for t in cuts if t < hero / fps],
                                 None if hero_at is None else hero / fps, plan.get("sfx_levels"))
    mix(seq, have, total, work / "soundtrack.m4a", effects, sfx)

    src = read_json(job / "sources.json")
    speech_files = src.get("speech") or []
    speech_key = f"speech:{Path(speech_files[quote.get('clip', 0)]).name}" if speech_files and quote else None
    beats = []
    song = next((c for t in seq.get("audio") or [] for c in t if c["kind"] == "music" and not c.get("off")), None)
    if song:
        bfile = work / f"beats-{Path(have[song['src']]).stem}.json"
        if bfile.exists():
            sp = song.get("speed", 1.0)
            beats = [r3(song["at"] + (b - song["in"]) / sp) for b in read_json(bfile)["beats"]
                     if song["in"] <= b <= song["in"] + song["dur"] * sp and song["at"] + (b - song["in"]) / sp <= total]
    edit = {
        "v": 2, "fps": fps, "width": canvas[0], "height": canvas[1], "frames": end, "montageStart": montage,
        "title": plan["title"], "year": plan.get("year"), "palette": plan["palette"],
        "grade": plan.get("grade", "neutral"), "look": plan.get("look") or {},
        "captions": _hook_caption(plan, total) + (captions(seq, quote, speech_key) if speech_key else []),
        "faces": sorted(faces), "faceSrc": face_src,
        "shots": shots, "beats": beats,
    }
    (render / "edit.js").write_text("window.EDIT=" + json.dumps(edit, ensure_ascii=False, default=lambda o: o.item()) + ";\n",
                                    encoding="utf-8")
    log(f"timeline: sequence of {len(pieces)} pieces = {total:.2f}s @ {fps}fps")
    got = probe(footage)
    log(f"footage: {got['duration']:.2f}s (expected {total:.2f}s)")
    return edit
