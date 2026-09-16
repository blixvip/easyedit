"""Install easyedit as a Claude Code skill, so `/easyedit <movie>` works in any session.

    python -m easyedit.skill install      # -> ~/.claude/skills/easyedit/SKILL.md
    python -m easyedit.skill uninstall
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .util import ROOT

TEMPLATE = ROOT / "skills" / "easyedit" / "SKILL.md"
TARGET = Path.home() / ".claude" / "skills" / "easyedit"


def python_command() -> str:
    """How to launch this same interpreter from a shell (it has the dependencies)."""
    exe = Path(sys.executable)
    return f'"{exe}"' if " " in str(exe) else str(exe)


def install() -> Path:
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{ROOT}}", ROOT.as_posix()).replace("{{PY}}", python_command())
    TARGET.mkdir(parents=True, exist_ok=True)
    out = TARGET / "SKILL.md"
    out.write_text(text, encoding="utf-8")
    return out


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        print(f"installed {install()}\nrestart Claude Code, then type: /easyedit Gladiator")
    elif cmd == "uninstall":
        shutil.rmtree(TARGET, ignore_errors=True)
        print(f"removed {TARGET}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
