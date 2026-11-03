"""Доменная модель заметки.

Note — единственная сущность ядра. Все поля нормализуются и проверяются в
``__post_init__``, поэтому объект Note, существующий в памяти, всегда
соответствует бизнес-правилам приложения.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4

from app.core.exceptions import NoteValidationError

logger = logging.getLogger(__name__)

#: Поля, которые участвуют в сериализации в JSON.
NOTE_FIELDS: tuple[str, ...] = ("id", "title", "content", "timestamp", "attachments")
#: Поля, обязательные для записи в хранилище.
REQUIRED_FIELDS: tuple[str, ...] = ("id", "title")
#: Поля, которые разрешено менять через NoteManager.update_note.
UPDATABLE_FIELDS: tuple[str, ...] = ("title", "content", "attachments")


def generate_note_id() -> str:
    """Уникальный идентификатор заметки.

    По умолчанию UUID4: не требует чтения всего хранилища и безопасен при
    параллельном создании. Для автоинкремента достаточно заменить эту функцию.
    """
    return str(uuid4())


def current_timestamp() -> str:
    """Метка времени в ISO-8601 с часовым поясом, например 2026-11-03T10:15:00+03:00."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _validate_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NoteValidationError("поле 'id' должно быть непустой строкой")
    return value.strip()


def _validate_title(value: Any) -> str:
    if not isinstance(value, str):
        raise NoteValidationError("поле 'title' должно быть строкой")
    title = value.strip()
    if not title:
        raise NoteValidationError("поле 'title' не может быть пустым")
    return title


def _validate_content(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise NoteValidationError("поле 'content' должно быть строкой")
    return value


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NoteValidationError("поле 'timestamp' должно быть строкой в формате ISO-8601")
    timestamp = value.strip()
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise NoteValidationError(
            f"поле 'timestamp' не распознано как ISO-8601: {timestamp!r}"
        ) from exc
    return timestamp


def _validate_attachments(value: Any) -> list[str]:
    """Attachments — список непустых строк-путей. Дубликаты убираются, порядок сохраняется."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise NoteValidationError("поле 'attachments' должно быть списком строк-путей")

    attachments: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise NoteValidationError("каждый элемент 'attachments' — непустая строка с путём")
        path = item.strip()
        if path not in attachments:
            attachments.append(path)
    return attachments


@dataclass
class Note:
    """Заметка: id, заголовок, текст, метка времени и список вложений."""

    id: str
    title: str
    content: str = ""
    timestamp: str = field(default_factory=current_timestamp)
    attachments: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = _validate_id(self.id)
        self.title = _validate_title(self.title)
        self.content = _validate_content(self.content)
        self.timestamp = _validate_timestamp(self.timestamp)
        self.attachments = _validate_attachments(self.attachments)

    # ------------------------------------------------------------------ factory
    @classmethod
    def create(
        cls,
        title: str,
        content: str = "",
        attachments: Sequence[str] | None = None,
        *,
        note_id: str | None = None,
        timestamp: str | None = None,
    ) -> "Note":
        """Создаёт заметку, додумывая id и timestamp."""
        return cls(
            id=note_id or generate_note_id(),
            title=title,
            content=content,
            timestamp=timestamp or current_timestamp(),
            attachments=list(attachments or []),
        )

    @classmethod
    def from_dict(cls, raw: Any) -> "Note":
        """Восстанавливает заметку из объекта JSON.

        Неизвестные ключи игнорируются — это позволяет добавлять поля в модель
        без переписывания уже существующих файлов.
        """
        if not isinstance(raw, dict):
            raise NoteValidationError(f"запись заметки должна быть объектом, получен {type(raw).__name__}")

        missing = [key for key in REQUIRED_FIELDS if key not in raw]
        if missing:
            raise NoteValidationError(f"в записи нет обязательных полей: {', '.join(missing)}")

        unknown = sorted(set(raw) - set(NOTE_FIELDS))
        if unknown:
            logger.debug("игнорируем неизвестные поля записи: %s", ", ".join(unknown))

        return cls(
            id=raw["id"],
            title=raw["title"],
            content=raw.get("content", ""),
            timestamp=raw.get("timestamp") or current_timestamp(),
            attachments=list(raw.get("attachments") or []),
        )

    # -------------------------------------------------------------- serializing
    def to_dict(self) -> dict[str, Any]:
        """Возвращает копию данных, пригодную для json.dumps."""
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "timestamp": self.timestamp,
            "attachments": list(self.attachments),
        }

    # ---------------------------------------------------------------- immutability
    def clone(self) -> "Note":
        """Копия заметки: внешние изменения не затронут состояние менеджера."""
        return replace(self)

    def updated(self, changes: Mapping[str, Any]) -> "Note":
        """Новая заметка с заменой части полей; валидация проходит заново."""
        return replace(self, **dict(changes))

    def __str__(self) -> str:  # pragma: no cover - convenience for CLI output
        return f"[{self.id}] {self.title}"
