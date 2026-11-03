"""Точка входа NoteMaster.

CLI реализован в :mod:`app.cli`; модуль оставлен для совместимости с
командами вида ``python app/main.py ...`` и ``python -m app.main``.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
