"""Build the edit: timeline math, face track, footage cut, soundtrack mix, edit.js."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from . import sfx as sound_fx
from .beats import find_drop
from .shots import select, shot_id
from .util import clamp, log, probe, run
from .vision import FaceDetector, cover_map, frames, letterbox, subtitle_band

W, H = 1920, 1080
LEAD, TAIL = 0.4, 0.45
PATTERN = [2, 2, 1, 3, 2, 1, 1, 2, 3, 2, 1, 2, 2, 1, 3, 1, 1, 2, 2, 4]
BUILD_VERSION = 4  # bump when assemble output changes
SFX_LEVEL = 0.7
# speed over a shot (0..1 through it), as a multiple of real time: the slow part is the one you feel
RAMPS = {
    "in": lambda u: 2.6 - 2.1 * (u * u * (3 - 2 * u)),          # rush in, settle into slow motion
    "out": lambda u: 0.5 + 2.1 * (u * u * (3 - 2 * u)),         # hang, then snap away
    "hit": lambda u: 0.45 + 1.95 * np.abs(2 * u - 1) ** 1.5,    # fast, slow in the middle, fast
}
AUTO_RAMPS = ("hit", "out", None, "in", "hit", None, "out", "in")  # what velocity gives shots that did not choose


def build_key(quote: dict, plan: dict, music: Path, fps: int, montage_len: float, hero_len: float,
              analyses: list[dict], curation: dict, cut_rate: str = "normal", velocity: bool = True,
              sfx: float = SFX_LEVEL) -> str:
    """Everything the cut footage depends on; the same key means footage + edit.js are still good."""
    parts = [quote, plan, str(music), fps, montage_len, hero_len, BUILD_VERSION,
             [(an["source"], an["crop"], len(an["shots"])) for an in analyses], curation]
    if cut_rate != "normal":  # left out by default so edits made before this option keep their key
        parts.append(cut_rate)
    if not velocity:
        parts.append("no-velocity")
    if abs(sfx - SFX_LEVEL) > 1e-6:
        parts.append(f"sfx={sfx:.2f}")
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def montage_slots(beats: list[float], drop: int, montage_len: float, hero_len: float,
                  cut_rate: str = "normal"):
    """Cut points on real beat times. Returns (durations, beat_offsets, hero_duration).

    cut_rate "slow" holds each shot twice as long, "fast" cuts twice as often."""
    rel = [b - beats[drop] for b in beats[drop:]]
    period = float(np.median(np.diff(rel[:32]))) if len(rel) > 2 else 0.5
    unit = 1
    while period * unit < 0.36:
        unit *= 2
    while period * unit > 0.95 and unit > 1:
        unit //= 2
    if period * unit > 0.95:  # very slow song: split beats in half
        rel = sorted(set(rel + [(a + b) / 2 for a, b in zip(rel, rel[1:])]))
    if cut_rate == "slow":
        unit *= 2
    elif cut_rate == "fast":
        if unit > 1:
            unit //= 2
        else:
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


# HDR footage looks grey and flat unless its brightness curve is mapped down to ordinary video
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv")


def _vf(crop, fps: int, hdr: bool = False) -> str:
    parts = ["crop=%d:%d:%d:%d" % tuple(crop)] if crop else []
    if hdr:
        parts.append(TONEMAP)
    parts += [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}:(iw-{W})/2:0",  # tall sources: keep the top (faces), drop the bottom (watermarks)
              f"fps={fps}", "setsar=1", "format=yuv420p"]
    return ",".join(parts)


def cut(src: Path, start: float, nframes: int, crop, fps: int, out: Path, speed: float = 1.0) -> None:
    vf = _vf(crop, fps, probe(src).get("hdr", False))
    if abs(speed - 1.0) > 0.01:  # retime before the fps filter so it fills or drops frames
        vf = f"setpts=PTS/{speed:.3f}," + vf
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", src, "-frames:v", nframes,
         "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
         "-r", fps, "-g", fps, out])


def cut_ramped(src: Path, start: float, times: np.ndarray, crop, fps: int, out: Path) -> None:
    """Cut a shot whose speed changes: `times` is where in the source each output frame looks.

    ffmpeg does the retime: the source's timestamps are bent along the ramp (a piecewise-linear
    warp through 16 knots), then neighbouring frames are blended up to the output rate, so slow parts are
    smooth slow motion instead of repeated frames."""
    n = len(times)
    knots = sorted({int(round(k)) for k in np.linspace(0, n - 1, 17)})
    src_t = [float(times[k]) for k in knots]            # seconds into the source
    out_t = [(k + 0.5) / fps for k in knots]            # seconds into the shot
    expr = f"{out_t[-1]:.5f}+(T-{src_t[-1]:.5f})"       # past the last knot: carry on at real speed
    for i in range(len(knots) - 2, -1, -1):
        slope = (out_t[i + 1] - out_t[i]) / max(src_t[i + 1] - src_t[i], 1e-4)
        expr = f"if(lt(T,{src_t[i + 1]:.5f}),{out_t[i]:.5f}+(T-{src_t[i]:.5f})*{slope:.5f},{expr})"
    parts = ["crop=%d:%d:%d:%d" % tuple(crop)] if crop else []
    if probe(src).get("hdr"):
        parts.append(TONEMAP)
    parts += [f"setpts='({expr})/TB'", f"framerate=fps={fps}:interp_start=0:interp_end=255:scene=100",
              f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}:(iw-{W})/2:0",
              "setsar=1", "format=yuv420p"]
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-t", f"{float(times[-1]) + 0.5:.3f}", "-i", src,
         "-frames:v", n, "-vf", ",".join(parts), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
         "-r", fps, "-g", fps, out])
    got = probe(out)
    if round(got["duration"] * fps) < n:                # a short source: hold the last frame to keep the cut on the beat
        fixed = out.with_name(out.stem + "-pad.mp4")
        run(["ffmpeg", "-v", "error", "-y", "-i", out, "-vf", f"tpad=stop_mode=clone:stop_duration=2", "-frames:v", n,
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-r", fps, "-g", fps, fixed])
        fixed.replace(out)


def _retime(opts: dict, auto: str | None, usable: float, slot_len: float, n: int) -> tuple[np.ndarray | None, float]:
    """Where each of a shot's n frames looks in its source, and how much source that uses.

    None means plain real-time footage, which the ordinary cut handles."""
    kind = opts.get("ramp") or auto
    speed = float(opts.get("speed") or 1.0)
    if kind in (None, "none") or kind not in RAMPS or slot_len < 0.45:
        if speed >= 0.99:
            return None, slot_len * _speed(opts, usable, slot_len)
        v = np.full(n, speed)       # constant slow motion still gets blended frames
    else:
        v = RAMPS[kind]((np.arange(n) + 0.5) / n) * speed
    need = slot_len * float(v.mean())
    if need > usable > 0:           # not enough source: keep the shape, ease the speeds
        v *= usable / need
    times = (np.cumsum(v) - v / 2) / n * slot_len
    return times, slot_len * float(v.mean())


def mix(speech: Path, speech_start: float, speech_len: float, music: Path, music_start: float,
        total: float, out: Path, effects: Path | None = None, effects_level: float = 0.0) -> None:
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
    )
    inputs = ["-i", speech, "-i", music]
    if effects and effects_level > 0:
        inputs += ["-i", effects]
        graph += (f"[2:a]aresample=48000,volume={effects_level:.3f}[fx];"
                  f"[sp][mu][fx]amix=inputs=3:normalize=0:duration=longest,")
    else:
        graph += "[sp][mu]amix=inputs=2:normalize=0:duration=longest,"
    graph += f"alimiter=limit=0.93,atrim=duration={total:.3f}[a]"
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph,
         "-map", "[a]", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", out])


def timeline(quote: dict, pool: list[dict], music_beats: dict, fps: int, montage_len: float,
             hero_len: float, cut_rate: str = "normal", cuts: list | None = None) -> dict:
    """The frame-exact layout of the edit: speech, beat-cut slots, hero. No media is touched,
    so the editor can project a timeline from saved choices without cutting footage."""
    s_start = max(0.0, quote["start"] - LEAD)
    speech_f = round((quote["end"] + TAIL - s_start) * fps)
    M = speech_f / fps
    drop = find_drop(music_beats, montage_len + hero_len + 1)
    durations, beat_marks, hero_len = montage_slots(music_beats["beats"], drop, montage_len, hero_len,
                                                    cut_rate)
    if cuts:  # shot lengths set by hand in the editor (trims, splits, deletes) replace the beat grid
        durations = [max(0.1, float(c)) for c in cuts]
    slots, hero = select(pool, durations, hero_len)
    bounds = [speech_f]
    acc = M
    for d in durations:
        acc += d
        bounds.append(round(acc * fps))
    hero_f = round(hero_len * fps)
    return {"speech_start": s_start, "speech_f": speech_f, "M": M, "durations": durations,
            "beat_marks": beat_marks, "hero_len": hero_len, "slots": slots, "hero": hero, "bounds": bounds,
            "hero_f": hero_f, "total_f": bounds[-1] + hero_f,
            "music_start": music_beats["beats"][drop] - M}


def build(job: Path, *, plan: dict, quote: dict, speech: Path, analyses: list[dict], pool: list[dict],
          music: Path, music_beats: dict, fps: int, montage_len: float, hero_len: float,
          cut_rate: str = "normal", velocity: bool = True, sfx: float = SFX_LEVEL,
          cuts: list | None = None) -> dict:
    work = job / "work"
    render = job / "render"
    (work / "segments").mkdir(parents=True, exist_ok=True)
    render.mkdir(parents=True, exist_ok=True)
    fd = FaceDetector()

    sinfo = probe(speech)
    s_crop = letterbox(speech, sinfo["duration"])
    subs = subtitle_band(speech, sinfo["duration"])
    if subs:  # burned-in subtitles: keep only the picture above them
        cw, ch, cx, cy = s_crop or (sinfo["width"], sinfo["height"], 0, 0)
        keep = int(sinfo["height"] * subs) - cy
        if ch * 0.6 < keep < ch:
            s_crop = (cw, keep - keep % 2, cx, cy)
            log(f"speech: cropping burned-in subtitles off the bottom {100 * (1 - subs):.0f}%")
    s_size = (s_crop[0], s_crop[1]) if s_crop else (sinfo["width"], sinfo["height"])
    tl = timeline(quote, pool, music_beats, fps, montage_len, hero_len, cut_rate, cuts)
    s_start, speech_f, M = tl["speech_start"], tl["speech_f"], tl["M"]
    durations, beat_marks, hero_len = tl["durations"], tl["beat_marks"], tl["hero_len"]
    slots, hero, bounds, hero_f, total_f = tl["slots"], tl["hero"], tl["bounds"], tl["hero_f"], tl["total_f"]
    if not slots or any(s is None for s in slots):
        raise RuntimeError("not enough usable montage shots; add more sources with --montage")
    music_start = tl["music_start"]

    segs = [(speech, s_start, speech_f, s_crop, 1.0, None)]
    shots = []
    for i, (slot, a, b) in enumerate(zip(slots, bounds, bounds[1:])):
        n = b - a
        src = analyses[slot["src"]]
        opts = slot.get("opts") or {}
        usable = slot["end"] - slot["start"]
        times, used = _retime(opts, AUTO_RAMPS[i % len(AUTO_RAMPS)] if velocity else None, usable, n / fps, n)
        start = slot["start"] + max(0.0, usable - used) * float(opts.get("slip", 0.3))
        segs.append((Path(src["source"]), start, n, src["crop"], _speed(opts, usable, n / fps), times))
        shots.append({"index": i, "start": a, "end": b, "face": _face_px(slot, src),
                      "id": shot_id(analyses, slot), **_film_opts(opts)})
    hero_shot = hero or slots[-1]
    hsrc = analyses[hero_shot["src"]]
    hopts = hero_shot.get("opts") or {}
    husable = hero_shot["end"] - hero_shot["start"]
    htimes, hused = _retime(hopts, "in" if velocity else None, husable, hero_len, hero_f)
    hstart = hero_shot["start"] + max(0.0, husable - hused) * float(hopts.get("slip", 0.25))
    segs.append((Path(hsrc["source"]), hstart, hero_f, hsrc["crop"], _speed(hopts, husable, hero_len), htimes))
    shots.append({"index": len(shots), "start": bounds[-1], "end": total_f, "face": _face_px(hero_shot, hsrc),
                  "hero": True, "id": shot_id(analyses, hero_shot), **_film_opts(hopts)})

    log(f"timeline: speech {M:.2f}s + {len(durations)} beat cuts + hero {hero_len:.1f}s "
        f"= {total_f / fps:.2f}s @ {fps}fps")
    listing = []
    ramped = 0
    for k, (src, start, n, crop, speed, times) in enumerate(segs):
        seg = work / "segments" / f"seg-{k:03d}.mp4"
        if times is None:
            cut(src, start, n, crop, fps, seg, speed)
        else:
            cut_ramped(src, start, times, crop, fps, seg)
            ramped += 1
        listing.append(f"file '{seg.as_posix()}'\n")
    (work / "segments.txt").write_text("".join(listing), encoding="utf-8")
    footage = render / "footage.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", work / "segments.txt",
         "-c", "copy", footage])
    soundtrack = job / "work" / "soundtrack.m4a"
    total = total_f / fps
    if ramped:
        log(f"velocity: {ramped} shots retimed")
    effects = None
    if sfx > 0:
        effects = sound_fx.build(work / "sfx.wav", total, M, [b / fps for b in bounds[1:-1]], bounds[-1] / fps)
    mix(speech, s_start, M, music, music_start, total, soundtrack, effects, sfx)

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
        "title": plan["title"], "year": plan.get("year"), "palette": plan["palette"],
        "grade": plan.get("grade", "neutral"), "look": plan.get("look") or {},
        "captions": captions, "faces": faces, "shots": shots,
        "beats": [round(M + b, 3) for b in beat_marks],
    }
    (render / "edit.js").write_text("window.EDIT=" + json.dumps(edit, ensure_ascii=False, default=lambda o: o.item()) + ";\n",
                                    encoding="utf-8")
    got = probe(footage)
    log(f"footage: {got['duration']:.2f}s (expected {total:.2f}s)")
    return edit


def _speed(opts: dict, usable: float, slot_len: float) -> float:
    """A shot's playback speed, capped so a fast shot never runs past its own last frame."""
    speed = float(opts.get("speed") or 1.0)
    if speed > 1.0:
        speed = max(1.0, min(speed, usable / max(slot_len, 1e-3)))
    return speed


def _film_opts(opts: dict) -> dict:
    """The per-shot options the film layer reads: the camera move and the effects."""
    out = {}
    if opts.get("move"):
        out["move"] = opts["move"]
    if opts.get("fx"):
        out["fx"] = list(opts["fx"])
    for key in ("ease", "grade", "tf"):   # how the move settles, and the shot's own colour correction
        if opts.get(key):
            out[key] = opts[key]
    return out


def _face_px(shot: dict, src: dict):
    if not shot.get("face"):
        return None
    to_px = cover_map(src["size"][0], src["size"][1], W, H)
    x, y = to_px(shot["face"][0], shot["face"][1])
    return [round(clamp(x, 0, W), 1), round(clamp(y, 0, H), 1)]
