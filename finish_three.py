"""Hand-finish the last three edits: correct sources, rebuild if needed, render, verify."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from easyedit import fetch
from easyedit.util import JOBS, log, probe, read_json

PY = sys.executable
ROOT = Path(__file__).resolve().parent


def render(title: str) -> bool:
    return subprocess.run([PY, "-m", "easyedit", title], cwd=ROOT, timeout=5400).returncode == 0


def fix_odyssey() -> None:
    job = JOBS / "the-odyssey"
    speech_dir = job / "downloads" / "speech"
    speech_dir.mkdir(parents=True, exist_ok=True)
    url = "https://www.youtube.com/watch?v=f_bKjZeJBBI"
    target = speech_dir / "f_bKjZeJBBI.mp4"
    if not target.exists():
        try:
            p = fetch.download(url, speech_dir)
        except Exception:
            p = fetch.download(url, speech_dir, cookies_browser="chrome")
        log(f"odyssey: speech re-downloaded -> {p.name}")
    files = [str(target.resolve())]
    q = read_json(job / "quote.json")
    q["files"] = files
    (job / "quote.json").write_text(json.dumps(q, indent=2, ensure_ascii=False), encoding="utf-8")
    src = read_json(job / "sources.json")
    src["speech"] = files
    (job / "sources.json").write_text(json.dumps(src, indent=2, ensure_ascii=False), encoding="utf-8")
    log("odyssey: quote + sources pinned to the dialogue trailer")


def main() -> None:
    order = ["Inception", "The Odyssey", "Resident Evil (2026)"]
    for title in order:
        slug = {"Inception": "inception", "The Odyssey": "the-odyssey",
                "Resident Evil (2026)": "resident-evil-2026"}[title]
        log(f"=== finishing {title} ===")
        try:
            if slug == "the-odyssey":
                fix_odyssey()
            if not render(title):
                log(f"{slug}: render command failed")
                continue
            out = JOBS / slug / f"{slug}.mp4"
            log(f"{slug}: DONE {out} ({probe(out)['duration']:.1f}s)" if out.exists() else f"{slug}: no mp4!")
        except Exception as e:
            log(f"{slug}: error {e}")
    log("finish pass complete")


if __name__ == "__main__":
    main()
