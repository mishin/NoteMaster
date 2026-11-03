"""Слой доступа к хранилищу (driver).

Ядро не знает ни о файлах, ни о JSON — оно работает с абстракцией
``BaseNotesDriver``. Реализация ``FileDriver`` хранит заметки в JSON-файле
формата «список объектов» и создаёт файл автоматически, если его нет.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.core.exceptions import StorageError

logger = logging.getLogger(__name__)

#: project_root/data — каталог данных по умолчанию.
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_STORAGE_PATH = DEFAULT_DATA_DIR / "notes.json"


class BaseNotesDriver(ABC):
    """Контракт хранилища: загрузить список записей и сохранить список записей."""

    @abstractmethod
    def load(self) -> list[dict[str, Any]]:
        """Возвращает все записи хранилища. Пустое хранилище — пустой список."""

    @abstractmethod
    def save(self, records: list[dict[str, Any]]) -> None:
        """Полностью перезаписывает хранилище набором записей."""


class FileDriver(BaseNotesDriver):
    """Файловый драйвер: notes.json + атомарная запись."""

    def __init__(
        self,
        path: str | os.PathLike[str] = DEFAULT_STORAGE_PATH,
        *,
        encoding: str = "utf-8",
        indent: int = 2,
    ) -> None:
        self._path = Path(path)
        self._encoding = encoding
        self._indent = indent

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------- public
    def load(self) -> list[dict[str, Any]]:
        self._ensure_parent_dir()

        if not self._path.exists():
            logger.info("Файл заметок отсутствует, создаём пустое хранилище: %s", self._path)
            self.save([])
            return []

        raw = self._read_text()
        if not raw.strip():
            logger.warning("Файл %s пуст, считаем хранилище пустым", self._path)
            return []

        records = self._parse_json(raw)
        if not isinstance(records, list):
            raise StorageError(
                f"в {self._path} должен лежать список заметок, найден {type(records).__name__}"
            )
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise StorageError(
                    f"запись №{index} в {self._path} должна быть JSON-объектом"
                )

        logger.debug("Загружено %d заметок из %s", len(records), self._path)
        return records

    def save(self, records: list[dict[str, Any]]) -> None:
        self._ensure_parent_dir()
        payload = self._serialize(records)

        tmp_path: Path | None = None
        try:
            # Пишем во временный файл рядом и подменяем одним атомарным rename:
            # при сбое чтения/записи notes.json остаётся в целостном состоянии.
            handle, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent),
                prefix=f"{self._path.name}.",
                suffix=".tmp",
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(handle, "w", encoding=self._encoding) as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp_path, self._path)
            tmp_path = None
        except OSError as exc:
            raise StorageError(f"не удалось записать {self._path}: {exc}") from exc
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:  # pragma: no cover - временный файл не критичен
                    logger.debug("Не удалён временный файл %s", tmp_path)

        logger.debug("Сохранено %d заметок в %s", len(records), self._path)

    # ------------------------------------------------------------------ helpers
    def _serialize(self, records: list[dict[str, Any]]) -> str:
        try:
            return json.dumps(records, ensure_ascii=False, indent=self._indent)
        except (TypeError, ValueError) as exc:
            raise StorageError(f"данные заметок не сериализуются в JSON: {exc}") from exc

    def _read_text(self) -> str:
        try:
            return self._path.read_text(encoding=self._encoding)
        except UnicodeDecodeError as exc:
            raise StorageError(f"файл {self._path} не является текстом в кодировке {self._encoding}") from exc
        except OSError as exc:
            raise StorageError(f"не удалось прочитать {self._path}: {exc}") from exc

    @staticmethod
    def _parse_json(raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StorageError(f"битый JSON в хранилище: {exc} (строка {exc.lineno}, позиция {exc.colno})") from exc

    def _ensure_parent_dir(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"не удалось создать каталог {self._path.parent}: {exc}") from exc
