"""Editor backend: one job's saved choices, the timeline they produce, and the bits the page edits.

The editor never invents a second project format. It reads and writes the same files the
pipeline already uses (plan.json, quote.json, curate.json) plus settings.json for the
lengths and frame rate, then asks the pipeline to rebuild.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
from pathlib import Path

from .util import read_json, write_json

GRADES = ("neutral", "cinematic", "teal-orange", "warm", "cool", "vivid", "anime", "dream", "matte", "noir",
          "blood", "ice")
ROLES = ("positive", "negative", "gold", "cool")
DEFAULTS = {"fps": 60, "montage_length": 21.0, "hero_length": 3.4, "cut_rate": "normal", "depth": False,
            "velocity": True, "sfx": 0.7}
RAMPS = ("in", "out", "hit", "none")
CUT_RATES = ("slow", "normal", "fast")
MOVES = ("punch", "whip", "whip-down", "slide", "slide-left", "slide-right", "slide-up", "slide-down", "spin",
         "zoom-out", "bounce", "elastic", "cut")
EASES = ("smooth", "linear", "bounce", "spring", "step")   # how a camera move settles; expo when unset
FX = ("shake", "wiggle", "twitch", "push", "pull", "breathe", "lens", "impact", "strobe", "rgb", "glitch", "vhs", "glow",
      "leak", "sweep", "exposure", "dissolve", "cloud", "tiles", "waves", "warp", "edge", "mirror", "poster", "pixel",
      "mono", "invert",
      "outline", "aura", "echo", "isolate")   # the last four read the depth map
DEPTH_FX = ("outline", "aura", "echo", "isolate")
CAP_ANIMS = ("rise", "drop", "left", "right", "pop", "wiggle", "wave", "custom")
# the look film.js draws when a job says nothing; (default, lowest, highest)
LOOK = {"grain": (0.05, 0.0, 0.25), "vignette": (0.42, 0.0, 0.9), "bars": (0.0, 0.0, 2.76),
        "pulse": (1.0, 0.0, 3.0), "flash": (1.0, 0.0, 2.5), "shake": (0.0, 0.0, 2.0),
        "capSize": (64.0, 36.0, 120.0), "capY": (0.61, 0.12, 0.9), "capGlow": (1.0, 0.0, 3.0),
        "blur": (1.0, 0.0, 2.5), "bloom": (0.3, 0.0, 1.5), "lens": (0.25, 0.0, 2.0), "sharpen": (0.2, 0.0, 1.0),
        "dof": (0.0, 0.0, 1.5), "pop": (0.0, 0.0, 1.0), "parallax": (0.0, 0.0, 2.0), "capBehind": (0.0, 0.0, 1.0),
        "dust": (0.0, 0.0, 1.0), "flicker": (0.0, 0.0, 1.0), "barsDrop": (0.0, 0.0, 1.0),
        "capTrack": (2.0, 0.0, 40.0), "capDX": (0.0, -300.0, 300.0), "capDY": (40.0, -300.0, 300.0),
        "capFromScale": (1.0, 0.2, 3.0), "capRot": (0.0, -90.0, 90.0)}
CAP_FONTS = ("caption", "display")
TF_LIMITS = {"x": (-1920, 1920), "y": (-1080, 1080), "s": (10, 400), "r": (-180, 180), "o": (0, 100)}
# After Effects-style text animations; they drive the signature text styles
CAP_MOTIONS = ("ease-up", "ease-down", "blur-up", "blur-down", "pop-up", "pop-down", "bounce-up", "bounce-down",
               "wobble-up", "wobble-down", "slide-up", "slide-down", "random-up", "random-down", "snappy-up",
               "snappy-down", "reveal-right", "reveal-left", "merge", "blink",
               "left-slide", "right-slide", "top-slide", "bottom-slide")
# effects layered on the signature text styles; any number at once
CAP_FX = ("sweep", "outline", "shadow", "turb", "waves", "wiggle", "cloud", "cells", "flux", "arc")
CAP_STYLES = ("cinema", "lyric", "kinetic", "duo", "gold", "chrome", "wide", "ghost", "whisper", "hype", "neonscript",
              "clean", "serif", "mixed", "aesthetic", "trailer", "script", "marker", "subtitle", "typewriter",
              "outline", "neon", "boxed", "karaoke", "impact", "glitch", "vhs")
NOT_READY = "Make this edit once before opening it in the editor."
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_THUMBS = threading.BoundedSemaphore(4)  # ffmpeg stills at once

SYSTEM = "You are a film editor adjusting a fan edit. Reply with JSON only."
PROMPT = """Film: {title}. The editor asks: {ask}

Current look: grade "{grade}" (choices: {grades}), caption colours {palette}.
Montage is {montage:.1f}s of beat cuts, final shot is {hero:.1f}s.

Captions, one line per row, every word numbered:
{captions}

Change only what the request needs. Return JSON with any of these keys:
{{"title": "text on the final card", "grade": "<one of the choices>",
  "palette": {{"positive": "#rrggbb", "negative": "#rrggbb", "gold": "#rrggbb", "cool": "#rrggbb"}},
  "emphasis": [[<word number>, "positive" | "negative" | "gold" | "cool" | null], ...],
  "montage_length": <seconds, 8-40>, "hero_length": <seconds, 2-8>,
  "note": "one short sentence on what you changed"}}"""


def built_edit(job: Path) -> dict | None:
    """The edit.js of the last build, or None when the job was never cut."""
    import json
    edit_js = job / "render" / "edit.js"
    if not edit_js.exists():
        return None
    try:
        return json.loads(edit_js.read_text(encoding="utf-8").removeprefix("window.EDIT=").rstrip().rstrip(";"))
    except ValueError:
        return None


def settings(job: Path) -> dict:
    s = dict(DEFAULTS)
    built = built_edit(job)
    if built and built.get("shots"):  # a job made from the command line: read back what it used
        hero = built["shots"][-1]
        s["fps"] = int(built["fps"])
        s["hero_length"] = round((hero["end"] - hero["start"]) / built["fps"], 2)
    saved = job / "settings.json"
    if saved.exists():
        s.update(_clean_settings(read_json(saved)))
    return s


def _clean_settings(raw: dict) -> dict:
    out = {}
    try:
        if int(raw.get("fps") or 0) in (24, 30, 60):
            out["fps"] = int(raw["fps"])
        if raw.get("montage_length") is not None:
            out["montage_length"] = float(min(40.0, max(8.0, float(raw["montage_length"]))))
        if raw.get("hero_length") is not None:
            out["hero_length"] = float(min(8.0, max(2.0, float(raw["hero_length"]))))
    except (TypeError, ValueError):
        pass
    if raw.get("cut_rate") in CUT_RATES:
        out["cut_rate"] = raw["cut_rate"]
    for flag in ("depth", "velocity"):
        if isinstance(raw.get(flag), bool):
            out[flag] = raw[flag]
    try:
        if raw.get("sfx") is not None:
            out["sfx"] = round(min(1.5, max(0.0, float(raw["sfx"]))), 2)
    except (TypeError, ValueError):
        pass
    return out


def look_of(plan: dict) -> dict:
    """The full look, every value present, for the page to show."""
    saved = plan.get("look") or {}
    out = {k: saved.get(k, d) for k, (d, _, _) in LOOK.items()}
    out["capFont"] = saved.get("capFont", "caption")
    out["capAnim"] = saved.get("capAnim", "rise")
    out["capStyle"] = saved.get("capStyle", "clean")
    out["capMotion"] = saved.get("capMotion", "")
    out["capFx"] = [f for f in CAP_FX if f in (saved.get("capFx") or [])]
    return out


def _clean_look(raw: dict) -> dict:
    """Only what differs from the default look, so an untouched edit keeps its build key."""
    out = {}
    for k, (default, lo, hi) in LOOK.items():
        try:
            v = round(min(hi, max(lo, float(raw.get(k, default)))), 3)
        except (TypeError, ValueError):
            continue
        if abs(v - default) > 1e-6:
            out[k] = v
    if raw.get("capFont") in CAP_FONTS and raw["capFont"] != "caption":
        out["capFont"] = raw["capFont"]
    if raw.get("capAnim") in CAP_ANIMS and raw["capAnim"] != "rise":
        out["capAnim"] = raw["capAnim"]
    if raw.get("capStyle") in CAP_STYLES and raw["capStyle"] != "clean":
        out["capStyle"] = raw["capStyle"]
    if raw.get("capMotion") in CAP_MOTIONS:
        out["capMotion"] = raw["capMotion"]
    fx = [f for f in CAP_FX if f in (raw.get("capFx") or [])]
    if fx:
        out["capFx"] = fx
    return out


def _clean_shot_opts(raw, known: set) -> dict:
    """Per-shot tools: camera move, speed, which part of the shot, effects. Defaults are left out."""
    out = {}
    for sid, o in (raw or {}).items():
        if sid not in known or not isinstance(o, dict):
            continue
        keep = {}
        if o.get("move") in MOVES:
            keep["move"] = o["move"]
        if o.get("ramp") in RAMPS:
            keep["ramp"] = o["ramp"]
        if o.get("ease") in EASES:
            keep["ease"] = o["ease"]
        if o.get("grade") in GRADES:
            keep["grade"] = o["grade"]
        if isinstance(o.get("tf"), dict):  # the layer's Transform; keys ending in 1 are the end values
            tf = {}
            for k, (lo, hi) in TF_LIMITS.items():
                for key in (k, k + "1"):
                    try:
                        if o["tf"].get(key) is not None:
                            tf[key] = round(min(hi, max(lo, float(o["tf"][key]))), 2)
                    except (TypeError, ValueError):
                        pass
            if tf:
                keep["tf"] = tf
        fx = [f for f in FX if f in (o.get("fx") or [])]
        if fx:
            keep["fx"] = fx
        try:
            speed = round(min(4.0, max(0.25, float(o.get("speed") or 1.0))), 2)
            if abs(speed - 1.0) > 0.01:
                keep["speed"] = speed
            if o.get("slip") is not None:
                keep["slip"] = round(min(1.0, max(0.0, float(o["slip"]))), 2)
        except (TypeError, ValueError):
            pass
        if keep:
            out[sid] = keep
    return out


def _inputs(job: Path) -> dict:
    need = [job / "plan.json", job / "sources.json", job / "quote.json"]
    if not all(p.exists() for p in need):
        raise ValueError(NOT_READY)
    src = read_json(job / "sources.json")
    music = Path(src["music"][0])
    caches = [job / "work" / f"shots-{Path(f).stem}.json" for f in src["montage"]]
    beats = job / "work" / f"beats-{music.stem}.json"
    if not beats.exists() or not all(c.exists() for c in caches):
        raise ValueError(NOT_READY)
    curate = job / "curate.json"
    return {"plan": read_json(need[0]), "sources": src, "quote": read_json(need[2]),
            "analyses": [read_json(c) for c in caches], "music": music, "beats": read_json(beats),
            "curation": read_json(curate) if curate.exists() else {}}


def _project(inp: dict, s: dict) -> dict:
    """The timeline the saved choices produce, without cutting any footage."""
    from . import assemble, shots
    pool = shots.rank(inp["analyses"])
    if inp["curation"]:
        pool = shots.curate(pool, inp["analyses"], inp["curation"])
    fps = s["fps"]
    try:
        tl = assemble.timeline(inp["quote"], pool, inp["beats"], fps, s["montage_length"], s["hero_length"],
                               s["cut_rate"], inp["curation"].get("cuts"))
    except (IndexError, ValueError) as e:
        return {"error": f"the song is too short for this montage ({e})"}
    slots, bounds = tl["slots"], tl["bounds"]
    if not slots or any(x is None for x in slots):
        return {"error": "Not enough usable shots. Bring some removed shots back or shorten the montage."}
    cuts = [{"id": shots.shot_id(inp["analyses"], x), "start": a, "end": b}
            for x, a, b in zip(slots, bounds, bounds[1:])]
    hero = tl["hero"] or slots[-1]
    cuts.append({"id": shots.shot_id(inp["analyses"], hero), "start": bounds[-1], "end": tl["total_f"],
                 "hero": True})
    return {"fps": fps, "frames": tl["total_f"], "speechFrames": tl["speech_f"],
            "speechStart": round(tl["speech_start"], 3), "musicStart": round(tl["music_start"], 3),
            "beats": [round(tl["M"] + b, 3) for b in tl["beat_marks"]], "shots": cuts}


def state(job: Path) -> dict:
    """Everything the editor page needs to draw itself."""
    from . import assemble, shots
    inp = _inputs(job)
    s = settings(job)
    plan, q, src = inp["plan"], inp["quote"], inp["sources"]
    pool = shots.rank(inp["analyses"])
    cur = inp["curation"]
    pins, drop = list(cur.get("pin", [])), set(cur.get("exclude", []))
    bin_ = []
    for rank, x in enumerate(pool):
        sid = shots.shot_id(inp["analyses"], x)
        bin_.append({"id": sid, "src": x["src"], "start": x["start"], "end": x["end"],
                     "score": round(x["score"], 2), "rank": rank, "face": bool(x["face"]),
                     "state": "exclude" if sid in drop else "pin" if sid in pins else None})
    bin_.sort(key=lambda b: (b["src"], b["start"]))
    clips = []
    for f in src["speech"]:
        words = job / "work" / f"words-{Path(f).stem}.json"
        clips.append({"name": Path(f).name,
                      "words": [{"text": w["text"], "start": w["start"], "end": w["end"]}
                                for w in read_json(words)] if words.exists() else []})
    # a draft export cuts the same edit at 30 fps; that footage still matches these choices
    keys = {assemble.build_key(q, plan, inp["music"], fps, s["montage_length"], s["hero_length"],
                               inp["analyses"], cur, s["cut_rate"], s["velocity"], s["sfx"])
            for fps in (s["fps"], 30)}
    key_file, footage = job / "work" / "build.key", job / "render" / "footage.mp4"
    has_build = footage.exists() and (job / "render" / "edit.js").exists()
    from . import depth as depth_maps
    has_depth = depth_maps.fresh(job)
    out = [p for p in job.glob("*.mp4") if "preview" not in p.name]
    video = max(out, key=lambda p: p.stat().st_mtime) if out else None
    return {
        "slug": job.name,
        "plan": {"title": plan.get("title", job.name), "palette": plan.get("palette", {}),
                 "grade": plan.get("grade", "neutral"), "look": look_of(plan)},
        "grades": list(GRADES), "roles": list(ROLES), "moves": list(MOVES), "fx": list(FX), "depthFx": list(DEPTH_FX), "ramps": list(RAMPS), "eases": list(EASES),
        "anims": list(CAP_ANIMS), "capStyles": list(CAP_STYLES), "capMotions": list(CAP_MOTIONS), "capFx": list(CAP_FX), "proStyles": list(CAP_STYLES[:11]), "settings": s,
        "quote": {"clip": q.get("clip", 0), "start": q["start"], "end": q["end"], "lines": q["lines"]},
        "clips": clips, "shots": bin_,
        "sources": {k: [Path(f).name for f in src[k]] for k in ("speech", "montage", "music")},
        "curation": {"pin": pins, "exclude": list(cur.get("exclude", [])), "hero": cur.get("hero"),
                     "shots": cur.get("shots") or {}, "cuts": list(cur.get("cuts") or [])},
        "timeline": _project(inp, s),
        "music": {"bpm": inp["beats"].get("bpm")},
        "built": {"exists": has_build,
                  "missing": not all(Path(f).exists() for k in ("speech", "montage", "music") for f in src[k]),
                  "fresh": has_build and key_file.exists() and key_file.read_text() in keys
                           and has_depth == s["depth"],
                  "depth": has_depth,
                  "version": int(footage.stat().st_mtime) if has_build else 0,
                  "video": f"/media/{job.name}/{video.name}" if video else None},
    }


def _clean_lines(raw) -> list[list[dict]]:
    lines = []
    for line in raw or []:
        words = []
        for w in line or []:
            text = str(w.get("text", "")).strip()[:40]
            if not text:
                continue
            start, end = float(w["start"]), float(w["end"])
            words.append({"text": text, "start": start, "end": max(start, end),
                          "role": w.get("role") if w.get("role") in ROLES else None})
        if words:
            lines.append(words)
    if not lines:
        raise ValueError("the captions need at least one word")
    return lines


def save(job: Path, body: dict) -> dict:
    """Write the editor's choices back to the job's own files."""
    inp = _inputs(job)
    if isinstance(body.get("plan"), dict):
        plan, p = inp["plan"], body["plan"]
        title = str(p.get("title") or "").strip()[:80]
        if title:
            plan["title"] = title
        if p.get("grade") in GRADES:
            plan["grade"] = p["grade"]
        colours = {r: c for r, c in (p.get("palette") or {}).items() if r in ROLES and _HEX.match(str(c))}
        if colours:
            plan["palette"] = {**plan.get("palette", {}), **colours}
        if isinstance(p.get("look"), dict):
            look = _clean_look(p["look"])
            if look:
                plan["look"] = look
            else:
                plan.pop("look", None)
        write_json(job / "plan.json", plan)
    if isinstance(body.get("quote"), dict):
        q = read_json(job / "quote.json")  # keeps "files": the pipeline drops a quote without it
        lines = _clean_lines(body["quote"].get("lines"))
        clip = body["quote"].get("clip", q.get("clip", 0))
        if not isinstance(clip, int) or not 0 <= clip < len(inp["sources"]["speech"]):
            raise ValueError("unknown speech clip")
        q.update(clip=clip, lines=lines, start=lines[0][0]["start"], end=lines[-1][-1]["end"])
        write_json(job / "quote.json", q)
    if isinstance(body.get("curation"), dict):
        c = body["curation"]
        known = {b["id"] for b in state(job)["shots"]}
        cur = {}
        drop = [i for i in dict.fromkeys(c.get("exclude") or []) if i in known]
        pin = [i for i in dict.fromkeys(c.get("pin") or []) if i in known and i not in drop]
        if pin:
            cur["pin"] = pin
        if drop:
            cur["exclude"] = drop
        if c.get("hero") in known:
            cur["hero"] = c["hero"]
        opts = _clean_shot_opts(c.get("shots"), known)
        if opts:
            cur["shots"] = opts
        try:  # shot lengths set by hand, in seconds
            cuts = [round(min(30.0, max(0.1, float(x))), 3) for x in (c.get("cuts") or [])][:300]
        except (TypeError, ValueError):
            cuts = []
        if cuts:
            cur["cuts"] = cuts
        if cur:
            write_json(job / "curate.json", cur)
        else:
            (job / "curate.json").unlink(missing_ok=True)
    if isinstance(body.get("settings"), dict):
        write_json(job / "settings.json", {**settings(job), **_clean_settings(body["settings"])})
    return state(job)


def requote(job: Path, clip: int, first: int, last: int, keep: dict) -> dict:
    """Caption lines for a new word range of a speech clip. Emphasis already chosen is kept."""
    from . import quote
    src = read_json(job / "sources.json")
    if not 0 <= clip < len(src["speech"]):
        raise ValueError("unknown speech clip")
    words = read_json(job / "work" / f"words-{Path(src['speech'][clip]).stem}.json")
    first, last = max(0, min(first, last)), min(len(words) - 1, max(first, last))
    span = words[last]["end"] - words[first]["start"]
    if span < 2 or span > 45:
        raise ValueError("pick between 2 and 45 seconds of speech")
    old = {round(w["start"], 2): w.get("role") for line in keep.get("lines", []) for w in line} \
        if keep.get("clip") == clip else {}
    caps = [{"text": quote._clean(w["text"]), "start": w["start"], "end": w["end"],
             "role": old.get(round(w["start"], 2))} for w in words[first:last + 1]]
    caps = [w for w in caps if w["text"]]
    if not caps:
        raise ValueError("no words in that range")
    return {"clip": clip, "start": caps[0]["start"], "end": caps[-1]["end"],
            "lines": [[caps[i] for i in g] for g in quote.lines(caps)]}


def run_argv(job: Path, mode: str) -> list[str]:
    """The pipeline command that rebuilds this job from its saved choices."""
    s, src = settings(job), read_json(job / "sources.json")
    # pin the settings before the run: a draft cuts at 30 fps, and that must not become the edit's frame rate
    if not (job / "settings.json").exists():
        write_json(job / "settings.json", s)
    argv = [sys.executable, "-u", "-m", "easyedit", job.name, "--fps", str(s["fps"]),
            "--montage-length", str(s["montage_length"]), "--hero-length", str(s["hero_length"]),
            "--cut-rate", s["cut_rate"], "--sfx", str(s["sfx"])]
    if not s["velocity"]:
        argv.append("--no-velocity")
    specs = src.get("specs") or {}  # the same source flags, or the locked downloads are thrown away
    for flag in ("speech", "music"):
        if specs.get(flag):
            argv += [f"--{flag}", specs[flag]]
    for m in specs.get("montage") or []:
        argv += ["--montage", m]
    if s["depth"]:
        argv.append("--depth")
    if mode == "refetch":  # look for sharper footage, then cut from it
        argv = [a for a in argv if a not in ("--speech", "--music", "--montage") and a not in
                [specs.get("speech"), specs.get("music"), *(specs.get("montage") or [])]]
        argv += ["--refetch", "--no-render"]
    elif mode == "build":
        argv.append("--no-render")
    elif mode == "draft":
        argv.append("--draft")
    elif mode != "full":
        raise ValueError(f"unknown mode: {mode}")
    return argv


def thumb(job: Path, kind: str, index: int, t: float) -> Path | None:
    """A small still from a source file, cached. Montage stills get the same letterbox crop as the edit."""
    src = read_json(job / "sources.json")
    if kind not in ("speech", "montage") or not 0 <= index < len(src[kind]):
        return None
    media = Path(src[kind][index])
    # named by the file, not its place in the list: clips are added and removed, and places shift
    name = hashlib.md5(media.name.encode()).hexdigest()[:10]
    out = job / "work" / "thumbs" / f"{kind}-{name}-{int(max(0.0, t) * 100)}.jpg"
    if out.exists():
        return out
    vf = "scale=320:-2"
    cache = job / "work" / f"shots-{media.stem}.json"
    small = media.parent / "proxy" / f"{media.stem}.mp4"   # stills come from the small copy of a 4K source
    if kind == "montage" and cache.exists():
        crop = read_json(cache).get("crop")
        if crop and small.exists():   # the crop is in the big file's pixels: say it as fractions
            from .util import probe
            info = probe(media)
            w, h = info["width"], info["height"]
            vf = f"crop=iw*{crop[0]}/{w}:ih*{crop[1]}/{h}:iw*{crop[2]}/{w}:ih*{crop[3]}/{h}," + vf
        elif crop:
            vf = "crop=%d:%d:%d:%d," % tuple(crop) + vf
    if small.exists():
        media = small
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _THUMBS:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(media),
                            "-frames:v", "1", "-vf", vf, "-q:v", "5", str(out)], check=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    return out if out.exists() else None


def source(job: Path, kind: str, index: int) -> Path | None:
    src = read_json(job / "sources.json")
    if kind not in ("speech", "montage", "music") or not 0 <= index < len(src[kind]):
        return None
    media = Path(src[kind][index])
    small = media.parent / "proxy" / f"{media.stem}.mp4"   # the source monitor plays the small copy of a 4K source
    if small.is_file():
        return small
    return media if media.is_file() else None


def wave(job: Path) -> dict:
    """Loudness peaks of the mixed soundtrack, 100 per second, for the audio track."""
    import numpy as np
    sound = job / "work" / "soundtrack.m4a"
    if not sound.exists():
        return {"rate": 100, "peaks": []}
    cache = job / "work" / "wave.json"
    stamp = int(sound.stat().st_mtime)
    if cache.exists():
        cached = read_json(cache)
        if cached.get("stamp") == stamp:
            return cached
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(sound), "-ac", "1", "-ar", "4000",
                          "-f", "s16le", "-"], capture_output=True, check=True, timeout=120).stdout
    y = np.abs(np.frombuffer(raw, np.int16).astype(np.int32))
    y = y[: len(y) // 40 * 40].reshape(-1, 40).max(axis=1)
    out = {"stamp": stamp, "rate": 100, "peaks": [int(v) for v in np.minimum(100, y * 100 // 32768)]}
    write_json(cache, out)
    return out


def ask(job: Path, request: str) -> dict:
    """Turn a plain-language request into changes the page applies as one undoable step."""
    from .llm import ask_json
    request = request.strip()[:600]
    if not request:
        raise ValueError("say what to change")
    st = state(job)
    rows, n = [], 0
    for line in st["quote"]["lines"]:
        row = []
        for w in line:
            row.append(f"[{n}]{w['text']}" + (f"({w['role']})" if w.get("role") else ""))
            n += 1
        rows.append(" ".join(row))
    reply = ask_json(PROMPT.format(title=st["plan"]["title"], ask=request, grade=st["plan"]["grade"],
                                   grades=", ".join(GRADES), palette=st["plan"]["palette"],
                                   montage=st["settings"]["montage_length"], hero=st["settings"]["hero_length"],
                                   captions="\n".join(rows)), SYSTEM)
    if not isinstance(reply, dict):
        raise ValueError("No AI account answered. Connect Claude or Codex on the main page, then try again.")
    patch = {"note": str(reply.get("note") or "")[:200]}
    if isinstance(reply.get("title"), str) and reply["title"].strip():
        patch["title"] = reply["title"].strip()[:80]
    if reply.get("grade") in GRADES:
        patch["grade"] = reply["grade"]
    if isinstance(reply.get("palette"), dict):
        colours = {r: c for r, c in reply["palette"].items() if r in ROLES and _HEX.match(str(c))}
        if colours:
            patch["palette"] = colours
    marks = []
    for item in reply.get("emphasis") or []:
        try:
            i, role = int(item[0]), item[1]
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= i < n and (role is None or role in ROLES):
            marks.append([i, role])
    if marks:
        patch["emphasis"] = marks
    patch.update(_clean_settings({k: reply.get(k) for k in ("montage_length", "hero_length")}))
    return patch
