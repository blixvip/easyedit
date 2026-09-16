# easyedit

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

Requirements: Python 3.10+, Node.js 22+, FFmpeg. An NVIDIA GPU is optional; it makes transcription faster.

```bash
git clone https://github.com/wasely/easyedit && cd easyedit
npm install
pip install -r requirements.txt
```

Optional LLM: log in to Claude Code (`claude`) or Codex (`codex login`). Without either you still get an edit,
but it runs on heuristics.

## Web UI

```bash
python -m easyedit.web      # http://127.0.0.1:4331 (easyedit-web.cmd on Windows)
```

A gallery of every edit you have made - thumbnail, length, the line it captions - plus a box to start a new
one. Running jobs show a live stage/progress bar and their log, and can be stopped from the page. Click a
thumbnail to watch the edit in the browser.

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

MIT license.
