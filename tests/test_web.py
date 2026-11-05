"""Тесты веб-слоя NoteMaster: маршруты, формы, ошибки, интеграция с ядром.

Запуск:
    python -m unittest discover -s tests -t .
    python -m pytest tests/
"""

from __future__ import annotations

import re
import tempfile
import unittest
import urllib.parse
from io import BytesIO
from pathlib import Path

import tests  # noqa: F401  — корень проекта в sys.path

from app.core import NoteManager
from web.app import (
    attachment_kind,
    create_app,
    human_size,
    safe_attachment_name,
    split_attachments,
)


class WebTestCase(unittest.TestCase):
    """Общая фикстура: временное хранилище, каталог вложений и тестовый клиент."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.storage = Path(self._dir.name) / "notes.json"
        self.attachments = Path(self._dir.name) / "attachments"
        self.app = create_app(
            storage_path=self.storage,
            secret_key="test-secret",
            attachments_dir=self.attachments,
        )
        self.client = self.app.test_client()

    # ------------------------------------------------------------- helpers
    def html(self, response) -> str:
        return response.get_data(as_text=True)

    def body(self, url: str) -> tuple[int, str]:
        with self.client.get(url) as response:
            return response.status_code, self.html(response)

    def add_note(self, **fields) -> str:
        """Создаёт заметку через POST /add и возвращает id из редиректа."""
        response = self.client.post("/add", data=fields)
        self.assertEqual(response.status_code, 302, self.html(response))
        location = response.headers["Location"]
        self.assertIn("/note/", location)
        return urllib.parse.unquote(location.rsplit("/note/", 1)[-1])

    def stored(self) -> list:
        return NoteManager(storage_path=self.storage).get_all_notes()

    def dismiss_flash(self) -> None:
        """Рендерит страницу, чтобы потребить накопленные flash-сообщения."""
        self.client.get("/")


class IndexViewTest(WebTestCase):
    """GET / — список заметок."""

    def test_empty_storage_shows_hint(self) -> None:
        code, html = self.body("/")

        self.assertEqual(code, 200)
        self.assertIn("Заметок пока нет", html)
        self.assertIn("add-form", html)

    def test_missing_file_is_created(self) -> None:
        self.body("/")

        self.assertTrue(self.storage.exists())

    def test_lists_created_notes(self) -> None:
        note_id = self.add_note(title="Первая", content="Текст первой")

        _, html = self.body("/")

        self.assertIn("Первая", html)
        self.assertIn("Текст первой", html)
        self.assertIn(note_id, html)

    def test_card_has_actions(self) -> None:
        note_id = self.add_note(title="Действия")
        quoted = urllib.parse.quote(note_id)

        _, html = self.body("/")

        self.assertIn(f'href="/note/{quoted}"', html)
        self.assertIn(f'href="/note/{quoted}#edit"', html)
        self.assertIn(f'action="/delete/{quoted}"', html)

    def test_notes_count_in_toolbar(self) -> None:
        self.add_note(title="Одна")
        self.dismiss_flash()
        self.add_note(title="Две")

        _, html = self.body("/")

        self.assertIn("(2)", html)

    def test_html_is_escaped(self) -> None:
        self.add_note(title="<script>alert(1)</script>")

        _, html = self.body("/")

        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)


class StaticAssetsTest(WebTestCase):
    """Статика доступна."""

    def test_css(self) -> None:
        code, css = self.body("/static/css/styles.css")

        self.assertEqual(code, 200)
        self.assertIn("--c-lemon", css)
        self.assertIn("border-radius", css)
        self.assertIn("transition", css)

    def test_js_files(self) -> None:
        for name, marker in (("ui.js", "data-confirm"), ("background.js", "bg-canvas")):
            with self.subTest(js=name):
                code, js = self.body(f"/static/js/{name}")
                self.assertEqual(code, 200)
                self.assertIn(marker, js)


class AddNoteTest(WebTestCase):
    """POST /add."""

    def test_creates_note_in_core(self) -> None:
        self.add_note(title="Создана", content="Тело", attachments="f1.md, f2.md")

        notes = self.stored()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "Создана")
        self.assertEqual(notes[0].content, "Тело")
        self.assertEqual(notes[0].attachments, ["f1.md", "f2.md"])

    def test_empty_title_returns_422_and_keeps_input(self) -> None:
        response = self.client.post("/add", data={"title": "", "content": "Важный текст"})

        self.assertEqual(response.status_code, 422)
        html = self.html(response)
        self.assertIn("Не удалось создать заметку", html)
        self.assertIn("Важный текст", html)  # введённое значение не потеряно
        self.assertIn("form-panel--open", html)
        self.assertEqual(self.stored(), [])

    def test_blank_title_returns_422(self) -> None:
        response = self.client.post("/add", data={"title": "   "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.stored(), [])

    def test_success_flash_after_redirect(self) -> None:
        response = self.client.post("/add", data={"title": "Успех"}, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        html = self.html(response)
        self.assertIn("flash--success", html)
        self.assertIn("Заметка «Успех» создана", html)

    def test_created_timestamp_is_iso(self) -> None:
        self.add_note(title="Дата")

        self.assertRegex(self.stored()[0].timestamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_upload_filename_added_as_attachment(self) -> None:
        self.client.post(
            "/add",
            data={"title": "С файлом", "attachment_files": (BytesIO(b"data"), "photo.png")},
            content_type="multipart/form-data",
        )

        self.assertEqual(self.stored()[0].attachments, ["photo.png"])


class NoteViewTest(WebTestCase):
    """GET /note/<id>."""

    def test_shows_full_note(self) -> None:
        note_id = self.add_note(title="Детали", content="Строка1\nСтрока2", attachments="x.md")

        code, html = self.body(f"/note/{note_id}")

        self.assertEqual(code, 200)
        self.assertIn("Детали", html)
        self.assertIn("Строка1", html)
        self.assertIn("x.md", html)
        self.assertIn(note_id, html)  # полный id подписан под карточкой
        self.assertIn("edit-form", html)

    def test_empty_content_hint(self) -> None:
        note_id = self.add_note(title="Пустая")

        _, html = self.body(f"/note/{note_id}")

        self.assertIn("Заметка пустая", html)

    def test_unknown_id_redirects_to_index_with_error(self) -> None:
        url = "/note/00000000-dead-beef-0000-000000000000"

        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

        follow = self.client.get(url, follow_redirects=True)
        html = self.html(follow)
        self.assertIn("не найдена", html)
        self.assertIn("flash--error", html)


class EditNoteTest(WebTestCase):
    """POST /edit/<id> — частичное обновление через ядро."""

    def test_partial_update_keeps_other_fields(self) -> None:
        note_id = self.add_note(title="Старый", content="Текст", attachments="keep.txt")
        self.dismiss_flash()

        self.client.post(
            f"/edit/{note_id}",
            data={"title": "Новый", "content": "Текст", "attachments": "keep.txt"},
        )

        note = self.stored()[0]
        self.assertEqual(note.title, "Новый")
        self.assertEqual(note.content, "Текст")
        self.assertEqual(note.attachments, ["keep.txt"])

    def test_content_can_be_cleared(self) -> None:
        note_id = self.add_note(title="Заметка", content="Было")
        self.dismiss_flash()

        self.client.post(f"/edit/{note_id}", data={"title": "Заметка", "content": "", "attachments": ""})

        self.assertEqual(self.stored()[0].content, "")

    def test_timestamp_is_not_changed(self) -> None:
        note_id = self.add_note(title="Заметка")
        before = self.stored()[0].timestamp
        self.dismiss_flash()

        self.client.post(
            f"/edit/{note_id}", data={"title": "Обновлено", "content": "", "attachments": ""}
        )

        self.assertEqual(self.stored()[0].timestamp, before)

    def test_no_changes_warns(self) -> None:
        note_id = self.add_note(title="Без изменений", content="Текст")

        response = self.client.post(
            f"/edit/{note_id}",
            data={"title": "Без изменений", "content": "Текст", "attachments": ""},
            follow_redirects=True,
        )

        html = self.html(response)
        self.assertIn("Изменений не найдено", html)
        self.assertIn("flash--warning", html)
        self.assertEqual(self.stored()[0].title, "Без изменений")

    def test_empty_title_is_rejected(self) -> None:
        note_id = self.add_note(title="Нормальный")
        self.dismiss_flash()

        response = self.client.post(
            f"/edit/{note_id}", data={"title": "  ", "content": "Текст"}, follow_redirects=True
        )

        html = self.html(response)
        self.assertIn("Не удалось обновить заметку", html)
        self.assertEqual(self.stored()[0].title, "Нормальный")

    def test_unknown_id_redirects_to_index(self) -> None:
        response = self.client.post("/edit/unknown", data={"title": "Что-то"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_get_is_not_allowed(self) -> None:
        note_id = self.add_note(title="Заметка")

        self.assertEqual(self.body(f"/note/{note_id}")[0], 200)
        self.assertEqual(self.client.get(f"/edit/{note_id}").status_code, 405)


class DeleteNoteTest(WebTestCase):
    """POST /delete/<id>."""

    def test_removes_note(self) -> None:
        note_id = self.add_note(title="На удаление")

        response = self.client.post(f"/delete/{note_id}", follow_redirects=True)

        self.assertEqual(self.stored(), [])
        self.assertIn("Заметка «На удаление» удалена", self.html(response))

    def test_other_notes_survive(self) -> None:
        first = self.add_note(title="Первая")
        self.dismiss_flash()
        second = self.add_note(title="Вторая")
        self.dismiss_flash()

        self.client.post(f"/delete/{first}")

        self.assertEqual([note.id for note in self.stored()], [second])

    def test_empty_storage_shows_warning(self) -> None:
        response = self.client.post("/delete/any-id", follow_redirects=True)

        html = self.html(response)
        self.assertIn("удалять нечего", html)
        self.assertIn("flash--warning", html)

    def test_unknown_id_shows_error(self) -> None:
        self.add_note(title="Существует")

        response = self.client.post("/delete/nonexistent-id", follow_redirects=True)

        html = self.html(response)
        self.assertIn("Не удалось удалить", html)
        self.assertIn("flash--error", html)
        self.assertEqual(len(self.stored()), 1)

    def test_get_is_not_allowed(self) -> None:
        response = self.client.get("/delete/any-id")

        self.assertEqual(response.status_code, 405)
        self.assertIn("Метод не поддерживается", self.html(response))


class StorageFailureTest(WebTestCase):
    """Повреждённое хранилище не роняет веб-слой."""

    def test_broken_json_shows_error_card(self) -> None:
        self.storage.write_text("[{", encoding="utf-8")

        code, html = self.body("/")

        self.assertEqual(code, 200)
        self.assertIn("Хранилище недоступно", html)
        self.assertIn("битый JSON", html)
        self.assertIn("flash--error", html)

    def test_broken_json_blocks_add_gracefully(self) -> None:
        self.storage.write_text("[{", encoding="utf-8")

        response = self.client.post("/add", data={"title": "Нельзя"}, follow_redirects=True)

        self.assertIn("Хранилище недоступно", self.html(response))
        self.assertEqual(self.storage.read_text(encoding="utf-8"), "[{")

    def test_root_not_a_list(self) -> None:
        self.storage.write_text('{"id": "1"}', encoding="utf-8")

        _, html = self.body("/")

        self.assertIn("Хранилище недоступно", html)


class ErrorPagesTest(WebTestCase):
    """Страницы ошибок."""

    def test_404(self) -> None:
        code, html = self.body("/definitely-not-here")

        self.assertEqual(code, 404)
        self.assertIn("Страница не найдена", html)
        self.assertIn("404", html)
        self.assertIn('href="/"', html)

    def test_405(self) -> None:
        response = self.client.get("/add")

        self.assertEqual(response.status_code, 405)
        self.assertIn("Метод не поддерживается", self.html(response))


class SplitAttachmentsTest(unittest.TestCase):
    """Служебные функции веб-слоя для вложений."""

    def test_splits_and_deduplicates(self) -> None:
        self.assertEqual(split_attachments("a.txt, b.png; a.txt"), ["a.txt", "b.png"])

    def test_strips_quotes_and_blanks(self) -> None:
        self.assertEqual(split_attachments(' "a.txt" , , b.txt '), ["a.txt", "b.txt"])

    def test_empty_input(self) -> None:
        self.assertEqual(split_attachments(""), [])
        self.assertEqual(split_attachments("   "), [])
        self.assertEqual(split_attachments(","), [])

    def test_fakepath_and_windows_separators_are_cut(self) -> None:
        self.assertEqual(safe_attachment_name("C:\fakepath\отчёт.pdf"), "отчёт.pdf")
        self.assertEqual(safe_attachment_name("/tmp/dir/photo.png"), "photo.png")

    def test_forbidden_symbols_are_replaced(self) -> None:
        self.assertEqual(safe_attachment_name('a<b>:c"|d?.txt'), "a_b__c__d_.txt")
        self.assertEqual(safe_attachment_name("   "), "")

    def test_kind_by_extension_is_case_insensitive(self) -> None:
        self.assertEqual(attachment_kind("report.PDF"), "pdf")
        self.assertEqual(attachment_kind("photo.PNG"), "image")
        self.assertEqual(attachment_kind("notes.md"), "text")
        self.assertEqual(attachment_kind("archive.zip"), "archive")
        self.assertEqual(attachment_kind("no_extension"), "other")

    def test_human_size(self) -> None:
        self.assertEqual(human_size(512), "512 Б")
        self.assertEqual(human_size(2048), "2.0 КБ")
        self.assertEqual(human_size(1536 * 1024), "1.5 МБ")


class AttachmentPreviewTest(WebTestCase):
    """GET /attachment/<имя> — файл отдаётся браузеру для предпросмотра."""

    def upload(self, name: str, payload: bytes = b"data") -> None:
        self.client.post(
            "/add",
            data={"title": "Файл", "attachment_files": (BytesIO(payload), name)},
            content_type="multipart/form-data",
        )

    def test_uploaded_file_is_stored_in_attachments_dir(self) -> None:
        self.upload("plan.pdf", b"%PDF-1.4")

        saved = self.attachments / "plan.pdf"
        self.assertTrue(saved.is_file(), "выбранный файл должен лежать в каталоге вложений")
        self.assertEqual(saved.read_bytes(), b"%PDF-1.4")
        self.assertEqual(self.stored()[0].attachments, ["plan.pdf"])

    def test_preview_is_inline_not_attachment(self) -> None:
        self.upload("plan.pdf", b"%PDF-1.4")

        response = self.client.get("/attachment/plan.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        disposition = response.headers.get("Content-Disposition", "")
        self.assertIn("inline", disposition)
        self.assertNotIn("attachment;", disposition)
        self.assertEqual(response.get_data(), b"%PDF-1.4")

    def test_text_preview_declares_utf8(self) -> None:
        self.upload("записка.md", "строка с кириллицей".encode("utf-8"))

        response = self.client.get("/attachment/" + urllib.parse.quote("записка.md"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("charset=utf-8", response.headers["Content-Type"])

    def test_image_preview_keeps_content_type(self) -> None:
        self.upload("shot.png", b"\x89PNG\r\n\x1a\n")

        response = self.client.get("/attachment/shot.png")

        self.assertEqual(response.mimetype, "image/png")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_missing_file_shows_ui_error_not_raw_text(self) -> None:
        response = self.client.get("/attachment/ghost.pdf")

        self.assertEqual(response.status_code, 404)
        html = self.html(response)
        self.assertIn("Файл не найден", html)
        self.assertIn("error-page", html)

    def test_path_traversal_is_not_served(self) -> None:
        self.storage.write_text("[]", encoding="utf-8")

        for candidate in ("..%2Fnotes.json", "notes.json", "..%2F..%2Fnotes.json"):
            with self.subTest(path=candidate):
                response = self.client.get(f"/attachment/{candidate}")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Заметка", self.html(response))

    def test_absolute_path_from_core_is_previewable(self) -> None:
        outside = Path(self._dir.name) / "вне-каталога.txt"
        outside.write_text("значение", encoding="utf-8")
        NoteManager(storage_path=self.storage).create_note(
            "Из CLI", "Текст", [str(outside)]
        )

        _, html = self.body(f"/note/{self.stored()[0].id}")
        link = re.search(r'href="(/attachment/[^"]+)"', html)
        self.assertIsNotNone(link, "в карточке должна быть ссылка на предпросмотр")

        response = self.client.get(link.group(1))  # type: ignore[union-attr]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "значение")

    def test_note_view_renders_attachment_cards(self) -> None:
        note_id = self.add_note(title="С файлами", attachments="plan.pdf, note.txt")

        _, html = self.body(f"/note/{note_id}")

        self.assertIn("attachment-cards", html)
        self.assertIn("attachment-card--pdf", html)
        self.assertIn("attachment-card--text", html)
        self.assertIn("/attachment/plan.pdf", html)
        self.assertIn('target="_blank"', html)
        self.assertIn("PDF", html)  # подпись расширения видна пользователю

    def test_index_links_attachments_to_preview(self) -> None:
        self.add_note(title="С файлами", attachments="plan.pdf")

        _, html = self.body("/")

        self.assertIn("/attachment/plan.pdf", html)
        self.assertIn("chip chip--file", html)

    def test_unavailable_attachment_is_marked(self) -> None:
        note_id = self.add_note(title="Без файла", attachments="ghost.pdf")

        _, html = self.body(f"/note/{note_id}")

        self.assertIn("attachment-card--missing", html)
        self.assertIn("Нет файла", html)


class AttachmentFormTest(WebTestCase):
    """Поле вложений заполняется только выбором файлов, без ручного ввода."""

    def test_no_manual_text_input_for_attachments(self) -> None:
        _, html = self.body("/")

        self.assertNotIn('placeholder="путь/имя через запятую"', html)
        self.assertIn('type="hidden"', html)
        self.assertIn('name="attachments"', html)
        self.assertIn('type="file"', html)
        self.assertIn("Выбрать файлы", html)

    def test_edit_form_lists_existing_attachments_as_chips(self) -> None:
        note_id = self.add_note(title="С файлами", attachments="plan.pdf")

        _, html = self.body(f"/note/{note_id}")

        self.assertIn('class="attachment-list"', html)
        self.assertIn('data-name="plan.pdf"', html)
        self.assertIn("attachment-chip__remove", html)

    def test_upload_and_kept_chips_merge_without_duplicates(self) -> None:
        self.client.post(
            "/add",
            data={
                "title": "Микс",
                "attachments": "a.txt,photo.png",
                "attachment_files": (BytesIO(b"data"), "photo.png"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(self.stored()[0].attachments, ["a.txt", "photo.png"])

    def test_removing_chip_clears_attachment_list(self) -> None:
        """Крестик в чипе = пустое скрытое поле: вложение уходит из заметки."""
        note_id = self.add_note(title="Заметка", attachments="a.txt")
        self.dismiss_flash()

        self.client.post(
            f"/edit/{note_id}",
            data={"title": "Заметка", "content": "", "attachments": ""},
        )

        self.assertEqual(self.stored()[0].attachments, [])


class IntegrationWithCoreTest(WebTestCase):
    """Веб и ядро видят одни и те же данные."""

    def test_notes_created_in_core_are_visible(self) -> None:
        NoteManager(storage_path=self.storage).create_note("Из ядра", "Текст")

        _, html = self.body("/")

        self.assertIn("Из ядра", html)

    def test_web_changes_are_visible_in_core(self) -> None:
        note_id = self.add_note(title="Из веба", content="Текст")

        note = NoteManager(storage_path=self.storage).get_note_by_id(note_id)
        self.assertIsNotNone(note)
        assert note is not None
        self.assertEqual(note.title, "Из веба")

    def test_storage_file_contains_list_of_objects(self) -> None:
        self.add_note(title="Проверка файла")

        raw = self.storage.read_text(encoding="utf-8")
        self.assertIn("Проверка файла", raw)
        self.assertTrue(raw.lstrip().startswith("["))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
