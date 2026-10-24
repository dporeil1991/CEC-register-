#!/usr/bin/env python3
"""Build the cec-register-check skill from this repo: code, a data snapshot and SKILL.md.

  python3 scripts/build_skill.py            # writes dist/cec-register-check.skill

The .skill file is a zip of the skill folder. Install it in Claude (Settings -> Capabilities
-> Skills -> upload), or click "Save skill" on the file card when Claude sends it to you.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "cec-register-check"
DATA_FILES = ["inverters.json", "modules.json", "batteries.json", "meta.json", "aliases.json"]


def build(out_dir: Path) -> Path:
    stage = Path(tempfile.mkdtemp()) / NAME
    stage.mkdir(parents=True)
    shutil.copy(ROOT / "skill" / "SKILL.md", stage / "SKILL.md")
    shutil.copy(ROOT / "cec.py", stage / "cec.py")
    shutil.copytree(ROOT / "ceclib", stage / "ceclib", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (stage / "data").mkdir()
    for name in DATA_FILES:
        shutil.copy(ROOT / "data" / name, stage / "data" / name)
    meta = json.loads((ROOT / "data" / "meta.json").read_text())
    pulled = min(m["fetched_at"][:10] for k, m in meta.items() if not k.startswith("_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{NAME}.skill"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(stage.parent))
    shutil.rmtree(stage.parent)
    print(f"{target} ({target.stat().st_size // 1024} KB), register pulled {pulled}")
    return target


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
