#!/usr/bin/env python3
"""Thin wrapper so `python3 scripts/refresh.py` keeps working (used by the GitHub Action)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ceclib.refresh import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
