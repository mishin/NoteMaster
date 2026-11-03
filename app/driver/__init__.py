"""Слой драйверов хранилища NoteMaster."""

from app.driver.file_driver import (
    DEFAULT_DATA_DIR,
    DEFAULT_STORAGE_PATH,
    BaseNotesDriver,
    FileDriver,
)

__all__ = [
    "DEFAULT_DATA_DIR",
    "DEFAULT_STORAGE_PATH",
    "BaseNotesDriver",
    "FileDriver",
]
