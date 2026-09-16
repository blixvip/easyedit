"""Setup check: which AI accounts are connected and which tools are installed.

    python -m easyedit.doctor           # human-readable checklist
    python -m easyedit.doctor --json    # for bots and the web UI

The web UI's Connect panel and any agent installing easyedit use the same report.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .util import ROOT

REPO = "https://github.com/blixvip/easyedit"


def _run(argv: list[str], timeout: float = 20) -> tuple[int, str]:
    exe = shutil.which(argv[0])
    if not exe:
        return 127, ""
    try:
        p = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def claude_account() -> dict:
    """easyedit calls Claude with the Claude Code login, read from ~/.claude/.credentials.json."""
    info = {"id": "claude", "name": "Claude", "cli": bool(shutil.which("claude")),
            "connected": False, "detail": "", "login": "claude auth login"}
    creds = Path.home() / ".claude" / ".credentials.json"
    oauth = None
    try:
        oauth = json.loads(creds.read_text(encoding="utf-8")).get("claudeAiOauth")
    except Exception:
        pass
    if oauth and oauth.get("accessToken"):
        expired = oauth.get("expiresAt") and oauth["expiresAt"] / 1000 < time.time()
        plan = oauth.get("subscriptionType")
        if expired:
            info["detail"] = "Login expired. Open Claude Code once to refresh it, or connect again."
        else:
            info["connected"] = True
            info["detail"] = f"Claude {plan.title()} plan" if plan else "Signed in"
        return info
    if not info["cli"]:
        info["detail"] = "Claude Code isn't installed."
        info["install"] = "npm install -g @anthropic-ai/claude-code"
        return info
    code, out = _run(["claude", "auth", "status", "--json"])
    try:
        status = json.loads(out)
    except Exception:
        status = {}
    if status.get("loggedIn"):
        # signed in, but the token lives in the OS keychain (macOS) where easyedit can't read it
        info["detail"] = "Signed in, but the login is stored in the system keychain. Use Codex, or run without an AI writer."
    else:
        info["detail"] = "Not signed in."
    return info


def codex_account() -> dict:
    info = {"id": "codex", "name": "Codex (ChatGPT)", "cli": bool(shutil.which("codex")),
            "connected": False, "detail": "", "login": "codex login"}
    if not info["cli"]:
        info["detail"] = "Codex CLI isn't installed."
        info["install"] = "npm install -g @openai/codex"
        return info
    code, out = _run(["codex", "login", "status"])
    if code == 0 and "logged in" in out.lower():
        info["connected"] = True
        info["detail"] = out.splitlines()[0]
    else:
        info["detail"] = "Not signed in."
    return info


def tools() -> list[dict]:
    out = []

    def add(name, ok, detail, fix=""):
        out.append({"name": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    py_ok = sys.version_info >= (3, 10)
    add("Python", py_ok, sys.version.split()[0], "Install Python 3.10 or newer")
    add("FFmpeg", shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "on PATH" if shutil.which("ffmpeg") else "missing", "Install FFmpeg and add it to PATH")
    code, ver = _run(["node", "--version"])
    m = re.match(r"v(\d+)", ver or "")
    add("Node.js 22+", m and int(m.group(1)) >= 22, ver or "missing", "Install Node.js 22 or newer")
    add("HyperFrames", (ROOT / "node_modules" / "hyperframes").exists(),
        "installed" if (ROOT / "node_modules" / "hyperframes").exists() else "missing", "npm install")
    missing = [mod for mod in ("faster_whisper", "cv2", "numpy", "yt_dlp") if not importlib.util.find_spec(mod)]
    add("Python packages", not missing, "installed" if not missing else "missing " + ", ".join(missing),
        f"{Path(sys.executable).name} -m pip install -r requirements.txt")
    gpu = "CPU only (slower transcription)"
    if importlib.util.find_spec("ctranslate2"):
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                gpu = "NVIDIA GPU"
        except Exception:
            pass
    out.append({"name": "GPU", "ok": True, "detail": gpu, "fix": "", "optional": True})
    return out


def report() -> dict:
    accounts = [claude_account(), codex_account()]
    checks = tools()
    return {
        "repo": REPO,
        "python": sys.executable,
        "accounts": accounts,
        "tools": checks,
        "writer": next((a["name"] for a in accounts if a["connected"]), None),
        "ready": all(t["ok"] for t in checks),
    }


def main() -> None:
    r = report()
    if "--json" in sys.argv:
        print(json.dumps(r, indent=2))
        return
    for stream in (sys.stdout,):
        stream.reconfigure(encoding="utf-8", errors="replace")
    print("AI accounts (one is enough; without one easyedit falls back to heuristics)")
    for a in r["accounts"]:
        mark = "ok " if a["connected"] else "-- "
        hint = "" if a["connected"] else f"   -> {a.get('install') or a['login']}"
        print(f"  {mark}{a['name']}: {a['detail']}{hint}")
    print("Tools")
    for t in r["tools"]:
        print(f"  {'ok ' if t['ok'] else 'XX '}{t['name']}: {t['detail']}" + (f"   -> {t['fix']}" if t["fix"] else ""))
    print("\nready" if r["ready"] else "\nnot ready: fix the XX lines above")
    sys.exit(0 if r["ready"] else 1)


if __name__ == "__main__":
    main()
