"""easyedit: type a movie name, get a captioned speech + beat-cut montage edit.

    python -m easyedit "The Wolf of Wall Street"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import assemble, beats, fetch, plan as planner, quote, render, shots, transcribe
from .util import JOBS, log, need, read_json, slugify, write_json
from .vision import FaceDetector

BUILD_VERSION = 2  # bump when assemble output changes


def parse(argv=None):
    p = argparse.ArgumentParser(prog="easyedit", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("movie", help="movie title, e.g. \"The Wolf of Wall Street\"")
    p.add_argument("--speech", help="speech scene: local file, URL, or custom search query")
    p.add_argument("--montage", action="append", help="montage source (file/URL/query); repeatable")
    p.add_argument("--music", help="song: local file, URL, or search query")
    p.add_argument("--llm", default="auto", choices=["auto", "claude", "codex", "none"])
    p.add_argument("--fps", type=int, default=60, choices=[24, 30, 60])
    p.add_argument("--montage-length", type=float, default=21.0, help="seconds of beat cuts")
    p.add_argument("--hero-length", type=float, default=3.4, help="seconds of the final mono shot")
    p.add_argument("--language", default=None, help="speech language code (default en)")
    p.add_argument("--cookies-from-browser", default=None, help="for age-restricted YouTube clips")
    p.add_argument("--draft", action="store_true", help="30fps draft quality render")
    p.add_argument("--no-render", action="store_true", help="stop after building footage + edit.js")
    p.add_argument("--preview", nargs=2, type=float, metavar=("START", "END"),
                   help="render only this time range (seconds) for a quick look")
    p.add_argument("--fresh", action="store_true", help="ignore cached plan/pick for this movie")
    p.add_argument("-o", "--output", help="output mp4 path")
    return p.parse_args(argv)


def main(argv=None) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    a = parse(argv)
    for b in ("ffmpeg", "ffprobe", "node"):
        need(b)
    if a.draft:
        a.fps = 30
    job = JOBS / slugify(a.movie)
    dl = job / "downloads"
    job.mkdir(parents=True, exist_ok=True)
    log(f"job: {job}")

    plan_file = job / "plan.json"
    if plan_file.exists() and not a.fresh:
        plan = read_json(plan_file)
    else:
        plan = planner.make_plan(a.movie, a.llm)
        write_json(plan_file, plan)

    # lock sources: YouTube search order drifts between runs, which would silently change the edit
    src_file = job / "sources.json"
    specs = {"speech": a.speech, "montage": a.montage, "music": a.music}
    cached = read_json(src_file) if src_file.exists() and not a.fresh else None
    if cached and cached.get("specs") == specs and all(
            Path(f).exists() for k in ("speech", "montage", "music") for f in cached[k]):
        speech_files, montage_files, music_files = ([Path(f) for f in cached[k]]
                                                    for k in ("speech", "montage", "music"))
        log("sources: reusing locked downloads")
    else:
        speech_files = fetch.resolve(a.speech, plan["speech_query"], dl / "speech", n=2, min_dur=20,
                                     max_dur=600, cookies=a.cookies_from_browser)
        montage_files = []
        for spec in (a.montage or plan["montage_queries"][:2]):
            try:
                montage_files += fetch.resolve(spec, spec, dl / "montage", n=1, min_dur=60, max_dur=1500,
                                               cookies=a.cookies_from_browser)
            except Exception as e:
                log(f"montage source skipped: {e}")
        if not montage_files:
            montage_files = speech_files
        music_files = fetch.resolve(a.music, a.music or plan["music_query"], dl / "music", n=1, min_dur=60,
                                    max_dur=900, audio_only=True, cookies=a.cookies_from_browser)
        write_json(src_file, {"specs": specs, "speech": [str(f) for f in speech_files],
                              "montage": [str(f) for f in montage_files],
                              "music": [str(f) for f in music_files]})

    pick_file = job / "quote.json"
    clips = [transcribe.transcribe(f, job / "work" / f"words-{f.stem}.json", a.language) for f in speech_files]
    if pick_file.exists() and not a.fresh and read_json(pick_file).get("files") == [str(f) for f in speech_files]:
        q = read_json(pick_file)
    else:
        q = quote.choose(clips, plan, a.llm)
        q["files"] = [str(f) for f in speech_files]
        write_json(pick_file, q)

    transcribe.release()
    fd = FaceDetector()
    analyses = [shots.analyze(f, job / "work" / f"shots-{f.stem}.json", fd) for f in montage_files]
    pool = shots.rank(analyses)
    log(f"shots: {len(pool)} candidates across {len(analyses)} source(s)")
    music_beats = beats.analyze(music_files[0], job / "work" / f"beats-{music_files[0].stem}.json")

    key = hashlib.sha256(json.dumps([q, plan, [str(f) for f in montage_files], str(music_files[0]), a.fps,
                                     a.montage_length, a.hero_length, BUILD_VERSION],
                                    sort_keys=True, default=str).encode()).hexdigest()
    key_file = job / "work" / "build.key"
    edit_js = job / "render" / "edit.js"
    if (key_file.exists() and key_file.read_text() == key and edit_js.exists()
            and (job / "render" / "footage.mp4").exists()):
        log("build: reusing footage + edit.js")
        edit = json.loads(edit_js.read_text(encoding="utf-8").removeprefix("window.EDIT=").rstrip().rstrip(";"))
    else:
        edit = assemble.build(job, plan=plan, quote=q, speech=speech_files[q["clip"]], analyses=analyses,
                              pool=pool, music=music_files[0], music_beats=music_beats, fps=a.fps,
                              montage_len=a.montage_length, hero_len=a.hero_length)
        key_file.write_text(key)
    if a.no_render:
        log(f"built {job / 'render'}; skipping render")
        return
    out = a.output or str(job / f"{slugify(a.movie)}{'-preview' if a.preview else ''}.mp4")
    render.render(job, edit, Path(out), quality="draft" if a.draft else "high",
                  only=tuple(a.preview) if a.preview else None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
