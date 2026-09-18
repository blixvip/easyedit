"""Pick the quotable passage, split it into caption lines, tag emphasis words."""
from __future__ import annotations

import re

from .llm import ask_json
from .util import log

MIN_SPAN, MAX_SPAN = 9.0, 24.0
ROLES = ("positive", "negative", "gold", "cool")

SYSTEM = "You are a film editor choosing captioned lines for a fan edit. Reply with JSON only."

PROMPT = """Film: {title}. Scene we want: {scene}

Here are candidate passages transcribed from the clip(s). Pick the ONE that is the film's most
iconic, quotable and emotionally punchy moment. Reject anything that reads like narrator voiceover,
YouTube commentary or an interview. The passage is used verbatim as on-screen captions.

Then mark up to 8 emphasis words from THAT passage: positive (wealth, winning, love, success),
negative (pain, poverty, death, failure), gold (power, luxury, the biggest word of a line),
cool (names, places, objects). Never filler words.

Return JSON: {{"pick": <candidate number>, "emphasis": [["word", "role"], ...], "reason": "short"}}

{candidates}"""

FILLER = set("a an the and or but so to of in on at is am are was were be been i you he she it we they "
             "my your his her its our their this that with for as if then just like um uh oh".split())
LEXICON = {
    "positive": "rich money win winner winning love success dream dreams free freedom alive million "
                "millions billion best greatest king queen power hope",
    "negative": "poor poverty death dead die dying kill pain lose loser losing never nothing fear hate "
                "broke fail failure alone war blood",
    "gold": "gold golden god legend everything forever always every fucking diamond crown",
}

MAX_GAP = 1.5  # dead air inside a passage; longer than this and the captions visibly stall


def _sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?…]['\"\u201d\u2019)]*$", text))


def _text(words: list[dict], s: int, e: int) -> str:
    return " ".join(w["text"].strip(",'") for w in words[s:e + 1])


def _candidates(clips: list[list[dict]], limit: int = 12) -> list[dict]:
    """Every valid captionable span (9-24s, sentence-aligned, no dead air), scored.

    The LLM then only has to choose between a dozen pre-validated options instead of
    counting word indexes - fewer tokens in, and no more picks that fail validation.
    """
    scored = []
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
                scored.append({"clip": c, "start": s, "end": e, "score": score})
    # drop candidates that overlap a better-scoring one, keep the top few
    scored.sort(key=lambda c: -c["score"])
    kept = []
    for c in scored:
        if len(kept) >= limit:
            break
        if any(c["clip"] == k["clip"] and c["start"] < k["end"] and k["start"] < c["end"] for k in kept):
            continue
        kept.append(c)
    return kept


def _format(cands: list[dict], clips: list[list[dict]]) -> str:
    out = []
    for n, c in enumerate(cands):
        w = clips[c["clip"]]
        dur = w[c["end"]]["end"] - w[c["start"]]["start"]
        out.append(f"{n}. [{dur:.0f}s] {_text(w, c['start'], c['end'])}")
    return "\n".join(out)


def _densest(clips: list[list[dict]]) -> dict | None:
    """Last-resort pick when the clip has no sentence punctuation at all."""
    best = None
    for c, words in enumerate(clips):
        for s in range(len(words)):
            e = s
            while e + 1 < len(words) and words[e + 1]["end"] - words[s]["start"] <= 16:
                e += 1
            if e > s and (not best or e - s > best["end"] - best["start"]):
                best = {"clip": c, "start": s, "end": e}
    return best


def _emphasis_from_pairs(words: list[dict], s: int, e: int, pairs) -> dict:
    """Map (word, role) emphasis pairs from the LLM onto indexes inside the picked span."""
    if isinstance(pairs, dict):  # accept {"word": "role"} too
        pairs = list(pairs.items())
    out = {}
    for item in pairs or []:
        try:
            if isinstance(item, dict):
                word, role = str(item.get("word", "")).strip().lower(), str(item.get("role", ""))
            else:
                word, role = str(item[0]).strip().lower(), str(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if role not in ROLES or word in FILLER:
            continue
        for i in range(s, e + 1):
            if i - s not in out and _clean(words[i]["text"]).lower() == word:
                out[i - s] = role
                break
    return out


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


def _voiced(words: list[dict], s: int, e: int) -> float:
    return sum(w["end"] - w["start"] for w in words[s:e + 1])


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
    cands = _candidates(clips)
    pick, emphasis_pairs, source, reason = None, None, "heuristic", ""
    if cands:
        reply = ask_json(PROMPT.format(title=plan["title"], scene=plan["speech_scene"],
                                       candidates=_format(cands, clips)), SYSTEM, provider)
        if isinstance(reply, dict):
            try:
                n = int(reply.get("pick"))
            except (TypeError, ValueError):
                n = -1
            if 0 <= n < len(cands):
                pick, source = cands[n], "llm"
                reason = str(reply.get("reason", ""))[:100]
                emphasis_pairs = reply.get("emphasis")
            else:
                log("quote: LLM pick out of range, using best candidate")
    if pick is None:
        pick = cands[0] if cands else _densest(clips)
        if not pick:
            raise RuntimeError("no speech found in the speech clip(s)")
        reason = "best-scored candidate" if cands else "densest window"
    c, s, e = pick["clip"], pick["start"], pick["end"]
    s, e = _tighten(clips[c], s, e)
    words = clips[c][s:e + 1]
    emphasis = _emphasis_from_pairs(clips[c], s, e, emphasis_pairs)
    if not emphasis:
        emphasis = _auto_emphasis(words)
    caption_words = [{"text": _clean(w["text"]), "start": w["start"], "end": w["end"],
                      "role": emphasis.get(i)} for i, w in enumerate(words)]
    caption_words = [w for w in caption_words if w["text"]]
    groups = lines(caption_words)
    result = {
        "clip": c, "source": source, "reason": reason,
        "start": words[0]["start"], "end": words[-1]["end"],
        "lines": [[caption_words[i] for i in g] for g in groups],
    }
    log(f"quote ({source}): clip {c}, {result['start']:.2f}-{result['end']:.2f}s, "
        f"{len(caption_words)} words in {len(groups)} lines")
    return result
