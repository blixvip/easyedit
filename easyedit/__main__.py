"""easyedit: type a movie name, get a captioned speech + beat-cut montage edit.

    python -m easyedit "The Wolf of Wall Street"

Or cut your own clips the way a creator you measured cuts (python -m easyedit.style):

    python -m easyedit "my reel" --footage C:/clips --music C:/song.mp3 --style nio_trade --aspect 9:16
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import anime, assemble, beats, clips, fetch, plan as planner, quote, render, sequence, shots, transcribe, vision
from .util import JOBS, die, log, need, probe, read_json, slugify, write_json
from .vision import FaceDetector

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts"}


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
    p.add_argument("--cut-rate", default="normal", choices=["slow", "normal", "fast"],
                   help="how often the montage cuts: slow holds shots twice as long, fast cuts twice as often")
    p.add_argument("--no-velocity", action="store_true",
                   help="keep every montage shot at real speed (no speed ramps)")
    p.add_argument("--sfx", type=float, default=assemble.SFX_LEVEL,
                   help="level of the built-in whooshes, riser and impacts, 0 to turn them off")
    p.add_argument("--depth", action="store_true",
                   help="read a depth map of the footage for background blur, subject pop, parallax and "
                        "captions behind the subject (adds a few minutes)")
    p.add_argument("--anime", action=argparse.BooleanOptionalAction, default=None,
                   help="treat the title as an anime (twixtor and scenepack footage, dub line, anime grade); "
                        "by default easyedit looks the title up and decides")
    p.add_argument("--language", default=None, help="speech language code (default en)")
    p.add_argument("--cookies-from-browser", default=None, help="for age-restricted YouTube clips")
    p.add_argument("--draft", action="store_true", help="30fps draft quality render")
    p.add_argument("--no-render", action="store_true", help="stop after building footage + edit.js")
    p.add_argument("--preview", nargs=2, type=float, metavar=("START", "END"),
                   help="render only this time range (seconds) for a quick look")
    p.add_argument("--fresh", action="store_true", help="ignore cached plan/pick for this movie")
    p.add_argument("--refetch", action="store_true",
                   help="look for better footage again and re-cut from it (keeps the plan and the song)")
    p.add_argument("-o", "--output", help="output mp4 path")
    own = p.add_argument_group("your own footage", "cut local clips instead of searching for a film's")
    own.add_argument("--footage", nargs="+", metavar="FILE|FOLDER|URL|yt:QUERY",
                     help="your clips, or URLs / search queries the tool downloads (yt:\"napoleon trailer 4k\"); "
                          "with --music they are cut on the beat (and under --speech or any narration found)")
    own.add_argument("--captions", default="auto", choices=["auto", "whole", "pick", "none"],
                     help="whole = caption every spoken word, pick = one passage like a film quote, "
                          "none = no captions; auto = whole when speech is found")
    own.add_argument("--style", help="a measured creator profile, e.g. nio_trade (styles/<name>/style.json)")
    own.add_argument("--aspect", default="16:9", choices=list(assemble.CANVASES), help="the canvas")
    own.add_argument("--fit", default=None, choices=list(assemble.FITS),
                     help="cover = fill the canvas and crop; box = whole picture with bars (the default for a "
                          "portrait canvas when the profile's reels are not portrait)")
    own.add_argument("--template", metavar="REEL",
                     help="follow one of the profile's reels shot for shot (its id from `easyedit.style templates`)")
    own.add_argument("--cards", nargs="+", metavar="IMAGE|FOLDER",
                     help="pictures (screenshots, tweets, charts) popped in as cards where the template has them")
    own.add_argument("--cta", help="closing card text, lines separated by |, e.g. \"NAPOLEON|@yourhandle\"")
    own.add_argument("--hook", help="a line held on screen from the first frame")
    return p.parse_args(argv)


def _own_footage(a, job: Path, dl: Path, canvas: tuple[int, int]) -> dict:
    """Sources, plan, captions and settings for an edit of the user's own clips."""
    from . import style as stylemod
    files: list[Path] = []
    fetched: list[Path] = []     # found online: channel intros and end screens get trimmed like any upload
    for spec in a.footage:
        pth = Path(spec)
        if pth.is_dir():
            files += sorted(q for q in pth.iterdir() if q.suffix.lower() in VIDEO_EXT)
        elif pth.is_file():
            files.append(pth.resolve())
        elif fetch.is_url(spec):
            got = fetch.download(spec, dl / "montage", False, a.cookies_from_browser)
            files.append(got)
            fetched.append(got)
        elif spec.startswith("yt:") or not any(ch in spec for ch in ("\\", "/", ":")):
            query = spec[3:] if spec.startswith("yt:") else spec
            try:
                got = fetch.best_sources([query], dl / "montage", kind="montage", movie=query, n=2, min_dur=20,
                                         max_dur=900, cookies=a.cookies_from_browser)
            except Exception as e:
                die(f"footage search failed for {query!r}: {e}")
            files += got
            fetched += got
        else:
            die(f"footage not found: {spec}")
    if not files:
        die("no video files in --footage")
    profile = stylemod.load(a.style) if a.style else None
    if not a.music and not profile:
        die("a reel needs a song: --music <file|url> (every cut lands on its beats); with --style the tool picks one")
    portrait = canvas[1] > canvas[0]
    fit = a.fit or ("box" if profile and portrait and profile["frame"]["aspect"] != "9:16" else "cover")
    template = stylemod.template(a.style, a.template) if a.template and a.style else None
    if a.template and not a.style:
        die("--template needs --style <name>: the reel comes from that profile")

    src_file = job / "sources.json"
    specs = {"speech": a.speech, "montage": None, "music": a.music, "footage": [str(f) for f in files],
             "cards": list(a.cards or []), "cta": a.cta, "template": a.template}
    cached = read_json(src_file) if src_file.exists() and not a.fresh else None
    from . import cards as cardmod
    card_shape = cardmod.card_size(profile["frame"]["aspect"] if profile else None, canvas)
    if cached and cached.get("specs") == specs and all(
            Path(f).exists() for k in ("speech", "montage", "music", "card") for f in cached.get(k, [])):
        speech_files, montage_files, music_files, card_files = ([Path(f) for f in cached.get(k, [])]
                                                                for k in ("speech", "montage", "music", "card"))
        log("sources: reusing locked footage")
    else:
        montage_files = files
        speech_files = fetch.resolve(a.speech, a.speech, dl / "speech", n=1, min_dur=1, max_dur=3600,
                                     cookies=a.cookies_from_browser) if a.speech else []
        if a.music:
            music_files = fetch.resolve(a.music, a.music, dl / "music", n=1, min_dur=10, max_dur=900,
                                        audio_only=True, cookies=a.cookies_from_browser)
        else:   # no song given: one that sounds like the creator's reels (tempo, a real drop)
            from . import music as musicmod
            music_files = [musicmod.pick(job, profile, dl / "music", cookies=a.cookies_from_browser)]
        pictures: list[Path] = []
        for spec in a.cards or []:
            pth = Path(spec)
            if pth.is_dir():
                pictures += sorted(q for q in pth.iterdir() if q.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", *VIDEO_EXT))
            elif pth.is_file():
                pictures.append(pth.resolve())
            else:
                die(f"card not found: {spec}")
        card_files = cardmod.insert_clips(job, pictures, card_shape)
        if a.cta:
            card_files.append(cardmod.cta_clip(job, a.cta, card_shape))
        write_json(src_file, {"specs": specs, "speech": [str(f) for f in speech_files],
                              "montage": [str(f) for f in montage_files], "music": [str(f) for f in music_files],
                              "card": [str(f) for f in card_files]})
    manifest = clips._manifest(job)   # own clips are footage from their first frame to their last
    for f in [*montage_files, *speech_files]:
        m = manifest.setdefault(str(f), {})
        m["whole"] = f not in fetched
        m["roles"] = sorted(set(m.get("roles", [])) | {"montage" if f in montage_files else "speech"})
    write_json(job / "clips.json", manifest)

    # who talks: the --speech clip, or narration found in the footage itself
    spoken = list(speech_files)
    if not spoken and a.captions != "none":
        found = []
        for f in montage_files:
            words = transcribe.transcribe(f, job / "work" / f"words-{f.stem}.json", a.language)
            if len(words) >= 8:
                found.append((len(words), f))
        if found:
            spoken = [max(found)[1]]
            log(f"speech: narration found in {spoken[0].name}")
    mode = "voiceover" if spoken and a.captions != "none" else "montage"
    if spoken and spoken != speech_files:   # the sequence names its audio by the sources list
        src = read_json(src_file)
        src["speech"] = [str(f) for f in spoken]
        write_json(src_file, src)
        speech_files = spoken

    info = probe(montage_files[0])
    if fit == "box":
        scale = min(canvas[0] / info["width"], canvas[1] / info["height"])
        picture = (info["width"] * scale, info["height"] * scale)
    else:
        picture = canvas
    applied = stylemod.apply(profile, mode, canvas, fit, picture)
    plan_file = job / "plan.json"
    if plan_file.exists() and not a.fresh:
        plan = read_json(plan_file)
    else:
        plan = {"title": a.movie, "year": None, "speech_scene": "the strongest passage",
                "palette": applied["palette"], "grade": applied["grade"], "look": applied["look"],
                "sfx_levels": applied["sfx_levels"], "source": "footage", "style": a.style, "mode": mode}
        if a.hook:
            hold = (template or {}).get("hook", {}).get("hold") or (2.5 if mode == "voiceover" else 0)
            plan["hook"] = {"text": a.hook, "hold": hold if mode == "voiceover" else 0}
        write_json(plan_file, plan)

    q, speech_in, speech_len, words = None, 0.0, 0.0, []
    if mode == "voiceover":
        pick_file = job / "quote.json"
        words = transcribe.transcribe(speech_files[0], job / "work" / f"words-{speech_files[0].stem}.json", a.language)
        if pick_file.exists() and not a.fresh and read_json(pick_file).get("files") == [str(speech_files[0])] \
                and read_json(pick_file).get("mode") == a.captions:
            q = read_json(pick_file)
        else:
            q = quote.choose([words], plan, a.llm) if a.captions == "pick" else quote.whole(words, plan, a.llm)
            q["files"] = [str(speech_files[0])]
            q["mode"] = a.captions
            write_json(pick_file, q)
        speech_in = max(0.0, q["start"] - assemble.LEAD)
        speech_len = q["end"] + assemble.TAIL - speech_in
    transcribe.release()

    ps = applied["settings"]
    settings = {"fps": a.fps, "cut_rate": a.cut_rate, "sfx": a.sfx, "depth": a.depth,
                "montage_length": a.montage_length if a.montage_length != 21.0 else ps["montage_length"],
                "hero_length": a.hero_length if a.hero_length != 3.4 else ps["hero_length"],
                "velocity": (not a.no_velocity) and ps["velocity"],
                "aspect": a.aspect, "fit": fit, "style": a.style, "captions": a.captions}
    saved = job / "settings.json"
    write_json(saved, {**(read_json(saved) if saved.exists() else {}), **settings})
    from . import remake
    return {"plan": plan, "speech": speech_files, "montage": montage_files, "music": music_files, "quote": q,
            "fit": fit, "applied": applied, "mode": mode, "speech_in": speech_in, "speech_len": speech_len,
            "settings": settings, "template": template,
            "words": remake.speech_words(words, speech_in, hold=float((plan.get("hook") or {}).get("hold") or 0)) if words else None,
            "hook_hold": float((plan.get("hook") or {}).get("hold") or 0) if mode == "voiceover" else 0.0,
            "cards": [f"card:{Path(f).name}" for f in card_files if Path(f).name != "cta.mp4"],
            "cta": "card:cta.mp4" if a.cta and any(Path(f).name == "cta.mp4" for f in card_files) else None}


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
    canvas = assemble.CANVASES[a.aspect]
    fit = a.fit or "cover"
    own = None
    if a.footage:
        own = _own_footage(a, job, dl, canvas)
        plan, speech_files, montage_files, music_files, q, fit = (own["plan"], own["speech"], own["montage"],
                                                                 own["music"], own["quote"], own["fit"])
        if canvas != (assemble.W, assemble.H) or fit != "cover":
            log(f"canvas: {canvas[0]}x{canvas[1]} {fit}")
    else:
        plan, speech_files, montage_files, music_files, q = _film(a, job, dl)
    fd, analyses = None, []
    for f in montage_files:
        cache = job / "work" / f"shots-{f.stem}.json"
        cached_shots = read_json(cache) if cache.exists() else None
        if cached_shots and cached_shots.get("v") == shots.CACHE_VERSION:
            analyses.append(cached_shots)
            continue
        if fd is None:
            fd = FaceDetector()
        analyses.append(shots.analyze(f, cache, fd, clips.whole(job, f)))
    pool = shots.rank(analyses)
    curate_file = job / "curate.json"
    curation = read_json(curate_file) if curate_file.exists() else {}
    if curation:
        pool = shots.curate(pool, analyses, curation)
    log(f"shots: {len(pool)} candidates across {len(analyses)} source(s)")
    music_beats = beats.analyze(music_files[0], job / "work" / f"beats-{music_files[0].stem}.json")

    if a.fresh and (job / sequence.FILE).exists():   # a fresh plan: the old layout names shots that may be gone
        (job / sequence.FILE).replace(job / "timeline.before-fresh.json")
    tl_file, cur_file = job / sequence.FILE, job / "curate.json"
    if own and tl_file.exists() and cur_file.exists() and cur_file.stat().st_mtime > tl_file.stat().st_mtime:
        log("layout: curate.json is newer than the layout; laying the footage out again")
        tl_file.replace(job / "timeline.before-curate.json")
    if own and not tl_file.exists():
        from . import remake
        raw = remake.generate(job, sources=read_json(job / "sources.json"), analyses=analyses, pool=pool,
                              beats=music_beats, quote=q, fps=a.fps, applied=own["applied"], settings=own["settings"],
                              mode=own["mode"], speech_in=own["speech_in"], speech_len=own["speech_len"],
                              words=own["words"], template=own["template"], cards=own["cards"], cta=own["cta"],
                              hook_hold=own["hook_hold"])
        sequence.save(job, raw)
    seq = sequence.load(job)   # laid out by hand in the editor: that layout is what gets cut
    if seq:
        key = sequence.key(seq, q, plan, a.fps, a.sfx, analyses, canvas, fit)
    else:
        try:   # a montage that shows some shots twice is laid out with that in mind (shots.laps)
            laps = assemble.has_laps(assemble.timeline(q, pool, music_beats, a.fps, a.montage_length, a.hero_length,
                                                       a.cut_rate, curation.get("cuts")))
        except (IndexError, ValueError):
            laps = False
        key = assemble.build_key(q, plan, music_files[0], a.fps, a.montage_length, a.hero_length, analyses, curation,
                                 a.cut_rate, not a.no_velocity, a.sfx, laps, canvas, fit)
    key_file = job / "work" / "build.key"
    edit_js = job / "render" / "edit.js"
    if (key_file.exists() and key_file.read_text() == key and edit_js.exists()
            and (job / "render" / "footage.mp4").exists()):
        log("build: reusing footage + edit.js")
        edit = json.loads(edit_js.read_text(encoding="utf-8").removeprefix("window.EDIT=").rstrip().rstrip(";"))
    elif seq:
        edit = sequence.build(job, seq, plan=plan, quote=q, analyses=analyses, fps=a.fps, sfx=a.sfx,
                              canvas=canvas, fit=fit)
        key_file.write_text(key)
    else:
        edit = assemble.build(job, plan=plan, quote=q, speech=speech_files[q["clip"]], analyses=analyses,
                              pool=pool, music=music_files[0], music_beats=music_beats, fps=a.fps,
                              montage_len=a.montage_length, hero_len=a.hero_length, cut_rate=a.cut_rate,
                              velocity=not a.no_velocity, sfx=a.sfx, cuts=curation.get("cuts"),
                              canvas=canvas, fit=fit)
        key_file.write_text(key)
    depth_file = job / "render" / "depth.mp4"
    if a.depth:
        from . import depth
        depth.ensure(job)
    elif depth_file.exists():
        depth_file.unlink()  # depth was switched off; a stale map must not ride along
    if a.no_render:
        log(f"built {job / 'render'}; skipping render")
        return
    out = a.output or str(job / f"{slugify(a.movie)}{'-preview' if a.preview else ''}.mp4")
    render.render(job, edit, Path(out), quality="draft" if a.draft else "high",
                  only=tuple(a.preview) if a.preview else None)


def _film(a, job: Path, dl: Path):
    """The classic path: plan a film, find its footage, pick the quote."""
    plan_file = job / "plan.json"
    if plan_file.exists() and not a.fresh:
        plan = read_json(plan_file)
    else:
        plan = planner.make_plan(a.movie, a.llm, anime.detect(job, a.movie, a.anime))
        write_json(plan_file, plan)
    ani = anime.of(job) if a.anime is not False else None

    # lock sources: YouTube search order drifts between runs, which would silently change the edit
    src_file = job / "sources.json"
    specs = {"speech": a.speech, "montage": a.montage, "music": a.music}
    cached = read_json(src_file) if src_file.exists() and not a.fresh and not a.refetch else None
    if cached and cached.get("specs") == specs and all(
            Path(f).exists() for k in ("speech", "montage", "music") for f in cached[k]):
        speech_files, montage_files, music_files = ([Path(f) for f in cached[k]]
                                                    for k in ("speech", "montage", "music"))
        log("sources: reusing locked downloads")
    else:
        title = plan["title"]
        if a.speech:
            speech_files = fetch.resolve(a.speech, plan["speech_query"], dl / "speech", n=2, min_dur=20,
                                         max_dur=600, cookies=a.cookies_from_browser, upgrade=a.refetch)
        else:
            speech_files = fetch.best_sources(
                [plan["speech_query"], f"{title} {plan.get('speech_scene', '')[:60]} "
                                       f"{'english dub' if ani else 'scene 4K'}"], dl / "speech",
                kind="speech", movie=anime.names(ani) if ani else title, anime=bool(ani), n=2, min_dur=20, max_dur=600, cookies=a.cookies_from_browser, upgrade=a.refetch)
            # a clip with subtitles burned in goes last: the quote is picked from the clean one when it can be
            speech_files.sort(key=lambda f: vision.subtitle_band(f, probe(f)["duration"]) is not None)
        montage_files = []
        if a.montage:
            for spec in a.montage:
                try:
                    montage_files += fetch.resolve(spec, spec, dl / "montage", n=1, min_dur=60, max_dur=1500,
                                                   cookies=a.cookies_from_browser, upgrade=a.refetch)
                except Exception as e:
                    log(f"montage source skipped: {e}")
        else:
            try:  # clean scene compilations first, the planner's own ideas (often trailers) as a fallback
                montage_files = fetch.best_sources(
                    [*(anime.footage_queries(ani) if ani else
                       [f"{title} 4K visuals", f"{title} 4K scenes", f"{title} amazing shots", f"{title} scenepack 4K"]),
                     *plan["montage_queries"][:2]], dl / "montage", kind="montage",
                    movie=anime.names(ani) if ani else title, anime=bool(ani), n=2,
                    min_dur=40, max_dur=1500, cookies=a.cookies_from_browser, upgrade=a.refetch)
            except Exception as e:
                log(f"montage sources: {e}")
        if not montage_files:
            montage_files = speech_files
        if a.refetch:  # choices that named shots of the old footage no longer mean anything
            for name in ("curate.json", "timeline.json"):
                old = job / name
                if old.exists():
                    old.replace(job / name.replace(".json", ".before-refetch.json"))
        music_files = fetch.resolve(a.music, a.music or plan["music_query"], dl / "music", n=1, min_dur=60,
                                    max_dur=900, audio_only=True, cookies=a.cookies_from_browser)
        # clips added by hand in the editor stay in the edit when the sources are looked up again
        speech_files = speech_files + clips.extras(job, "speech", speech_files)
        montage_files = montage_files + clips.extras(job, "montage", montage_files)
        music_files = clips.extras(job, "music", [])[-1:] or music_files   # a song picked in the editor stays the song
        write_json(src_file, {"specs": specs, "speech": [str(f) for f in speech_files],
                              "montage": [str(f) for f in montage_files],
                              "music": [str(f) for f in music_files]})

    # Existing arrangements keep their sources. Report weak originals rather than disguising them
    # as HD just because the final canvas is 1920x1080.
    for media in dict.fromkeys([*speech_files, *montage_files]):
        info = probe(media)
        tier = fetch.video_height(info)
        mbps = media.stat().st_size * 8 / max(1, info.get("duration", 0)) / 1_000_000
        if tier < fetch.MIN_HEIGHT or mbps < 1.5:
            log(f"source quality: {media.name}: {info.get('width', 0)}x{info.get('height', 0)}, "
                f"{mbps:.2f} Mbps; a sharper original is needed for a sharper export")

    pick_file = job / "quote.json"
    spoken = [transcribe.transcribe(f, job / "work" / f"words-{f.stem}.json", a.language) for f in speech_files]
    if pick_file.exists() and not a.fresh and read_json(pick_file).get("files") == [str(f) for f in speech_files]:
        q = read_json(pick_file)
    else:
        q = quote.choose(spoken, plan, a.llm)
        q["files"] = [str(f) for f in speech_files]
        write_json(pick_file, q)

    transcribe.release()
    return plan, speech_files, montage_files, music_files, q


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
