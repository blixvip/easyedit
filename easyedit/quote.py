"""Pick the quotable passage, split it into caption lines, tag emphasis words."""
from __future__ import annotations

import re

from .llm import ask_json
from .util import log

MIN_SPAN, MAX_SPAN = 9.0, 24.0
ROLES = ("positive", "negative", "gold", "cool")

SYSTEM = "You are a film editor choosing captioned lines for a fan edit. Reply with JSON only."

PROMPT = """Film: {title}. Scene we want: {scene}

Below are transcribed words from {n} candidate clip(s), formatted `index:word` (per clip, with
[t=seconds] markers every few words). Pick ONE continuous passage from ONE clip that is the most
iconic, quotable and emotionally punchy. It must be {lo:.0f}-{hi:.0f} seconds long, start at the
beginning of a sentence and end at the end of a sentence. It must be CONTINUOUS speech: never span a
silence longer than 1.5 seconds (check the [t=] markers - a jump means crowd noise or music, not
talking). Skip narrator/trailer voiceover, YouTube intros and garbled text.

Then pick emphasis words inside the passage (about 1 per 4 words, never filler words) and give each
a color role: positive (wealth, winning, love, success), negative (pain, poverty, death, failure),
gold (power, luxury, the single biggest word of a line), cool (names, places, objects).

Return JSON: {{"clip": 0, "start": first_word_index, "end": last_word_index,
"emphasis": {{"word_index": "role"}}, "reason": "short"}}

{listing}"""

FILLER = set("a an the and or but so to of in on at is am are was were be been i you he she it we they "
             "my your his her its our their this that with for as if then just like um uh oh".split())
LEXICON = {
    "positive": "rich money win winner winning love success dream dreams free freedom alive million "
                "millions billion best greatest king queen power hope",
    "negative": "poor poverty death dead die dying kill pain lose loser losing never nothing fear hate "
                "broke fail failure alone war blood",
    "gold": "gold golden god legend everything forever always every fucking diamond crown",
}


def _listing(clips: list[list[dict]]) -> str:
    out = []
    for c, words in enumerate(clips):
        parts = [f"--- clip {c} ---"]
        for i, w in enumerate(words):
            if i % 8 == 0:
                parts.append(f"[t={w['start']:.1f}]")
            parts.append(f"{i}:{w['text']}")
        out.append(" ".join(parts))
    return "\n".join(out)


def _sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?…]['\"”’)]*$", text))


def _heuristic(clips: list[list[dict]]):
    best = None
    for c, words in enumerate(clips):
        starts = [i for i in range(len(words))
                  if i == 0 or _sentence_end(words[i - 1]["text"]) or words[i]["start"] - words[i - 1]["end"] > 0.5]
        for s in starts:
            for e in range(s + 3, len(words)):
                span = words[e]["end"] - words[s]["start"]
                if span > MAX_SPAN:
                    break
                if span < MIN_SPAN or not _sentence_end(words[e]["text"]):
                    continue
                seg = words[s:e + 1]
                if max(b["start"] - a["end"] for a, b in zip(seg, seg[1:])) > MAX_GAP:
                    continue
                gaps = sum(max(0.0, b["start"] - a["end"] - 0.6) for a, b in zip(seg, seg[1:]))
                rate = len(seg) / span
                conf = sum(w["prob"] for w in seg) / len(seg)
                score = conf * 2 - abs(rate - 2.6) * 0.4 - gaps * 0.8 + min(span, 18) / 18
                if not best or score > best[0]:
                    best = (score, c, s, e)
    if not best:  # no sentence punctuation at all: take the densest window
        for c, words in enumerate(clips):
            for s in range(len(words)):
                e = s
                while e + 1 < len(words) and words[e + 1]["end"] - words[s]["start"] <= 16:
                    e += 1
                if e > s and (not best or e - s > best[3] - best[2]):
                    best = (0, c, s, e)
    if not best:
        return None
    return {"clip": best[1], "start": best[2], "end": best[3], "emphasis": {}}


def _clean(text: str) -> str:
    return re.sub(r"^[^\w$’']+|[^\w%’']+$", "", text).upper()


def _auto_emphasis(words: list[dict]) -> dict:
    lookup = {w: role for role, s in LEXICON.items() for w in s.split()}
    out = {}
    for i, w in enumerate(words):
        t = _clean(w["text"]).lower()
        if re.search(r"\d|\$", t):
            out[i] = "positive"
        elif t in lookup:
            out[i] = lookup[t]
    return out


def lines(words: list[dict], max_words: int = 6, max_chars: int = 26) -> list[list[int]]:
    """Group word indices into caption lines at pauses, punctuation and length limits."""
    groups, cur = [], []
    for i, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            chars = sum(len(words[j]["text"]) + 1 for j in cur) + len(w["text"])
            pause = w["start"] - prev["end"]
            if (len(cur) >= max_words or chars > max_chars or pause > 0.45
                    or (re.search(r"[.!?,;:—]$", prev["text"]) and len(cur) >= 2)):
                groups.append(cur)
                cur = []
        cur.append(i)
    if cur:
        if groups and len(cur) == 1 and len(groups[-1]) < max_words:
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    return groups


MAX_GAP = 1.5  # dead air inside a passage; longer than this and the captions visibly stall


def _voiced(words: list[dict], s: int, e: int) -> float:
    return sum(w["end"] - w["start"] for w in words[s:e + 1])


def _extend(words: list[dict], s: int, e: int) -> tuple[int, int]:
    """Grow a too-short pick by whole sentences (forward first, then backward), never over dead air."""
    def span(a, b):
        return words[b]["end"] - words[a]["start"]
    while span(s, e) < MIN_SPAN:
        nxt = next((j for j in range(e + 1, len(words)) if _sentence_end(words[j]["text"])
                    or j == len(words) - 1), None)
        if (nxt is not None and span(s, nxt) <= MAX_SPAN
                and words[e + 1]["start"] - words[e]["end"] <= MAX_GAP):
            e = nxt
            continue
        prv = next((j for j in range(s - 1, -1, -1) if j == 0 or _sentence_end(words[j - 1]["text"])), None)
        if (prv is not None and span(prv, e) <= MAX_SPAN
                and words[s]["start"] - words[s - 1]["end"] <= MAX_GAP):
            s = prv
            continue
        break
    return s, e


def _tighten(words: list[dict], s: int, e: int) -> tuple[int, int]:
    """Drop dead air: keep the best run of words with no gap longer than MAX_GAP."""
    runs, start = [], s
    for i in range(s, e):
        if words[i + 1]["start"] - words[i]["end"] > MAX_GAP:
            runs.append((start, i))
            start = i + 1
    runs.append((start, e))
    if len(runs) == 1:
        return s, e
    def score(r):
        a, b = r
        span = words[b]["end"] - words[a]["start"]
        if span > MAX_SPAN:  # too long, but still better than a stalled window
            return _voiced(words, a, b) - (span - MAX_SPAN)
        return _voiced(words, a, b) - max(0.0, MIN_SPAN - span) * 0.8
    best = max(runs, key=score)
    return best


def choose(clips: list[list[dict]], plan: dict, provider: str) -> dict:
    listing = _listing(clips)
    pick = ask_json(PROMPT.format(title=plan["title"], scene=plan["speech_scene"], n=len(clips),
                                  lo=MIN_SPAN, hi=MAX_SPAN, listing=listing), SYSTEM, provider)
    ok = False
    if isinstance(pick, dict):
        try:
            c, s, e = int(pick["clip"]), int(pick["start"]), int(pick["end"])
            span = clips[c][e]["end"] - clips[c][s]["start"]
            ok = 0 <= s < e and 4.0 <= span <= MAX_SPAN + 6
        except (KeyError, ValueError, IndexError, TypeError):
            ok = False
    if not ok:
        log("quote: LLM pick unusable, using heuristic")
        pick = _heuristic(clips)
        if not pick:
            raise RuntimeError("no speech found in the speech clip(s)")
        source = "heuristic"
    else:
        source = "llm"
    c, s, e = int(pick["clip"]), int(pick["start"]), int(pick["end"])
    s, e = _tighten(clips[c], s, e)
    s, e = _extend(clips[c], s, e)
    words = clips[c][s:e + 1]
    emphasis = {}
    for k, role in (pick.get("emphasis") or {}).items():
        try:
            idx = int(k) - s
        except ValueError:
            continue
        if 0 <= idx < len(words) and role in ROLES and _clean(words[idx]["text"]).lower() not in FILLER:
            emphasis[idx] = role
    if not emphasis:
        emphasis = _auto_emphasis(words)
    caption_words = [{"text": _clean(w["text"]), "start": w["start"], "end": w["end"],
                      "role": emphasis.get(i)} for i, w in enumerate(words)]
    caption_words = [w for w in caption_words if w["text"]]
    groups = lines(caption_words)
    result = {
        "clip": c, "source": source, "reason": pick.get("reason", ""),
        "start": words[0]["start"], "end": words[-1]["end"],
        "lines": [[caption_words[i] for i in g] for g in groups],
    }
    log(f"quote ({source}): clip {c}, {result['start']:.2f}-{result['end']:.2f}s, "
        f"{len(caption_words)} words in {len(groups)} lines")
    return result
