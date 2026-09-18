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

The user can watch progress in the browser while you poll.

## Troubleshooting

See the troubleshooting table in `README.md`. The most common problems are 403s from YouTube (upgrade yt-dlp)
and age-restricted clips (`--cookies-from-browser chrome`).

Use footage and music the user has the right to use.
