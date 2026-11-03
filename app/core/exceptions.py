"""Исключения ядра.

Единая иерархия позволяет внешнему слою (CLI, HTTP API) перехватить
NoteMasterError одним блоком и при этом различать природу проблемы.
"""

from __future__ import annotations


class NoteMasterError(Exception):
    """Базовая ошибка всех слоёв приложения."""


class StorageError(NoteMasterError):
    """Хранилище недоступно или содержит некорректные данные.

    Сюда относятся: ошибку ввода-вывода, битый JSON, структуру файла не
    соответствующую формату «список объектов».
    """


class NoteValidationError(NoteMasterError):
    """Нарушено бизнес-правило к полям заметки."""


class NoteNotFoundError(NoteMasterError):
    """Заметка с запрошенным идентификатором отсутствует."""

    def __init__(self, note_id: str, message: str | None = None) -> None:
        super().__init__(message or f"заметка с id={note_id!r} не найдена")
        self.note_id = note_id


class EmptyStorageError(NoteNotFoundError):
    """Пустое хранилище: редактировать и удалять нечего.

    Наследуется от NoteNotFoundError, поэтому существующий код, ловящий
    «заметка не найдена», продолжает работать.
    """

    def __init__(self, message: str = "хранилище пусто: заметок для изменения нет") -> None:
        super().__init__(note_id="", message=message)
