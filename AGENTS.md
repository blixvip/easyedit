# easyedit for AI agents

You're a coding agent (Claude Code, Codex, or similar) and someone asked you for a movie fan edit. This file is
everything you need. The user gives you a movie name. You give them back an MP4: the film's best speech with
animated captions, then a beat-cut montage and a black-and-white title shot.

## 1. Install (once per computer)

```bash
git clone https://github.com/blixvip/easyedit && cd easyedit
npm install
python -m pip install -r requirements.txt
python -m easyedit.doctor
```

`doctor` prints one line per requirement and exits non-zero until everything passes. Fix each `XX` line with the
command it suggests: FFmpeg on PATH, Node 22+, `npm install`, the pip packages. Use a Python where the pip
install worked. On Windows that's often `py -3.12`, not the `python` on PATH, so run every later command with that
same interpreter.

The AI writer (it picks the scene, the quote and the song) uses a login the user already has. It needs no API keys:

- **Claude**: the user's Claude Code login (`claude auth login`). On macOS the login is kept in the keychain, where
  easyedit can't read it, so use Codex there.
- **Codex**: `codex login` (a ChatGPT account).

Don't sign in for the user. If `doctor` shows neither account connected, ask them to run one of those commands,
or carry on without one. easyedit still works, and you then pick the quote yourself (step 3).

Optional: `python -m easyedit.skill install` adds a `/easyedit` skill to Claude Code.

## 2. Build the edit, don't render yet

```bash
python -m easyedit "Movie Title" --no-render
```

This downloads the footage, transcribes it, picks the quote, finds the beats and cuts the footage, which takes
about 5-8 minutes. Everything is cached in `jobs/<slug>/`. Rendering is the slow part (about 10 minutes), so check
the build first.

## 3. Check the build (this is what makes an edit good)

**The plan.** `jobs/<slug>/plan.json` holds the search queries, the song, the caption palette and the color
grade. If the film is newer than the AI writer's training data, the plan is guesswork. Search the web, then
rewrite `speech_query`, `montage_queries` and `music_query` with real YouTube URLs, and delete `sources.json`.
Official studio channels and Movieclips give clean 1080p/4K sources.

**The quote.** Run `python -m easyedit.sheet "<Movie>" --report` first. `qa/report.txt` prints the passage
with its emphasis words; read that instead of `quote.json`. If `source: heuristic` (the report flags this) or
the words aren't the scene people remember, pick it yourself: delete `quote.json` and rerun the build with
`--llm` connected, or transcribe manually. Word timings are in `jobs/<slug>/work/words-*.json`. The passage
must be one continuous span of 9-24s with no silence longer than 1.5s. Lines should be 1-5 words that break
at punctuation. Give each emphasised word a `role` (`positive`, `negative`, `gold` or `cool`, roughly one
word in four).

**The shots.**

```bash
python -m easyedit.sheet "Movie Title"
```

`--report` (included by default in the run above) also writes `qa/report.txt`: a text list of every shot with
its id, score, face flag and suspect flags, plus `qa/suggest-curate.json`, a ready-made curate draft. Read the
text first - it's nearly free for you to read. Only open the images when the text leaves doubt:

- `jobs/<slug>/qa/candidates.jpg`: every montage shot, numbered. `qa/candidates.txt` maps each number to its id.
- `jobs/<slug>/qa/footage.jpg`: the assembled cut, in order.

Look for these problems: title cards, channel watermarks, the same shot several times, shots from the speech
scene, and faces cropped out. (White-on-black title cards are already flagged and ranked down automatically.)
Then write `jobs/<slug>/curate.json` - start from `qa/suggest-curate.json` and edit it:

```json
{
  "pin": ["<id>", "<id>", "..."],
  "exclude": ["<id>", "..."],
  "hero": "<id of a shot at least 3.4s long, ideally a face>"
}
```

`pin` is the montage in order, with about 24 shots for the default 21s. Make it build like a story, from calm to
intense, and save the most striking image for `hero`, the black-and-white final shot. Unpinned shots fill any
gaps. Run the `--no-render` build again and re-check `qa/report.txt` (or `qa/footage.jpg` if unsure).

If there are too few good shots, add sources with `--montage <url>`, which you can repeat. Pass the same flags
on every later run.

## 4. Render and verify

```bash
python -m easyedit "Movie Title"            # add the same --speech/--montage/--music flags you used
python -m easyedit.sheet "Movie Title" --final
```

Look at `qa/final.jpg`. The captions should be readable, faces in frame, no watermark, and the title card on
the last shot. The video is `jobs/<slug>/<slug>.mp4`, at 1920×1080 and 60fps. Tell the user that path. Don't
say it looks good unless you've looked at the stills.

Token discipline: the `--report` text and `report.txt` are cheap to read; every image you open costs far more.
Read text first, images only for doubt.

## Driving the web UI instead

If `python -m easyedit.web` is running (default `http://127.0.0.1:4331`):

| Call | Does |
|---|---|
| `POST /api/new {"movie": "...", "speech": null, "montage": [], "music": null, "draft": false}` | starts a full run |
| `GET /api/jobs` | every job, with `running`, `stage`, `progress` and `video` once it's done |
| `GET /api/log/<slug>` | the log tail and progress |
| `POST /api/stop {"slug": "..."}` | stops a run |
| `GET /api/setup` | the doctor report (accounts and tools) |
| `GET /api/edit/<slug>/clips` | every source in the edit: role, origin, resolution, bitrate, shots |
| `GET /api/edit/<slug>/clips/search?site=youtube\|clipcafe\|sakuga\|filmgrab&q=...` | clips on that site (empty `q` searches the film's title) |
| `POST /api/edit/<slug>/clips/add {"item": <a search result, or {"site": "link", "url": "<link or file path>"}>, "role": "montage"\|"speech"}` | downloads the clip, reads its shots or words, adds it to the edit (follow `/api/log/<slug>`) |
| `POST /api/edit/<slug>/clips/remove {"path": "...", "role": "montage"\|"speech"}` | takes a source out of the edit |

| `GET /api/edit/<slug>/anime/search?q=...` then `POST /api/edit/<slug>/anime/set {"id": <AniList id or null>}` | links the edit to an anime; `twixtor`, `themes` (openings) and `sakuga` searches then look for that anime |

Without the server: `python -m easyedit.clips <slug> montage '{"site": "link", "url": "https://..."}'`.
The role is `montage`, `speech` or `music` (`music` replaces the song).

**Anime.** A title AniList knows as an anime is built as one automatically (dub speech, twixtor and scenepack
footage, anime grade); the log prints an `anime:` line. Pass `--anime` or `--no-anime` to decide it yourself.
Speech is transcribed as English, so the speech clip must be the English dub.

The user can watch progress in the browser while you poll.

## A reel from a topic alone

```bash
python -m easyedit.reel "Genghis Khan" --style nio_trade --template Dcc45l-PonG --no-render
python -m easyedit.reel "Genghis Khan" --style nio_trade --template Dcc45l-PonG
```

`reel` writes an original script in the creator's shape (hook line, 60-80 word narration, closing card, picture
and footage searches) with the user's Claude/Codex login, speaks it with neural TTS, pulls the pictures from
Wikimedia Commons (credits in `footage/reels/<slug>/images/credits.txt`), searches YouTube for moving footage,
picks a track by tempo and drop, and runs the own-footage build below. `--script file.json` uses a script you
wrote; `--music` your track; `--no-footage` pictures only. Check `jobs/<slug>/qa/` the same way before the render.

## Your own footage, in a creator's style

easyedit can also cut clips the user already has, the way a creator they like cuts. Two steps:

**1. Measure the creator** (once per creator). Put their reels in `styles/<name>/reels/*.mp4`, then

```bash
python -m easyedit.style analyze <name>          # -> styles/<name>/style.json, report.txt, sheets/
python -m easyedit.style report <name>           # the report again, plain text, cheap to read
```

`report.txt` says what was measured: cut lengths and how they sit on the beat, speed ramps, camera moves,
grade, where the captions are and how they behave (word by word or a static hook line), flashes, shakes,
faces, speech share, sound hits, and how the reels open and close. It splits the reels into `voiceover`
(talking over pictures) and `montage` (music and cuts). Read it before cutting anything; if a number looks
wrong, look at `sheets/<id>.jpg` (30 frames of one reel) and `sheets/<id>-cuts.jpg` (the first frame after
every cut). Only measurements are kept; nothing from the reels ever goes into an edit.

Getting reels off Instagram: `python -m easyedit.style pull <handle>` prints the recipe (yt-dlp no longer
works there; the Aside browser REPL plus `tools/ig_pull.js` does, with `pull <handle> --receive` catching
the files). A folder of mp4s from anywhere works the same: `analyze C:\path	oolder`.

**2. Cut the user's clips**

```bash
python -m easyedit "my reel" --footage C:\clips --music C:\song.mp3 --style <name> --aspect 9:16 --no-render
python -m easyedit "my reel" --footage C:\clips --speech talk.wav --music C:\song.mp3 --style <name> --template <reel> --cards C:\shots --cta "TITLE|@handle" --hook "the hook line:" --aspect 9:16
python -m easyedit.sheet my-reel                 # qa/footage.jpg: is the crop right, faces in frame?
python -m easyedit "my reel" --footage C:\clips --music C:\song.mp3 --style <name> --aspect 9:16
python -m easyedit.style compare my-reel <name>  # qa/style-compare.txt: the render measured like a reel
```

- No `--music` with `--style`: the tool searches for a track itself (`music.py`: the profile's `music_queries`
  or phonk/edit-audio defaults), downloads a few, and keeps the one whose tempo is nearest the creator's bpm
  with the hardest drop; the choice is remembered in `jobs/<slug>/music-pick.json` (delete it to pick again).
  A track the user gives always wins.
- `--hook "text"` in a voiceover edit holds the line on the first frame for the template's hook time (2.5 s
  without a template) and the narration starts after it, the way a hook card runs before the voice.
- `--footage` takes files, folders, URLs or search queries (`yt:"napoleon 2023 trailer 4k"`): found clips are
  downloaded and trimmed like any upload. `--music` is required (every cut lands on its beats).
- Speech: `--speech talk.mp4`, or narration found in the footage itself. `--captions whole` captions every
  word (voiceover reels), `pick` picks one passage like a film quote, `none` makes a pure montage.
- `--aspect 9:16` is a 1080x1920 canvas. `--fit cover` fills it and crops (faces are kept in frame);
  `--fit box` keeps the whole picture with bars, which is the default when the creator's own reels are not
  portrait (many post 4:3 or square inside the reel frame).
- The profile sets the cut pattern, ramps, moves, grade, captions, sound hits and the length; `--montage-length`,
  `--hero-length`, `--no-velocity` override it. The layout is written to `timeline.json` and then cut by the
  sequence build, so the editor can move every clip, and `curate.json` still pins or excludes shots.
- Pass the same `--footage/--music/--style/--aspect` flags on every later run (the editor does, via settings.json).
- **Template mode, the closest match.** `python -m easyedit.style templates <name>` lists the measured reels
  (mode, length, cuts, cards, hook hold, outro). `--template <reel id>` then rebuilds that one reel's structure
  with your material: its cut times stretched to your narration, its opening hold, its card inserts where it
  has them (`--cards img1.png img2.png ...` or a folder, popped in with a punch and a luma dissolve), and its
  closing card (`--cta "LINE ONE|line two"`, black card of the reel's outro length). `--hook "text"` holds a
  line from the first frame for the reel's hook time. In voiceover edits every cut is nudged onto a word start
  and every sentence starts on a cut. Cards and the end card are listed under `card` in sources.json.
- Typical creator structure seen in the measurements: one long opening hold (6-9 s, often a screenshot or a
  talking clip) with the hook line, then a burst of 0.2-0.5 s cuts, card pops, a 4-9 s black text card to close.
  Give the tool at least one clip long enough to carry the opening hold (a 12 s still-with-push works).
- The layout is made once; to lay the footage out again after changing `--style`, `--captions` or the song, add
  `--fresh` (the old layout is kept as `timeline.before-fresh.json`).

`compare` measures the finished video exactly like a reel and prints profile | output | delta for the cut
length, beat alignment, mono share, grade, caption position and size, flashes and ramps.

## Troubleshooting

See the troubleshooting table in `README.md`. The most common problems are 403s from YouTube (upgrade yt-dlp)
and age-restricted clips (`--cookies-from-browser chrome`).

Use footage and music the user has the right to use.
