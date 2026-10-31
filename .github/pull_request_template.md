## What does this change?

<!-- A short description. Link the issue it fixes, e.g. "Fixes #12". -->

## How did you test it?

- [ ] `python -m unittest discover` passes
- [ ] `python -m easyedit.doctor` still reports a clean machine
- [ ] Made a real edit end to end (if the pipeline changed) — `--llm none` is fine
- [ ] UI changes checked in a real browser (screenshot or GIF below)

## Checklist

- [ ] One fix or feature, kept small and focused
- [ ] Used the `easyedit/util.py` helpers (`run`, `output`, `probe`, `need`, `die`) rather than calling `subprocess` directly
- [ ] No new runtime dependency — or it's explained above, `requirements.txt` is updated, and `doctor.py` checks for it
- [ ] Nothing committed from `jobs/`, `.cache/`, `node_modules/`, or any footage
- [ ] README updated if flags, behavior, or setup changed

<!-- Which OS did you test on? Windows is the best-tested; macOS and Linux reports are especially useful. -->

<!-- Questions? Ask in the Discord: https://discord.gg/zEB4VjmfSb -->
