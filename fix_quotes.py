"""Hand-fix quotes for jobs where the transcript needs a human-picked span."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from easyedit import quote as Q
from easyedit.util import JOBS, log, read_json


def build(slug: str, words_file: str, start_text: str, end_text: str, roles: dict) -> None:
    words = read_json(JOBS / slug / "work" / words_file)
    text = " ".join(w["text"].lower() for w in words)
    s = text.find(start_text.lower())
    if s < 0:
        raise SystemExit(f"start not found: {start_text!r}")
    si = text[:s].count(" ")
    tail = text[s:]
    e = tail.find(end_text.lower())
    if e < 0:
        raise SystemExit(f"end not found: {end_text!r}")
    ei = si + tail[:e].count(" ") + len(end_text.split()) - 1
    s, e = Q._tighten(words, si, ei)
    span = words[e]["end"] - words[s]["start"]
    if not (4.0 <= span <= Q.MAX_SPAN + 4):
        log(f"{slug}: WARNING span {span:.1f}s outside comfort range, continuing")
    seg = words[s:e + 1]
    emphasis = {}
    for i, w in enumerate(seg):
        key = Q._clean(w["text"]).lower()
        if key in roles:
            emphasis[i] = roles[key]
    caption_words = [{"text": Q._clean(w["text"]), "start": w["start"], "end": w["end"],
                      "role": emphasis.get(i)} for i, w in enumerate(seg)]
    caption_words = [w for w in caption_words if w["text"]]
    groups = Q.lines(caption_words)
    result = {
        "clip": 0, "source": "manual", "reason": "hand-picked iconic span",
        "start": seg[0]["start"], "end": seg[-1]["end"],
        "lines": [[caption_words[i] for i in g] for g in groups],
    }
    (JOBS / slug / "quote.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"{slug}: quote.json rewritten {result['start']:.1f}-{result['end']:.1f}s "
        f"({len(caption_words)} words, {len(groups)} lines, {len(emphasis)} emphasized)")


if __name__ == "__main__":
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for job in cfg:
        build(**job)
