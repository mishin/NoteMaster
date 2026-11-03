"""Тесты CLI и интерактивного режима NoteMaster.

Запуск:
    python -m unittest discover -s tests -t .
    python -m pytest tests/
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  # корень проекта в sys.path

from app import cli, ui
from app.console import (
    InputError,
    parse_attachments,
    render_notes_table,
    resolve_note_id,
    success,
)
from app.core import Note, NoteManager


def _feed(values: list[str]):
    """input_func для UI: отдаёт строки по очереди, затем EOF."""
    iterator = iter(values)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    return fake_input


class CLITestCase(unittest.TestCase):
    """Интеграционные тесты argparse-команд поверх временного notes.json."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "notes.json"
        self.addCleanup(cli.reset_logging)

    def manager(self) -> NoteManager:
        return NoteManager(storage_path=self.path)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--storage", str(self.path), *argv])
        return code, out.getvalue(), err.getvalue()

    # ------------------------------------------------------------------ add
    def test_add_creates_note(self) -> None:
        code, out, _ = self.run_cli(
            "add", "Заметка", "-c", "Текст", "-a", "a.txt", "-a", "b.txt"
        )

        self.assertEqual(code, 0)
        self.assertIn("Заметка создана", out)
        notes = self.manager().get_all_notes()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "Заметка")
        self.assertEqual(notes[0].content, "Текст")
        self.assertEqual(notes[0].attachments, ["a.txt", "b.txt"])
        self.assertTrue(self.path.exists())

    def test_add_rejects_empty_title(self) -> None:
        code, _, err = self.run_cli("add", "   ")

        self.assertEqual(code, 1)
        self.assertIn("title", err)
        self.assertEqual(self.manager().get_all_notes(), [])

    def test_add_rejects_empty_attachment(self) -> None:
        code, _, err = self.run_cli("add", "Заметка", "-a", "  ")

        self.assertEqual(code, 2)
        self.assertIn("вложения", err.lower())

    # ----------------------------------------------------------------- list
    def test_list_empty_storage(self) -> None:
        code, out, _ = self.run_cli("list")

        self.assertEqual(code, 0)
        self.assertIn("Заметок пока нет", out)

    def test_list_shows_structured_table(self) -> None:
        self.manager().create_note("Список", "Текст", ["a.txt"])

        code, out, _ = self.run_cli("list")

        self.assertEqual(code, 0)
        self.assertIn("ЗАГОЛОВОК", out)
        self.assertIn("СОЗДАНА", out)
        self.assertIn("ВЛОЖЕНИЯ", out)
        self.assertIn("Список", out)
        self.assertIn("a.txt", out)

    def test_list_json(self) -> None:
        note = self.manager().create_note("JSON", "Текст")

        code, out, _ = self.run_cli("list", "--json")

        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload, [note.to_dict()])

    # ----------------------------------------------------------------- show
    def test_show_by_unique_prefix(self) -> None:
        note = self.manager().create_note("Префикс")

        code, out, _ = self.run_cli("show", note.id[:8])

        self.assertEqual(code, 0)
        self.assertIn(note.id, out)
        self.assertIn("Префикс", out)

    def test_show_by_row_number(self) -> None:
        self.manager().create_note("Первая")
        second = self.manager().create_note("Вторая")

        code, out, _ = self.run_cli("show", "2")

        self.assertEqual(code, 0)
        self.assertIn(second.id, out)
        self.assertIn("Вторая", out)

    def test_show_by_row_number_out_of_range(self) -> None:
        self.manager().create_note("Единственная")

        code, _, err = self.run_cli("show", "5")

        self.assertEqual(code, 2)
        self.assertIn("вне диапазона", err)

    def test_delete_by_row_number(self) -> None:
        first = self.manager().create_note("Первая")
        second = self.manager().create_note("Вторая")

        code, out, _ = self.run_cli("delete", "1", "--yes")

        self.assertEqual(code, 0)
        self.assertIn(first.id, out)
        remaining = self.manager().get_all_notes()
        self.assertEqual([note.id for note in remaining], [second.id])

    def test_show_unknown_id(self) -> None:
        self.manager().create_note("Есть")

        code, _, err = self.run_cli("show", "zzz")

        self.assertEqual(code, 2)
        self.assertIn("не найдена", err)

    # ----------------------------------------------------------------- edit
    def test_edit_is_partial(self) -> None:
        note = self.manager().create_note("Старый", "Текст", ["a.txt"])

        code, out, _ = self.run_cli("edit", note.id[:8], "--title", "Новый")

        self.assertEqual(code, 0)
        updated = self.manager().get_note_by_id(note.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.title, "Новый")
        self.assertEqual(updated.content, "Текст")
        self.assertEqual(updated.attachments, ["a.txt"])
        self.assertIn("Текущее значение", out)

    def test_edit_requires_at_least_one_field(self) -> None:
        note = self.manager().create_note("Заметка")

        code, out, _ = self.run_cli("edit", note.id)

        self.assertEqual(code, 2)
        self.assertIn("Не передано ни одного поля", out)

    def test_edit_clears_attachments(self) -> None:
        note = self.manager().create_note("Заметка", attachments=["a.txt"])

        code, _, _ = self.run_cli("edit", note.id, "--no-attachments")

        self.assertEqual(code, 0)
        self.assertEqual(self.manager().get_note_by_id(note.id).attachments, [])  # type: ignore[union-attr]

    def test_edit_rejects_conflicting_attachment_flags(self) -> None:
        note = self.manager().create_note("Заметка", attachments=["a.txt"])

        code, _, err = self.run_cli(
            "edit", note.id, "--attachment", "b.txt", "--no-attachments"
        )

        self.assertEqual(code, 2)
        self.assertIn("одновременно", err)

    # --------------------------------------------------------------- delete
    def test_delete_requires_yes_without_terminal(self) -> None:
        note = self.manager().create_note("Заметка")

        with mock.patch.object(cli, "_stdin_is_interactive", return_value=False):
            code, _, err = self.run_cli("delete", note.id)

        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        self.assertIsNotNone(self.manager().get_note_by_id(note.id))

    def test_delete_with_yes(self) -> None:
        note = self.manager().create_note("Заметка")

        code, out, _ = self.run_cli("delete", note.id[:8], "--yes")

        self.assertEqual(code, 0)
        self.assertIn("удалена", out.lower())
        self.assertEqual(self.manager().get_all_notes(), [])

    def test_delete_cancelled_by_user(self) -> None:
        note = self.manager().create_note("Заметка")

        with mock.patch.object(cli, "_stdin_is_interactive", return_value=True), \
                mock.patch.object(cli, "confirm", return_value=False):
            code, out, _ = self.run_cli("delete", note.id)

        self.assertEqual(code, 0)
        self.assertIn("отменено", out)
        self.assertIsNotNone(self.manager().get_note_by_id(note.id))

    # ------------------------------------------------------- прочие сценарии
    def test_log_file_records_user_action(self) -> None:
        log_path = Path(self._dir.name) / "cli.log"

        code, _, _ = self.run_cli("--log-file", str(log_path), "add", "Логируемая")

        self.assertEqual(code, 0)
        self.assertTrue(log_path.exists())
        self.assertIn("создана заметка", log_path.read_text(encoding="utf-8"))

    def test_no_command_prints_help(self) -> None:
        code, out, _ = self.run_cli()

        self.assertEqual(code, 0)
        self.assertIn("usage", out)

    def test_core_error_does_not_raise(self) -> None:
        self.path.write_text("[{", encoding="utf-8")

        code, _, err = self.run_cli("list")

        self.assertEqual(code, 1)
        self.assertIn("битый JSON", err)


class ConsoleHelpersTest(unittest.TestCase):
    """Форматирование и разбор ввода без запуска CLI."""

    def test_table_contains_required_fields(self) -> None:
        note = Note.create("Заголовок", attachments=["a.txt", "b.txt"])

        table = render_notes_table([note])

        self.assertIn(note.id[:8], table)
        self.assertIn("Заголовок", table)
        self.assertIn("a.txt, b.txt", table)
        self.assertRegex(table, r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}")

    def test_resolve_accepts_row_number(self) -> None:
        notes = [
            Note(id="aaaa1111-0000-0000-0000-000000000000", title="1"),
            Note(id="bbbb2222-0000-0000-0000-000000000000", title="2"),
            Note(id="cccc3333-0000-0000-0000-000000000000", title="3"),
        ]

        self.assertEqual(resolve_note_id(notes, "1"), notes[0].id)
        self.assertEqual(resolve_note_id(notes, "3"), notes[2].id)

    def test_resolve_accepts_decorated_row_number(self) -> None:
        notes = [
            Note(id="aaaa1111-0000-0000-0000-000000000000", title="1"),
            Note(id="bbbb2222-0000-0000-0000-000000000000", title="2"),
        ]

        for raw in ("2", "[2]", "#2", " (2) "):
            with self.subTest(raw=raw):
                self.assertEqual(resolve_note_id(notes, raw), notes[1].id)

    def test_resolve_rejects_row_number_out_of_range(self) -> None:
        notes = [
            Note(id="aaaa1111-0000-0000-0000-000000000000", title="1"),
            Note(id="bbbb2222-0000-0000-0000-000000000000", title="2"),
        ]

        for raw in ("0", "3", "99"):
            with self.subTest(raw=raw), self.assertRaises(InputError) as ctx:
                resolve_note_id(notes, raw)
            self.assertIn("вне диапазона", str(ctx.exception))

    def test_resolve_exact_id_wins_over_row_number(self) -> None:
        notes = [
            Note(id="aaaa1111-0000-0000-0000-000000000000", title="1"),
            Note(id="2", title="id совпадает с номером"),
        ]

        self.assertEqual(resolve_note_id(notes, "2"), "2")

    def test_resolve_rejects_empty_storage(self) -> None:
        with self.assertRaises(InputError):
            resolve_note_id([], "1")

    def test_resolve_exact_and_unique_prefix(self) -> None:
        notes = [
            Note(id="aaaa1111-0000-0000-0000-000000000000", title="1"),
            Note(id="aaaa2222-0000-0000-0000-000000000000", title="2"),
            Note(id="bbbb3333-0000-0000-0000-000000000000", title="3"),
        ]

        self.assertEqual(resolve_note_id(notes, notes[0].id), notes[0].id)
        self.assertEqual(resolve_note_id(notes, "bbbb"), notes[2].id)

    def test_resolve_rejects_ambiguous_unknown_and_empty(self) -> None:
        notes = [
            Note(id="aaaa1111-0000-0000-0000-000000000000", title="1"),
            Note(id="aaaa2222-0000-0000-0000-000000000000", title="2"),
        ]

        for raw in ("aaaa", "cccc", "   "):
            with self.subTest(raw=raw), self.assertRaises(InputError):
                resolve_note_id(notes, raw)

    def test_parse_attachments_deduplicates(self) -> None:
        self.assertEqual(parse_attachments("a.txt, b.png; a.txt"), ["a.txt", "b.png"])
        self.assertEqual(parse_attachments("   "), [])

    def test_no_ansi_codes_in_redirected_output(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            success("готово")

        self.assertNotIn("\033[", out.getvalue())


class UITestCase(unittest.TestCase):
    """Сценарии интерактивного режима с подменённым вводом."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "notes.json"
        self.manager = NoteManager(storage_path=self.path)
        self.addCleanup(cli.reset_logging)

    def run_ui(self, values: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ui.run(self.manager, input_func=_feed(values))
        return code, out.getvalue(), err.getvalue()

    def test_add_full_flow(self) -> None:
        code, out, _ = self.run_ui(
            ["add", "Моя заметка", "Строка 1", "Строка 2", "", "a.txt, b.txt", "exit"]
        )

        self.assertEqual(code, 0)
        self.assertIn("Заметка создана", out)
        notes = self.manager.get_all_notes()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "Моя заметка")
        self.assertEqual(notes[0].content, "Строка 1\nСтрока 2")
        self.assertEqual(notes[0].attachments, ["a.txt", "b.txt"])

    def test_add_repeats_prompt_for_empty_title(self) -> None:
        code, out, _ = self.run_ui(["add", "", "Заголовок", "", "", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("не может быть пустым", out)
        self.assertEqual(self.manager.get_all_notes()[0].title, "Заголовок")

    def test_list_empty_storage(self) -> None:
        code, out, _ = self.run_ui(["list", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("Заметок пока нет", out)

    def test_unknown_command_and_help(self) -> None:
        code, out, _ = self.run_ui(["wat", "help", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("Неизвестная команда", out)
        self.assertIn("delete", out)

    def test_show_by_row_number(self) -> None:
        self.manager.create_note("Первая")
        second = self.manager.create_note("Вторая")

        code, out, _ = self.run_ui(["show", "2", "exit"])

        self.assertEqual(code, 0)
        self.assertIn(second.id, out)
        self.assertIn("Вторая", out)

    def test_show_by_decorated_number(self) -> None:
        self.manager.create_note("Первая")
        second = self.manager.create_note("Вторая")

        code, out, _ = self.run_ui(["show [2]", "exit"])

        self.assertEqual(code, 0)
        self.assertIn(second.id, out)

    def test_show_prompts_with_number_hint(self) -> None:
        note = self.manager.create_note("Заметка")
        prompts: list[str] = []
        values = iter(["show", "1", "exit"])

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            try:
                return next(values)
            except StopIteration as exc:
                raise EOFError from exc

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ui.run(self.manager, input_func=fake_input)

        self.assertEqual(code, 0)
        self.assertTrue(
            any("номер или id" in prompt for prompt in prompts),
            msg=f"prompts={prompts}",
        )
        self.assertIn(note.id, out.getvalue())

    def test_edit_by_row_number(self) -> None:
        first = self.manager.create_note("Первая")
        second = self.manager.create_note("Вторая")

        code, out, _ = self.run_ui(["edit", "2", "Новая", "", "", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("Заметка обновлена", out)
        self.assertEqual(self.manager.get_note_by_id(first.id).title, "Первая")  # type: ignore[union-attr]
        self.assertEqual(self.manager.get_note_by_id(second.id).title, "Новая")  # type: ignore[union-attr]

    def test_delete_by_row_number(self) -> None:
        first = self.manager.create_note("Первая")
        second = self.manager.create_note("Вторая")

        code, out, _ = self.run_ui(["delete", "1", "y", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("удалена", out.lower())
        remaining = self.manager.get_all_notes()
        self.assertEqual([note.id for note in remaining], [second.id])
        self.assertIsNone(self.manager.get_note_by_id(first.id))

    def test_out_of_range_number_keeps_session(self) -> None:
        self.manager.create_note("Единственная")

        code, _, err = self.run_ui(["show", "9", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("вне диапазона", err)

    def test_invalid_id_does_not_stop_session(self) -> None:
        self.manager.create_note("Есть")

        code, _, err = self.run_ui(["show", "zzz", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("не найдена", err)

    def test_edit_keeps_values_on_empty_input(self) -> None:
        note = self.manager.create_note("Старый", "Текст", ["a.txt"])

        code, out, _ = self.run_ui(["edit", note.id, "", "", "", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("Изменений нет", out)
        unchanged = self.manager.get_note_by_id(note.id)
        self.assertIsNotNone(unchanged)
        assert unchanged is not None
        self.assertEqual(unchanged.title, "Старый")
        self.assertEqual(unchanged.content, "Текст")
        self.assertEqual(unchanged.attachments, ["a.txt"])

    def test_edit_without_changes_when_note_has_no_attachments(self) -> None:
        note = self.manager.create_note("Без вложений")

        code, out, _ = self.run_ui(["edit", note.id, "", "", "", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("Изменений нет", out)

    def test_edit_updates_title_and_content(self) -> None:
        note = self.manager.create_note("Старый", "Старый текст")

        code, out, _ = self.run_ui(
            ["edit", note.id, "Новый", "Новый текст", "", "", "exit"]
        )

        self.assertEqual(code, 0)
        self.assertIn("Заметка обновлена", out)
        updated = self.manager.get_note_by_id(note.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.title, "Новый")
        self.assertEqual(updated.content, "Новый текст")

    def test_edit_can_clear_content_and_attachments(self) -> None:
        note = self.manager.create_note("Заметка", "Текст", ["a.txt"])

        self.run_ui(["edit", note.id, "", "-", "-", "exit"])

        cleared = self.manager.get_note_by_id(note.id)
        self.assertIsNotNone(cleared)
        assert cleared is not None
        self.assertEqual(cleared.content, "")
        self.assertEqual(cleared.attachments, [])

    def test_delete_cancelled(self) -> None:
        note = self.manager.create_note("Заметка")

        code, out, _ = self.run_ui(["delete", note.id, "n", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("отменено", out)
        self.assertIsNotNone(self.manager.get_note_by_id(note.id))

    def test_delete_confirmed(self) -> None:
        note = self.manager.create_note("Заметка")

        code, out, _ = self.run_ui(["delete", note.id, "y", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("удалена", out.lower())
        self.assertEqual(self.manager.get_all_notes(), [])

    def test_empty_storage_operations_are_safe(self) -> None:
        code, out, _ = self.run_ui(["list", "delete", "edit", "exit"])

        self.assertEqual(code, 0)
        self.assertIn("Заметок пока нет", out)
        self.assertIn("удалять нечего", out)
        self.assertIn("редактировать нечего", out)

    def test_eof_exits_gracefully(self) -> None:
        code, out, _ = self.run_ui([])

        self.assertEqual(code, 0)
        self.assertIn("Завершение работы", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
