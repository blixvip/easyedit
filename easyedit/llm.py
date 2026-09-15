"""LLM access with no API keys: Claude Code OAuth token -> codex exec -> none.

Every caller must supply a deterministic fallback, so a missing/expired
login degrades quality instead of breaking the pipeline.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .util import log

CLAUDE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
CLAUDE_MODELS = [os.environ.get("EASYEDIT_CLAUDE_MODEL", "claude-sonnet-5"), "claude-sonnet-4-6"]


def _claude_token() -> str | None:
    creds = Path.home() / ".claude" / ".credentials.json"
    try:
        return json.loads(creds.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    except Exception:
        return None


def _claude(prompt: str, system: str) -> str | None:
    token = _claude_token()
    if not token:
        return None
    for model in dict.fromkeys(CLAUDE_MODELS):
        body = {
            "model": model,
            "max_tokens": 4000,
            "system": f"{CLAUDE_IDENTITY}\n\n{system}",
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={
                "authorization": f"Bearer {token}",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "oauth-2025-04-20",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            return "".join(b.get("text", "") for b in data.get("content", []))
        except urllib.error.HTTPError as e:
            log(f"claude {model}: HTTP {e.code} {e.read()[:160]!r}")
        except Exception as e:  # network, timeout
            log(f"claude {model}: {e}")
    return None


def _codex(prompt: str, system: str) -> str | None:
    exe = shutil.which("codex")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "last.txt"
        try:
            subprocess.run(
                [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                 "-o", str(out), "-"],
                input=f"{system}\n\nDo not run any tools. Answer directly.\n\n{prompt}",
                text=True, encoding="utf-8", capture_output=True, timeout=300, cwd=tmp, check=True,
            )
            return out.read_text(encoding="utf-8")
        except Exception as e:
            log(f"codex: {e}")
            return None


def extract_json(text: str):
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in reply")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def ask_json(prompt: str, system: str, provider: str = "auto"):
    """Return parsed JSON from the first provider that answers validly, else None."""
    order = {"auto": [_claude, _codex], "claude": [_claude], "codex": [_codex], "none": []}[provider]
    for fn in order:
        reply = fn(prompt, system)
        if not reply:
            continue
        try:
            return extract_json(reply)
        except Exception as e:
            log(f"{fn.__name__[1:]}: unparseable reply ({e})")
    return None
