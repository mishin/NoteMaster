"""Тесты ядра NoteMaster: модель, драйвер, бизнес-правила NoteManager.

Запуск без сторонних зависимостей:
    python -m unittest discover -s tests -t .
или, если установлен pytest:
    python -m pytest
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import tests  # noqa: F401  # обеспечивает корень проекта в sys.path

from app.core.exceptions import (
    EmptyStorageError,
    NoteMasterError,
    NoteNotFoundError,
    NoteValidationError,
    StorageError,
)
from app.core.models import Note
from app.core.note_manager import NoteManager
from app.driver.file_driver import BaseNotesDriver, FileDriver


class InMemoryDriver(BaseNotesDriver):
    """Драйвер-заглушка: доказывает, что ядро не привязано к файловой системе."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records: list[dict[str, Any]] = records or []
        self.load_calls = 0
        self.save_calls = 0

    def load(self) -> list[dict[str, Any]]:
        self.load_calls += 1
        return json.loads(json.dumps(self.records))

    def save(self, records: list[dict[str, Any]]) -> None:
        self.save_calls += 1
        self.records = json.loads(json.dumps(records))


class BrokenSaveDriver(InMemoryDriver):
    """Драйвер, у которого запись всегда падает — проверка отката состояния."""

    def save(self, records: list[dict[str, Any]]) -> None:
        raise StorageError("диск недоступен")


class NoteModelTest(unittest.TestCase):
    """Бизнес-правила модели Note."""

    def test_create_generates_id_and_timestamp(self) -> None:
        note = Note.create("Заголовок")

        self.assertEqual(len(note.id), 36)  # UUID4
        self.assertEqual(note.title, "Заголовок")
        self.assertEqual(note.content, "")
        self.assertEqual(note.attachments, [])
        self.assertRegex(note.timestamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_title_is_required_and_stripped(self) -> None:
        self.assertEqual(Note(id="1", title="  Космос  ").title, "Космос")

        for bad_title in ("", "   ", None, 123):
            with self.subTest(title=bad_title), self.assertRaises(NoteValidationError):
                Note(id="1", title=bad_title)  # type: ignore[arg-type]

    def test_content_must_be_string_or_none(self) -> None:
        self.assertEqual(Note(id="1", title="t", content=None).content, "")  # type: ignore[arg-type]
        with self.assertRaises(NoteValidationError):
            Note(id="1", title="t", content=42)  # type: ignore[arg-type]

    def test_attachments_must_be_list_of_strings(self) -> None:
        note = Note(id="1", title="t", attachments=("a.txt", "b.txt"))
        self.assertEqual(note.attachments, ["a.txt", "b.txt"])

        for bad in ("a.txt", 5, {"a": 1}, ["a.txt", None], ["a.txt", ""], ["a.txt", 7]):
            with self.subTest(attachments=bad), self.assertRaises(NoteValidationError):
                Note(id="1", title="t", attachments=bad)  # type: ignore[arg-type]

    def test_duplicate_attachments_are_removed(self) -> None:
        note = Note(id="1", title="t", attachments=["a.txt", " a.txt ", "b.txt"])
        self.assertEqual(note.attachments, ["a.txt", "b.txt"])

    def test_timestamp_must_be_iso_8601(self) -> None:
        for bad in ("не дата", "2026-13-45", 17, ""):
            with self.subTest(timestamp=bad), self.assertRaises(NoteValidationError):
                Note(id="1", title="t", timestamp=bad)  # type: ignore[arg-type]

    def test_id_must_be_non_empty_string(self) -> None:
        for bad in ("", "  ", None, 1):
            with self.subTest(id=bad), self.assertRaises(NoteValidationError):
                Note(id=bad, title="t")  # type: ignore[arg-type]

    def test_to_dict_from_dict_roundtrip(self) -> None:
        note = Note.create("Заголовок", "Текст", ["a.txt", "b.txt"])
        restored = Note.from_dict(note.to_dict())

        self.assertEqual(restored, note)
        self.assertIsNot(restored.attachments, note.attachments)

    def test_from_dict_ignores_unknown_fields_and_fills_defaults(self) -> None:
        note = Note.from_dict({"id": "1", "title": "t", "future_field": 1})

        self.assertEqual(note.content, "")
        self.assertEqual(note.attachments, [])
        self.assertTrue(note.timestamp)

    def test_from_dict_rejects_broken_record(self) -> None:
        for bad in (None, [], "row", {"title": "без id"}, {"id": "1", "title": " "}):
            with self.subTest(record=bad), self.assertRaises(NoteValidationError):
                Note.from_dict(bad)

    def test_clone_and_updated_do_not_change_original(self) -> None:
        note = Note.create("Заголовок", "Текст")
        clone = note.clone()
        clone.attachments.append("hack.txt")
        self.assertEqual(note.attachments, [])

        updated = note.updated({"title": "Другой"})
        self.assertEqual(note.title, "Заголовок")
        self.assertEqual(updated.title, "Другой")
        self.assertEqual(updated.id, note.id)


class FileDriverTest(unittest.TestCase):
    """Работа драйвера с файлом notes.json."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "notes.json"

    def test_missing_file_is_created_with_empty_list(self) -> None:
        driver = FileDriver(self.path)

        self.assertEqual(driver.load(), [])
        self.assertTrue(self.path.exists())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [])

    def test_creates_missing_parent_directories(self) -> None:
        deep = Path(self._dir.name) / "a" / "b" / "notes.json"

        FileDriver(deep).save([])

        self.assertTrue(deep.exists())

    def test_empty_file_is_treated_as_empty_storage(self) -> None:
        self.path.write_text("   ", encoding="utf-8")

        self.assertEqual(FileDriver(self.path).load(), [])

    def test_broken_json_raises_storage_error(self) -> None:
        self.path.write_text("[{" , encoding="utf-8")

        with self.assertRaises(StorageError) as ctx:
            FileDriver(self.path).load()
        self.assertIn("битый JSON", str(ctx.exception))

    def test_non_list_root_raises_storage_error(self) -> None:
        self.path.write_text('{"id": "1"}', encoding="utf-8")

        with self.assertRaises(StorageError):
            FileDriver(self.path).load()

    def test_record_must_be_object(self) -> None:
        self.path.write_text('["не объект"]', encoding="utf-8")

        with self.assertRaises(StorageError):
            FileDriver(self.path).load()

    def test_save_is_atomic_and_keeps_no_temp_files(self) -> None:
        driver = FileDriver(self.path)
        driver.save([{"id": "1", "title": "Раз"}])

        leftovers = [p.name for p in Path(self._dir.name).iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))[0]["title"], "Раз")

    def test_unicode_is_preserved(self) -> None:
        FileDriver(self.path).save([{"id": "1", "title": "Привет, мир 🙂"}])

        self.assertIn("Привет, мир", self.path.read_text(encoding="utf-8"))


class NoteManagerStorageTest(unittest.TestCase):
    """Менеджер + файловый драйвер: сохранение и чтение данных."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "notes.json"
        self.manager = NoteManager(storage_path=self.path)

    def test_starts_empty_and_creates_file(self) -> None:
        self.assertEqual(self.manager.get_all_notes(), [])
        self.assertTrue(self.path.exists())

    def test_created_note_is_written_to_disk(self) -> None:
        note = self.manager.create_note("Заметка", "Тело", ["files/a.png"])

        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored, [note.to_dict()])

    def test_notes_survive_new_manager_instance(self) -> None:
        first = self.manager.create_note("Раз", "текст")
        self.manager.create_note("Два", attachments=["b.txt"])

        reopened = NoteManager(storage_path=self.path)
        notes = reopened.get_all_notes()

        self.assertEqual([note.title for note in notes], ["Раз", "Два"])
        self.assertEqual(notes[0].id, first.id)

    def test_get_all_notes_returns_copies(self) -> None:
        self.manager.create_note("Оригинал")

        notes = self.manager.get_all_notes()
        notes[0].title = "Изменённый"
        notes[0].attachments.append("мусор")

        self.assertEqual(self.manager.get_all_notes()[0].title, "Оригинал")
        self.assertEqual(self.manager.get_all_notes()[0].attachments, [])

    def test_storage_path_and_driver_are_exposed(self) -> None:
        self.assertEqual(self.manager.storage_path, self.path)
        self.assertIsInstance(self.manager.driver, FileDriver)

    def test_corrupted_record_raises_storage_error(self) -> None:
        self.path.write_text('[{"id": "1", "title": "ок"}, {"title": "без id"}]', encoding="utf-8")

        with self.assertRaises(StorageError) as ctx:
            self.manager.get_all_notes()
        self.assertIn("повреждённая запись №1", str(ctx.exception))

    def test_reload_reads_external_changes(self) -> None:
        self.manager.create_note("Старая")
        self.path.write_text(
            json.dumps([{"id": "ext-1", "title": "Внешняя", "content": "", "attachments": [],
                         "timestamp": "2026-01-01T00:00:00+00:00"}], ensure_ascii=False),
            encoding="utf-8",
        )

        notes = self.manager.reload()

        self.assertEqual([note.id for note in notes], ["ext-1"])

    def test_lazy_load_reads_storage_once(self) -> None:
        driver = InMemoryDriver()
        manager = NoteManager(driver)

        manager.get_all_notes()
        manager.get_all_notes()

        self.assertEqual(driver.load_calls, 1)


class NoteManagerRulesTest(unittest.TestCase):
    """Бизнес-правила CRUD: пустое хранилище, работа по id, частичное обновление."""

    def setUp(self) -> None:
        self.driver = InMemoryDriver()
        self.manager = NoteManager(self.driver)

    # ------------------------------------------------------------------ создание
    def test_create_note_returns_note_and_persists(self) -> None:
        note = self.manager.create_note("Первая", "Содержимое", ["a.txt"])

        self.assertIsInstance(note, Note)
        self.assertEqual(self.driver.save_calls, 1)
        self.assertEqual(self.manager.get_note_by_id(note.id), note)

    def test_create_note_validates_title(self) -> None:
        with self.assertRaises(NoteValidationError):
            self.manager.create_note("   ")
        self.assertEqual(self.manager.get_all_notes(), [])
        self.assertEqual(self.driver.save_calls, 0)

    # ------------------------------------------------------------------- чтение
    def test_get_note_by_id_returns_none_when_absent(self) -> None:
        self.assertIsNone(self.manager.get_note_by_id("нет-такого"))

    def test_get_note_by_id_validates_identifier(self) -> None:
        with self.assertRaises(NoteValidationError):
            self.manager.get_note_by_id("  ")

    def test_get_all_notes_keeps_insertion_order(self) -> None:
        created = [self.manager.create_note(f"Заметка {i}") for i in range(3)]

        self.assertEqual([n.id for n in self.manager.get_all_notes()], [n.id for n in created])

    # ---------------------------------------------------------------- обновление
    def test_update_note_changes_only_given_fields(self) -> None:
        note = self.manager.create_note("Заголовок", "Текст", ["a.txt"])

        updated = self.manager.update_note(note.id, title="Новый заголовок")

        self.assertEqual(updated.title, "Новый заголовок")
        self.assertEqual(updated.content, "Текст")
        self.assertEqual(updated.attachments, ["a.txt"])
        self.assertEqual(updated.timestamp, note.timestamp)
        self.assertEqual(updated.id, note.id)

    def test_update_note_accepts_several_fields(self) -> None:
        note = self.manager.create_note("Старый")

        updated = self.manager.update_note(note.id, title="Новый", content="Тело", attachments=["x.md"])

        self.assertEqual((updated.title, updated.content, updated.attachments),
                         ("Новый", "Тело", ["x.md"]))
        self.assertEqual(self.manager.get_note_by_id(note.id), updated)

    def test_update_note_requires_existing_id(self) -> None:
        self.manager.create_note("Существующая")

        with self.assertRaises(NoteNotFoundError):
            self.manager.update_note("несуществующий-id", title="Новый")

    def test_update_note_rejects_unknown_and_protected_fields(self) -> None:
        note = self.manager.create_note("Заголовок")

        for field in ("id", "timestamp", "unknown"):
            with self.subTest(field=field), self.assertRaises(NoteValidationError):
                self.manager.update_note(note.id, **{field: "значение"})

    def test_update_note_requires_at_least_one_field(self) -> None:
        note = self.manager.create_note("Заголовок")

        with self.assertRaises(NoteValidationError):
            self.manager.update_note(note.id)

    def test_update_note_rejects_invalid_values_and_keeps_original(self) -> None:
        note = self.manager.create_note("Заголовок")

        with self.assertRaises(NoteValidationError):
            self.manager.update_note(note.id, title="   ")
        with self.assertRaises(NoteValidationError):
            self.manager.update_note(note.id, attachments="a.txt")

        self.assertEqual(self.manager.get_note_by_id(note.id), note)

    # ------------------------------------------------------------------- удаление
    def test_delete_note_removes_and_persists(self) -> None:
        first = self.manager.create_note("Первая")
        second = self.manager.create_note("Вторая")

        removed = self.manager.delete_note(first.id)

        self.assertEqual(removed, first)
        self.assertEqual([n.id for n in self.manager.get_all_notes()], [second.id])
        self.assertEqual(len(self.driver.records), 1)

    def test_delete_note_requires_existing_id(self) -> None:
        self.manager.create_note("Первая")

        with self.assertRaises(NoteNotFoundError):
            self.manager.delete_note("чужой-id")

    def test_deleted_note_cannot_be_updated(self) -> None:
        note = self.manager.create_note("Первая")
        self.manager.delete_note(note.id)
        self.manager.create_note("Вторая")

        with self.assertRaises(NoteNotFoundError):
            self.manager.update_note(note.id, title="Нет")

    # ------------------------------------------------------------- пустое хранилище
    def test_update_on_empty_storage_raises_empty_storage_error(self) -> None:
        with self.assertRaises(EmptyStorageError):
            self.manager.update_note("любой-id", title="Новый")

    def test_delete_on_empty_storage_raises_empty_storage_error(self) -> None:
        with self.assertRaises(EmptyStorageError):
            self.manager.delete_note("любой-id")

    def test_empty_storage_error_is_also_note_not_found(self) -> None:
        with self.assertRaises(NoteNotFoundError):
            self.manager.delete_note("любой-id")

    def test_operations_after_last_delete_are_blocked(self) -> None:
        note = self.manager.create_note("Единственная")
        self.manager.delete_note(note.id)

        with self.assertRaises(EmptyStorageError):
            self.manager.update_note(note.id, title="Новый")


class NoteManagerFailureTest(unittest.TestCase):
    """Сбой сохранения не должен оставлять «призрачные» заметки в памяти."""

    def test_create_is_rolled_back_on_save_failure(self) -> None:
        manager = NoteManager(BrokenSaveDriver())

        with self.assertRaises(StorageError):
            manager.create_note("Заметка")

        self.assertEqual(manager.get_all_notes(), [])

    def test_update_is_rolled_back_on_save_failure(self) -> None:
        driver = InMemoryDriver()
        manager = NoteManager(driver)
        note = manager.create_note("Было")
        driver.save = BrokenSaveDriver().save  # type: ignore[method-assign]

        with self.assertRaises(StorageError):
            manager.update_note(note.id, title="Стало")

        self.assertEqual(manager.get_note_by_id(note.id).title, "Было")  # type: ignore[union-attr]

    def test_core_errors_share_single_base_exception(self) -> None:
        for error in (StorageError("x"), NoteValidationError("x"), NoteNotFoundError("x")):
            self.assertIsInstance(error, NoteMasterError)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
