# Contributing to easyedit

Thanks for helping! Questions and ideas are welcome in the **[Discord](https://discord.gg/zEB4VjmfSb)**, and bugs go in [GitHub issues](https://github.com/blixvip/easyedit/issues/new/choose).

## Finding something to work on

- [Good first issues](https://github.com/blixvip/easyedit/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) are small and list the files to touch.
- [Help wanted](https://github.com/blixvip/easyedit/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) issues are bigger.
- Comment on the issue to claim it so two people don't build the same thing. If you go quiet for a week, it opens up again.
- Have your own idea? Open a [Discussion](https://github.com/blixvip/easyedit/discussions) first for anything larger than a bug fix.

By contributing, you agree your work is released under the project's [MIT License](LICENSE).

## Setup

You need **Python 3.10+**, **Node.js 22+**, and **FFmpeg and ffprobe** on your `PATH`.

```sh
git clone https://github.com/<you>/easyedit.git
cd easyedit
npm install                          # the HyperFrames renderer
pip install -r requirements.txt
python -m easyedit.doctor            # checks every requirement and says what's missing
```

Run `python -m easyedit.doctor` first whenever something behaves strangely — it is the authoritative list of what the project needs, and it is shared by the CLI, the web UI and agents.

```sh
python -m easyedit "The Wolf of Wall Street"    # the CLI
python -m easyedit.web                          # http://127.0.0.1:4331
```

No API keys. The LLM step reuses a Claude Code or Codex login you already have, and `--llm none` falls back to deterministic heuristics for every stage, which is the fastest way to test changes that aren't about the model.

An NVIDIA GPU is optional (NVENC encoding, CUDA Whisper). A first run downloads the Whisper model, a face-detection model and two fonts into `.cache/`.

## Before you open a pull request

```sh
python -m unittest discover
```

The README currently says `python -m pytest`. That's wrong — pytest isn't in `requirements.txt` and the one existing test uses `unittest`. Fixing that is an open issue; until then use the command above.

- Keep changes small and focused. One fix or feature per pull request.
- Check UI changes in a real browser and add a screenshot or GIF.
- There is no linter or formatter. Match the style of the file you are editing.
- Use the helpers in `easyedit/util.py` (`log`, `die`, `need`, `run`, `output`, `probe`) rather than calling `subprocess` directly, so failures are reported the same way everywhere.
- Don't commit anything from `jobs/`, `.cache/`, `node_modules/`, or any footage.
- Don't add runtime dependencies without saying why in the pull request. If you do, update `requirements.txt` and add the check to `easyedit/doctor.py`.
- Windows is the best-tested platform. If you're on macOS or Linux, say so in the pull request — several paths are Windows-only today and those fixes are wanted.

## Where things live

- `easyedit/__main__.py`: the CLI, the stage ordering, and every cache key. Read this first — it is the map of the whole pipeline.
- `easyedit/util.py`: `log`/`die`/`need`/`run`/`output`/`probe`/`slugify`/`read_json`/`write_json`. Every module depends on it.
- `easyedit/assemble.py`: timeline math, the footage cut, the ducked audio mix, and `render/edit.js`.
- `easyedit/web.py`: the entire local web UI and its JSON API, in the standard library — no Flask.
- `easyedit/doctor.py`: the single source of truth for system requirements.
- Stage modules: `fetch.py` (download), `transcribe.py` (Whisper), `quote.py` (line breaking), `beats.py` (onset detection), `shots.py` + `vision.py` (shot scoring and framing), `plan.py` (the edit plan), `design.py`, `render.py` (HyperFrames + Chrome).

Made an edit with it? Show it off in the [Discord](https://discord.gg/zEB4VjmfSb).
