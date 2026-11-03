"""Тесты ядра NoteMaster.

Добавляем корень проекта в sys.path, чтобы тесты запускались из любой
директории и любым раннером: unittest, pytest, IDE.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
