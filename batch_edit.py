"""Batch driver: run easyedit for several movies sequentially with auto-QA.

    python batch_edit.py titles.txt

Per movie: pre-plan (optional manual plan from plans/), build --no-render,
QA report check (quote source, shot flags), auto-apply curate draft when clean,
rebuild, render, disk cleanup. One movie at a time; every stage is cached, so a
re-run skips what already succeeded.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from easyedit.util import JOBS, log, read_json, slugify, write_json  # noqa: E402

PY = sys.executable
MIN_DISK_GB = 8.0  # sequential jobs stay under ~2 GB transient; report if the machine runs tighter


def disk_gb() -> float:
    return shutil.disk_usage("C:").free / 1e9


def run(args: list[str], timeout: int) -> bool:
    p = subprocess.run([PY, *args], cwd=ROOT, timeout=timeout)
    return p.returncode == 0


def free_temp(job: Path) -> None:
    for sub in ("downloads", "work/segments", "work/sections"):
        d = job / sub
        if d.exists():
            for f in d.iterdir():
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink(missing_ok=True)


def disk_check(slug: str) -> bool:
    free = disk_gb()
    if free >= MIN_DISK_GB:
        return True
    job = JOBS / slug
    if job.exists():
        log(f"disk: {free:.1f} GB free, clearing temp for {slug}")
        free_temp(job)
    log(f"disk: {disk_gb():.1f} GB free after cleanup")
    return disk_gb() >= MIN_DISK_GB - 2.0


def qa_and_curate(slug: str) -> str:
    """Run the text QA report; auto-apply the curate draft if the pool is clean.

    Returns 'ok', 'curated' (draft applied), or 'review' (needs a human/agent look).
    """
    job = JOBS / slug
    if not run(["-m", "easyedit.sheet", slug, "--report"], timeout=600):
        return "review"
    rep = (job / "qa" / "report.txt").read_text(encoding="utf-8", errors="replace")
    heuristic = "!! AI writer unavailable" in rep  # quote needs eyes, but render anyway
    pool = [ln for ln in rep.splitlines() if ln[:4].strip().isdigit()]
    bad = sum(1 for ln in pool if " title-card?" in ln or " repeat" in ln)
    if not pool or bad > max(2, len(pool) // 6):
        return "review"
    if heuristic:
        return "render-flag"
    sug = job / "qa" / "suggest-curate.json"
    if sug.exists() and not (job / "curate.json").exists():
        shutil.copy2(sug, job / "curate.json")
        log(f"{slug}: applied suggested curate.json")
        return "curated"
    return "ok"


def process(title: str, plans_dir: Path) -> None:
    slug = slugify(title)
    log(f"=== {title} -> {slug} ===")
    if not disk_check(slug):
        log(f"{slug}: SKIPPED, disk too low")
        return
    plan_file = JOBS / slug / "plan.json"
    manual = plans_dir / f"{slug}.json"
    if not plan_file.exists() and manual.exists():  # plan.json is hand-written for new/2026 films
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manual, plan_file)
        log(f"{slug}: using manual plan")
    ok = run(["-m", "easyedit", title, "--no-render"], timeout=3600)
    if not ok:
        log(f"{slug}: build failed, marking for review")
        (JOBS / slug / "NEEDS_REVIEW.txt").write_text("build failed\n", encoding="utf-8")
        return
    status = qa_and_curate(slug)
    if status == "review":
        log(f"{slug}: QA flags need review before render, marking")
        (JOBS / slug / "NEEDS_REVIEW.txt").write_text("qa review needed (see qa/report.txt)\n", encoding="utf-8")
        return
    if status == "render-flag":
        log(f"{slug}: heuristic quote - rendering anyway, VERIFY quote before shipping")
        (JOBS / slug / "VERIFY_QUOTE.txt").write_text("heuristic quote; check qa/report.txt\n", encoding="utf-8")
        ok = run(["-m", "easyedit", title, "--no-render"], timeout=3600)
        if not ok:
            log(f"{slug}: rebuild failed, marking for review")
            (JOBS / slug / "NEEDS_REVIEW.txt").write_text("rebuild after curate failed\n", encoding="utf-8")
            return
    if not run(["-m", "easyedit", title], timeout=5400):
        log(f"{slug}: render failed, marking for review")
        (JOBS / slug / "NEEDS_REVIEW.txt").write_text("render failed\n", encoding="utf-8")
        return
    run(["-m", "easyedit.sheet", slug, "--final"], timeout=900)
    out = JOBS / slug / f"{slug}.mp4"
    log(f"{slug}: DONE -> {out}" if out.exists() else f"{slug}: render done but no mp4?")
    free_temp(JOBS / slug)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    titles = [ln.strip() for ln in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if ln.strip()]
    plans_dir = ROOT / "plans"
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    for t in titles:
        if only and slugify(t) != slugify(only):
            continue
        try:
            process(t, plans_dir)
        except Exception as e:
            log(f"{t}: crashed: {e}")
            (JOBS / slugify(t) / "NEEDS_REVIEW.txt").parent.mkdir(parents=True, exist_ok=True)
            (JOBS / slugify(t) / "NEEDS_REVIEW.txt").write_text(f"crash: {e}\n", encoding="utf-8")
    log("batch complete")


if __name__ == "__main__":
    main()
