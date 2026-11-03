"""NoteManager — инкапсулированная работа с заметками.

Менеджер знает только абстракцию ``BaseNotesDriver`` и бизнес-правила:
    * нельзя редактировать/удалять, когда хранилище пусто;
    * изменение возможно только после получения заметки по id;
    * обновление частичное — меняются лишь переданные поля;
    * title не пустой, attachments — список строк (правила модели Note).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.core.exceptions import (
    EmptyStorageError,
    NoteNotFoundError,
    NoteValidationError,
    StorageError,
)
from app.core.models import UPDATABLE_FIELDS, Note
from app.driver.file_driver import DEFAULT_STORAGE_PATH, BaseNotesDriver, FileDriver

logger = logging.getLogger(__name__)


class NoteManager:
    """Точка входа в ядро: CRUD-операции над заметками поверх драйвера."""

    def __init__(
        self,
        driver: BaseNotesDriver | None = None,
        *,
        storage_path: str | os.PathLike[str] | None = None,
    ) -> None:
        if driver is not None and storage_path is not None:
            logger.debug("storage_path игнорируется: передан готовый driver")

        self._driver: BaseNotesDriver = driver or FileDriver(storage_path or DEFAULT_STORAGE_PATH)
        self._notes: list[Note] = []
        self._loaded: bool = False

    # ------------------------------------------------------------------ свойства
    @property
    def driver(self) -> BaseNotesDriver:
        """Используемый драйвер хранилища."""
        return self._driver

    @property
    def storage_path(self) -> Path | None:
        """Путь к файлу, если драйвер файловый, иначе None."""
        return getattr(self._driver, "path", None)

    # ------------------------------------------------------------------ загрузка
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._notes = self._load_notes()
        self._loaded = True

    def _load_notes(self) -> list[Note]:
        records = self._driver.load()
        notes: list[Note] = []
        for index, record in enumerate(records):
            try:
                notes.append(Note.from_dict(record))
            except NoteValidationError as exc:
                logger.error("Пропущена некорректная запись №%d: %s", index, exc)
                raise StorageError(f"повреждённая запись №{index} в хранилище: {exc}") from exc
        return notes

    def reload(self) -> list[Note]:
        """Заново читает хранилище: данные могли изменить извне."""
        self._notes = self._load_notes()
        self._loaded = True
        logger.info("Хранилище перезагружено, заметок: %d", len(self._notes))
        return self.get_all_notes()

    # ---------------------------------------------------------------- CRUD-операции
    def create_note(
        self,
        title: str,
        content: str = "",
        attachments: Sequence[str] | None = None,
    ) -> Note:
        """Создаёт заметку и сохраняет её в хранилище."""
        self._ensure_loaded()
        note = Note.create(title=title, content=content, attachments=attachments)

        snapshot = list(self._notes)
        self._notes.append(note)
        self._commit(snapshot)

        logger.info("Создана заметка id=%s title=%r", note.id, note.title)
        return note.clone()

    def get_all_notes(self) -> list[Note]:
        """Возвращает копии всех заметок в порядке хранения."""
        self._ensure_loaded()
        return [note.clone() for note in self._notes]

    def get_note_by_id(self, note_id: str) -> Note | None:
        """Возвращает копию заметки по id либо None — основа для точечного обновления."""
        self._ensure_loaded()
        note = self._find(note_id)
        return note.clone() if note is not None else None

    def update_note(self, note_id: str, **fields: Any) -> Note:
        """Частично обновляет заметку: изменяются только переданные поля.

        Разрешённые поля: title, content, attachments.
        """
        self._ensure_loaded()
        changes = self._validate_changes(fields)
        note = self._require_note(note_id, action="обновление")

        index = self._index_of(note.id)
        try:
            updated = note.updated(changes)
        except NoteValidationError as exc:
            logger.error("Отклонено обновление заметки id=%s: %s", note_id, exc)
            raise

        snapshot = list(self._notes)
        self._notes[index] = updated
        self._commit(snapshot)

        logger.info("Обновлена заметка id=%s поля=%s", updated.id, sorted(changes))
        return updated.clone()

    def delete_note(self, note_id: str) -> Note:
        """Удаляет заметку по id и сохраняет хранилище без неё."""
        self._ensure_loaded()
        note = self._require_note(note_id, action="удаление")

        snapshot = list(self._notes)
        self._notes.remove(note)
        self._commit(snapshot)

        logger.info("Удалена заметка id=%s title=%r", note.id, note.title)
        return note.clone()

    # --------------------------------------------------------------------- helpers
    def _find(self, note_id: str) -> Note | None:
        if not isinstance(note_id, str) or not note_id.strip():
            raise NoteValidationError("идентификатор заметки должен быть непустой строкой")
        wanted = note_id.strip()
        return next((note for note in self._notes if note.id == wanted), None)

    def _index_of(self, note_id: str) -> int:
        return next(index for index, note in enumerate(self._notes) if note.id == note_id)

    def _require_note(self, note_id: str, *, action: str) -> Note:
        """Правило: любое изменение начинается с получения заметки по id."""
        if not self._notes:
            logger.error("%s невозможно: хранилище пусто", action)
            raise EmptyStorageError(f"{action} невозможно: в хранилище нет заметок")

        note = self._find(note_id)
        if note is None:
            logger.error("%s невозможно: заметка id=%r не найдена", action, note_id)
            raise NoteNotFoundError(note_id, f"{action} невозможно: заметка id={note_id!r} не найдена")
        return note

    @staticmethod
    def _validate_changes(fields: dict[str, Any]) -> dict[str, Any]:
        if not fields:
            raise NoteValidationError("не передано ни одного поля для обновления")

        allowed = set(UPDATABLE_FIELDS)
        unknown = sorted(set(fields) - allowed)
        if unknown:
            raise NoteValidationError(
                f"нельзя изменить поля: {', '.join(unknown)}. "
                f"Доступно: {', '.join(UPDATABLE_FIELDS)}"
            )
        return fields

    def _commit(self, previous: Iterable[Note]) -> None:
        """Сохраняет состояние; при ошибке драйвера откатывает память."""
        try:
            self._driver.save([note.to_dict() for note in self._notes])
        except StorageError as exc:
            self._notes = list(previous)
            logger.error("Сохранение не удалось, изменения откачены: %s", exc)
            raise
