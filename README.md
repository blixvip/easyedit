<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="easyedit/web/assets/logo.svg">
    <img alt="easyedit" src="easyedit/web/assets/logo-light.svg" height="72">
  </picture>
</p>

<p align="center"><b>Type a movie. Get a captioned speech + beat-cut fan edit.</b><br>
Local-first · no API keys · works with your AI bot</p>

![easyedit web UI](docs/ui.png)

Type a movie name and get a finished fan edit. It makes the kind that's all over social media: the film's best
speech with animated word-by-word captions, then a fast montage cut to the beat of a song.

```bash
python -m easyedit "The Wolf of Wall Street"
```

The finished video is saved to `jobs/<movie>/<movie>.mp4` as 1920×1080 at 60fps.

## What it does

| Stage | How |
|---|---|
| **Plan** | An LLM picks the film's most iconic speech, search queries for the scene and montage footage, a song that fits the mood, a color palette for the captions and a color grade. It uses your Claude Code login or the `codex` CLI, so you don't need API keys. If neither is available it uses built-in defaults. |
| **Source** | `yt-dlp` searches YouTube and downloads the speech scene, 1–2 montage sources and the song. Any of these can be replaced with a local file or a URL. |
| **Transcribe** | `faster-whisper` gives a timestamp for every word, on the GPU when one is available. |
| **Quote** | The LLM picks the best continuous 9–24s passage and marks emphasis words by role: positive, negative, gold or cool. The passage is split into caption lines at pauses. A heuristic takes over if the LLM fails. |
| **Faces** | OpenCV YuNet tracks the speaker at 10Hz so the camera can push in and follow their face. |
| **Shots** | Finds scene cuts in the montage sources and scores each shot on motion, sharpness, contrast, color and faces. It removes black bars, title cards and near-duplicate shots. |
| **Beats** | A numpy beat tracker (spectral flux plus dynamic programming) finds the song's tempo, its beats and the drop. The montage starts exactly on the drop. |
| **Assemble** | Cuts land exactly on real beats in a repeating short/long pattern. It encodes the footage frame-accurately, and the song stays quiet under the speech, then jumps to full volume on the drop. |
| **Render** | A [HyperFrames](https://github.com/heygen-com/hyperframes) composition (`template/`) adds the effects: face-follow camera, captions that go from blur to outline to glowing fill, echo text behind emphasis words, whip transitions with directional motion blur, flashes, beat-synced zoom pulses, film grain, and a black-and-white final shot with the title card. It renders as parallel sections (each one streams its frames straight into the encoder, so nothing piles up on disk), then the soundtrack is muxed in and the video is encoded once for delivery. |

## Setup

Requirements: Python 3.10+, Node.js 22+, FFmpeg on `PATH`. An NVIDIA GPU is optional: it makes transcription
and the final encode faster. Everything else (fonts, the face model, Chrome for rendering) downloads on first
run into `.cache/`.

```bash
git clone https://github.com/blixvip/easyedit && cd easyedit
npm install
pip install -r requirements.txt
python -m easyedit.doctor     # checks everything and tells you what to fix
```

Connect an AI account so it can pick the scene, the quote and the song. One is enough, and there are no API keys:
sign in to Claude Code (`claude auth login`) or Codex (`codex login`), or click **Connect** in the web UI. With no
account connected you still get an edit, but it runs on heuristics.

## Give it to your bot

easyedit is built so an AI agent can run the whole thing for you, from install to curating the shots to the
final render. Paste this into Claude Code, Codex or any agent that can use a terminal:

```text
Install easyedit from https://github.com/blixvip/easyedit. Read its AGENTS.md first and follow it.
Run "python -m easyedit.doctor" and fix anything it flags.
Then make a fan edit of "Gladiator" and tell me where the finished video is.
```

[`AGENTS.md`](AGENTS.md) is the agent's playbook. It covers setup, checking the quote, reading the shot contact
sheets, writing `curate.json`, rendering, and checking the stills afterwards. For Claude Code there's also a skill:

```bash
python -m easyedit.skill install    # then: /easyedit Gladiator
```

The web UI's **Bot** section has these prompts ready to copy, with your movie filled in. A bot on the same
computer can also drive the running app through its local API (`POST /api/new`, `GET /api/jobs`). See AGENTS.md.

## Web UI

```bash
python -m easyedit.web      # http://127.0.0.1:4331 (easyedit-web.cmd on Windows)
```

A gallery of every edit you have made (thumbnail, length, the line it captions) plus a box to start a new
one. Running jobs show a live stage/progress bar and their log, and can be stopped from the page. Click a
thumbnail to watch the edit in the browser.

**Connect** shows which AI accounts are signed in and checks this computer's tools. Its buttons open the sign-in
for Claude or Codex. **Give it to your bot** has copy-ready prompts for an agent.

## Usage

```bash
# fully automatic
python -m easyedit "Fight Club"

# steer any stage: local file, URL, or your own search query
python -m easyedit "Interstellar" \
  --speech "interstellar do not go gentle scene" \
  --montage ~/clips/interstellar-trailer.mp4 \
  --music "https://www.youtube.com/watch?v=..."

# fast iteration
python -m easyedit "Scarface" --draft                 # 30fps, draft quality
python -m easyedit "Scarface" --preview 15 25         # render only 15s-25s
python -m easyedit "Scarface" --no-render             # build footage + edit.js only
```

| Flag | Default | |
|---|---|---|
| `--speech / --montage / --music` | auto | file, URL or search query (`--montage` can be repeated) |
| `--llm` | `auto` | `claude`, `codex` or `none` |
| `--fps` | `60` | 24, 30 or 60 |
| `--montage-length` | `21` | seconds of beat cuts |
| `--hero-length` | `3.4` | seconds of the final black-and-white shot |
| `--language` | `en` | speech language (non-English uses whisper `large-v3`) |
| `--cookies-from-browser` | | for age-restricted YouTube clips, e.g. `chrome` |
| `--fresh` | | re-plan and re-pick instead of reusing the cache |

Every stage caches its results in `jobs/<movie>/`: downloads, transcripts, shot and beat analysis, `plan.json`,
`quote.json` and `sources.json` (which pins the chosen downloads, since YouTube search results drift).
Re-running is quick, and you can edit `quote.json` or `render/edit.js` by hand and render again.

To hand-pick the montage, add `jobs/<movie>/curate.json`. Shot ids are `<source stem>@<start seconds>`, and
they're listed in `work/shots-*.json`:

```json
{"pin": ["TaaDkbG3I7g@110.52", "ebjTAHwSWMw@73.60"], "exclude": ["TaaDkbG3I7g@32.77"], "hero": "ebjTAHwSWMw@163.10"}
```

To see the shots, run `python -m easyedit.sheet "<movie>"`. It writes `qa/candidates.jpg` (every shot, numbered),
`qa/candidates.txt` (number → id) and `qa/footage.jpg` (the cut in order). Add `--final` to get stills from
the rendered video.

`pin` shots are used first, in that order. `exclude` shots are never used, and `hero` is the final black-and-white shot.

Environment overrides: `EASYEDIT_CLAUDE_MODEL`, `EASYEDIT_WHISPER` (a faster-whisper model name),
`EASYEDIT_PARALLEL` (render processes, default 2 - raise it if you have RAM to spare) and
`EASYEDIT_ENCODER=x264` (the default uses NVENC when the GPU has it).

Rendering is the slow part: roughly 5 frames/second at 1080p60 on a GTX 1650, so about 8 minutes
for a 40-second edit. `--fps 30` halves it.

## Tweaking the look

Everything visual is in `template/film.js`. It's a pure function of time, so any frame renders on its own.
To preview a built job in the browser:

```bash
cd jobs/<movie>/render && npx hyperframes preview
```

## Notes

Use footage and music you have the right to use. easyedit doesn't include any film or music content; it only
processes what you point it at or what it downloads into your local `jobs/` folder.

## When something goes wrong

| Symptom | Fix |
|---|---|
| YouTube downloads fail with HTTP 403 | `pip install -U "yt-dlp[default]"`. easyedit runs yt-dlp's JS challenges through Node, so Node must be on `PATH`. |
| "every download failed" | The clip is age-restricted: `--cookies-from-browser chrome`. Or pass the scene yourself with `--speech <file|url>`. |
| The quote is dull, or the wrong scene | Both LLM routes were rate-limited and the heuristic ran. Check the log for `quote (heuristic)`, then delete `jobs/<movie>/quote.json` and run again. |
| Render dies on temp space | Disk capture needs ~9 MB per frame. easyedit already streams frames instead, so if you see this, something forced the old path - make sure `PRODUCER_FORCE_SCREENSHOT` is not set. |
| Render is killed / the machine thrashes | Lower `EASYEDIT_PARALLEL` to 1. Each section is a separate Chrome. |
| Captions stall on silence | The passage spans dead air. `MAX_GAP` in `quote.py` controls how much is allowed. |
| Black bars or a channel watermark survive | `letterbox()` in `vision.py` keeps only rows lit across the frame. Sources taller than 16:9 keep the top of the frame, so corner watermarks at the bottom get cropped off. If one still survives, exclude that shot in `curate.json` or crop the source yourself. |
| The montage is weak or repetitive | Run `python -m easyedit.sheet "<movie>"`, then pin a sequence and exclude text cards in `curate.json`. |

## How it fits together

```
easyedit/
  __main__.py   CLI and the per-stage cache keys
  plan.py       movie title  -> scene / montage / music queries, palette, grade
  fetch.py      yt-dlp search + download (or your own files and URLs)
  transcribe.py faster-whisper word timings
  quote.py      the passage to caption, its lines and emphasis words
  vision.py     frame sampling, picture-area detection, YuNet faces
  shots.py      scene cuts and how edit-worthy each shot is
  beats.py      onset envelope, tempo, beat tracking, the drop
  assemble.py   frame-exact timeline, footage cut, ducked soundtrack, edit.js
  render.py     parallel HyperFrames sections, mux, delivery encode
  web.py        the local web UI (web/index.html, web/assets/ logo + icon)
  doctor.py     setup check: AI accounts + tools (used by the UI and by agents)
  sheet.py      contact sheets for checking shots and the final render
  skill.py      installs the Claude Code skill
template/       index.html + film.js: every visual effect, as a function of time
skills/         the /easyedit skill for Claude Code
AGENTS.md       the playbook for AI agents
```

MIT license.
