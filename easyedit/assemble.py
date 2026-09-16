"""Build the edit: timeline math, face track, footage cut, soundtrack mix, edit.js."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .beats import find_drop
from .shots import select
from .util import clamp, log, probe, run
from .vision import FaceDetector, cover_map, frames, letterbox

W, H = 1920, 1080
LEAD, TAIL = 0.4, 0.45
PATTERN = [2, 2, 1, 3, 2, 1, 1, 2, 3, 2, 1, 2, 2, 1, 3, 1, 1, 2, 2, 4]


def montage_slots(beats: list[float], drop: int, montage_len: float, hero_len: float):
    """Cut points on real beat times. Returns (durations, beat_offsets, hero_duration)."""
    rel = [b - beats[drop] for b in beats[drop:]]
    period = float(np.median(np.diff(rel[:32]))) if len(rel) > 2 else 0.5
    unit = 1
    while period * unit < 0.36:
        unit *= 2
    while period * unit > 0.95 and unit > 1:
        unit //= 2
    if period * unit > 0.95:  # very slow song: split beats in half
        rel = sorted(set(rel + [(a + b) / 2 for a, b in zip(rel, rel[1:])]))
    cuts, k, i = [0.0], 0, 0
    while True:
        k += PATTERN[i % len(PATTERN)] * unit
        i += 1
        if k >= len(rel):
            break
        cuts.append(rel[k])
        if rel[k] >= montage_len:
            break
    durations = [b - a for a, b in zip(cuts, cuts[1:])]
    hero_start = cuts[-1]
    hero_end = hero_start + hero_len
    beat_marks = [r for r in rel if r <= hero_end]
    return durations, beat_marks, hero_len


def track_faces(src: Path, start: float, dur: float, crop, size, fd: FaceDetector) -> list:
    to_px = cover_map(size[0], size[1], W, H)
    raw = []
    prev = None
    for t, img in frames(src, 10, 640, start=start, duration=dur, crop=crop):
        found = fd.detect(img)
        if prev and found:
            face = min(found, key=lambda f: (f[0] - prev[0]) ** 2 + (f[1] - prev[1]) ** 2 - 0.3 * f[2])
        else:
            face = max(found, key=lambda f: f[2]) if found else None
        if face and face[2] > 0.06:
            prev = face
            x, y = to_px(face[0], face[1])
            raw.append((t - start, x, y, face[2] * H))
        else:
            raw.append((t - start, None, None, None))
    hits = [r for r in raw if r[1] is not None]
    if len(hits) < max(3, len(raw) * 0.25):
        return []
    ts = np.array([r[0] for r in raw])
    ht = np.array([r[0] for r in hits])
    cols = [np.interp(ts, ht, [r[i] for r in hits]) for i in (1, 2, 3)]
    k = np.ones(5) / 5
    smooth = [np.convolve(np.pad(c, 2, mode="edge"), k, mode="valid") for c in cols]
    return [[round(float(t), 2), round(float(x), 1), round(float(y), 1), round(float(s), 1)]
            for t, x, y, s in zip(ts, *smooth)]


def _vf(crop, fps: int) -> str:
    parts = ["crop=%d:%d:%d:%d" % tuple(crop)] if crop else []
    parts += [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}",
              f"fps={fps}", "setsar=1", "format=yuv420p"]
    return ",".join(parts)


def cut(src: Path, start: float, nframes: int, crop, fps: int, out: Path) -> None:
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", src, "-frames:v", nframes,
         "-vf", _vf(crop, fps), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
         "-r", fps, "-g", fps, out])


def mix(speech: Path, speech_start: float, speech_len: float, music: Path, music_start: float,
        total: float, out: Path) -> None:
    pre = max(0.0, -music_start)
    m0 = max(0.0, music_start)
    # volume runs before adelay, so its clock starts `pre` seconds into the timeline
    duck = f"volume='0.15+0.85*clip((t-{speech_len - 0.06 - pre:.3f})/0.06,0,1)':eval=frame"
    graph = (
        f"[0:a]atrim=start={speech_start:.3f}:duration={speech_len:.3f},asetpts=PTS-STARTPTS,"
        f"aresample=48000,loudnorm=I=-15:TP=-2:LRA=9,afade=t=in:d=0.04,"
        f"afade=t=out:st={speech_len - 0.2:.3f}:d=0.2[sp];"
        f"[1:a]atrim=start={m0:.3f},asetpts=PTS-STARTPTS,aresample=48000,loudnorm=I=-14:TP=-1.5:LRA=11,"
        f"{duck},adelay={pre * 1000:.0f}:all=1,afade=t=out:st={total - 1.8:.3f}:d=1.8[mu];"
        f"[sp][mu]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.93,"
        f"atrim=duration={total:.3f}[a]"
    )
    run(["ffmpeg", "-v", "error", "-y", "-i", speech, "-i", music, "-filter_complex", graph,
         "-map", "[a]", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", out])


def build(job: Path, *, plan: dict, quote: dict, speech: Path, analyses: list[dict], pool: list[dict],
          music: Path, music_beats: dict, fps: int, montage_len: float, hero_len: float) -> dict:
    work = job / "work"
    render = job / "render"
    (work / "segments").mkdir(parents=True, exist_ok=True)
    render.mkdir(parents=True, exist_ok=True)
    fd = FaceDetector()

    sinfo = probe(speech)
    s_crop = letterbox(speech, sinfo["duration"])
    s_size = (s_crop[0], s_crop[1]) if s_crop else (sinfo["width"], sinfo["height"])
    s_start = max(0.0, quote["start"] - LEAD)
    speech_f = round((quote["end"] + TAIL - s_start) * fps)
    M = speech_f / fps

    drop = find_drop(music_beats, montage_len + hero_len + 1)
    durations, beat_marks, hero_len = montage_slots(music_beats["beats"], drop, montage_len, hero_len)
    slots, hero = select(pool, durations, hero_len)
    if not slots or any(s is None for s in slots):
        raise RuntimeError("not enough usable montage shots; add more sources with --montage")
    music_start = music_beats["beats"][drop] - M

    # frame-exact timeline
    bounds = [speech_f]
    acc = M
    for d in durations:
        acc += d
        bounds.append(round(acc * fps))
    hero_f = round(hero_len * fps)
    total_f = bounds[-1] + hero_f
    segs = [(speech, s_start, speech_f, s_crop)]
    shots = []
    for i, (slot, a, b) in enumerate(zip(slots, bounds, bounds[1:])):
        n = b - a
        src = analyses[slot["src"]]
        usable = slot["end"] - slot["start"]
        start = slot["start"] + max(0.0, usable - n / fps) * 0.3
        segs.append((Path(src["source"]), start, n, src["crop"]))
        shots.append({"index": i, "start": a, "end": b, "face": _face_px(slot, src)})
    hero_shot = hero or slots[-1]
    hsrc = analyses[hero_shot["src"]]
    hstart = hero_shot["start"] + max(0.0, (hero_shot["end"] - hero_shot["start"]) - hero_len) * 0.25
    segs.append((Path(hsrc["source"]), hstart, hero_f, hsrc["crop"]))
    shots.append({"index": len(shots), "start": bounds[-1], "end": total_f, "face": _face_px(hero_shot, hsrc),
                  "hero": True})

    log(f"timeline: speech {M:.2f}s + {len(durations)} beat cuts + hero {hero_len:.1f}s "
        f"= {total_f / fps:.2f}s @ {fps}fps")
    listing = []
    for k, (src, start, n, crop) in enumerate(segs):
        seg = work / "segments" / f"seg-{k:03d}.mp4"
        cut(src, start, n, crop, fps, seg)
        listing.append(f"file '{seg.as_posix()}'\n")
    (work / "segments.txt").write_text("".join(listing), encoding="utf-8")
    footage = render / "footage.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", work / "segments.txt",
         "-c", "copy", footage])
    soundtrack = job / "work" / "soundtrack.m4a"
    total = total_f / fps
    mix(speech, s_start, M, music, music_start, total, soundtrack)

    faces = track_faces(speech, s_start, M, s_crop, s_size, fd)
    log(f"faces: {'tracked ' + str(len(faces)) + ' samples' if faces else 'none found, centered punch-in'}")
    captions = [{"start": round(line[0]["start"] - s_start, 3),
                 "end": round(line[-1]["end"] - s_start, 3),
                 "words": [{"text": w["text"], "t": round(w["start"] - s_start, 3), "role": w["role"]}
                           for w in line]} for line in quote["lines"]]
    for a, b in zip(captions, captions[1:]):  # hold each line until the next arrives (max 1.2s)
        a["end"] = round(min(b["start"], a["end"] + 1.2), 3)
    captions[-1]["end"] = round(min(M, captions[-1]["end"] + 0.5), 3)

    edit = {
        "fps": fps, "width": W, "height": H, "frames": total_f, "montageStart": speech_f,
        "title": plan["title"], "palette": plan["palette"], "grade": plan.get("grade", "neutral"),
        "captions": captions, "faces": faces, "shots": shots,
        "beats": [round(M + b, 3) for b in beat_marks],
    }
    (render / "edit.js").write_text("window.EDIT=" + json.dumps(edit, ensure_ascii=False, default=lambda o: o.item()) + ";\n",
                                    encoding="utf-8")
    got = probe(footage)
    log(f"footage: {got['duration']:.2f}s (expected {total:.2f}s)")
    return edit


def _face_px(shot: dict, src: dict):
    if not shot.get("face"):
        return None
    to_px = cover_map(src["size"][0], src["size"][1], W, H)
    x, y = to_px(shot["face"][0], shot["face"][1])
    return [round(clamp(x, 0, W), 1), round(clamp(y, 0, H), 1)]
