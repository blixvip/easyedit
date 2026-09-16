---
name: easyedit
description: Make a movie fan edit with easyedit - the film's best speech with animated word-by-word captions, then a beat-cut montage and a black-and-white title shot. Use when the user says "/easyedit <movie>", "make an edit of <movie>", "fan edit", "movie edit", or wants another easyedit video.
---

# easyedit

easyedit is installed at `{{ROOT}}`. Run it with `{{PY}}`.
Read `{{ROOT}}/AGENTS.md` before your first edit in a session. It has the whole workflow, and it's short.

## Make an edit

1. `cd {{ROOT}}` and run `{{PY}} -m easyedit.doctor`. Fix anything marked XX.
2. Build without rendering: `{{PY}} -m easyedit "<Movie>" --no-render`.
3. Check it before you spend ~10 minutes on the render, as described in AGENTS.md:
   - Is the quote in `jobs/<movie>/quote.json` actually the iconic line? `source: heuristic` means the AI writer was
     unavailable, so choose the quote yourself.
   - Run `{{PY}} -m easyedit.sheet "<Movie>"` and look at `qa/candidates.jpg` and `qa/footage.jpg`. Exclude text
     cards, repeats and badly cropped shots. Pin a sequence that builds, and pick the hero shot in `curate.json`.
4. Render: `{{PY}} -m easyedit "<Movie>"` (same flags as the build), then `{{PY}} -m easyedit.sheet "<Movie>" --final`
   and look at the stills.
5. Give the user the path `jobs/<slug>/<slug>.mp4`.

If the easyedit web UI is running (`http://127.0.0.1:4331`), you can also start jobs with
`POST /api/new {"movie": "..."}` and watch `GET /api/jobs`.
