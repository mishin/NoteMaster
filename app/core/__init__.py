"""Ядро NoteMaster: модель заметки и менеджер заметок.

Публичный API слоя — то, что должны импортировать внешние слои (CLI, API,
интеграции).
"""

from app.core.exceptions import (
    EmptyStorageError,
    NoteMasterError,
    NoteNotFoundError,
    NoteValidationError,
    StorageError,
)
from app.core.models import UPDATABLE_FIELDS, Note, current_timestamp, generate_note_id
from app.core.note_manager import NoteManager

__all__ = [
    "EmptyStorageError",
    "Note",
    "NoteManager",
    "NoteMasterError",
    "NoteNotFoundError",
    "NoteValidationError",
    "StorageError",
    "UPDATABLE_FIELDS",
    "current_timestamp",
    "generate_note_id",
]
