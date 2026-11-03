"""Flask-приложение NoteMaster — веб-слой над ядром.

Слой не содержит бизнес-логики и не работает с JSON напрямую: все операции
выполняются через ``NoteManager`` из ``app.core``. Единая точка правды — файл
``data/notes.json``.

Запуск:
    python web/app.py
    python -m web.app                       # модуль сам вызывает main()
    flask --app web.app run                 # через Flask CLI

Аргументы запуска: --host, --port, --debug, --storage PATH
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from flask import (  # type: ignore[import-untyped]  # noqa: E402
    Flask,
    Response,
    flash,
    get_flashed_messages,
    g,
    redirect,
    render_template,
    request,
    url_for,
)  # type: ignore[import-untyped]

# Прямой запуск файла (python web/app.py): добавляем корень проекта в sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import (  # noqa: E402 — импорт после bootstrap sys.path
    EmptyStorageError,
    Note,
    NoteManager,
    NoteNotFoundError,
    NoteValidationError,
    StorageError,
)

LOGGER_NAME = "notemaster.web"
logger = logging.getLogger(LOGGER_NAME)


# ------------------------------------------------------------------ presentation
def format_timestamp(value: str) -> str:
    """ISO-8601 -> «03.11.2026 12:20». Некорректное значение не ломает страницу."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%d.%m.%Y %H:%M")


def short_id(note_id: str, width: int = 8) -> str:
    """Сокращение UUID для подписи под карточкой."""
    return note_id if len(note_id) <= width else note_id[:width]


def parse_attachments(text: str) -> list[str]:
    """Строка «a.txt, b.png; c.md» -> список уникальных путей."""
    result: list[str] = []
    for part in re.split(r"[,;]", text or ""):
        path = part.strip().strip("\"'")
        if path and path not in result:
            result.append(path)
    return result


def collect_attachments() -> list[str]:
    """Вложения из текстового поля плюс имена выбранных в браузере файлов."""
    attachments = parse_attachments(request.form.get("attachments", ""))
    for uploaded in request.files.getlist("attachment_files"):
        name = uploaded.filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if name and name not in attachments:
            attachments.append(name)
    return attachments


# ------------------------------------------------------------------ core access
def _manager(storage_path: str | os.PathLike[str] | None = None) -> NoteManager:
    """Менеджер на запрос: состояние всегда читается из файла, кэш не устаревает."""
    return NoteManager(storage_path=storage_path)


def _collect_changes(note: Note) -> dict[str, Any]:
    """Только реально изменённые поля — частичное обновление выполняет ядро."""
    changes: dict[str, Any] = {}

    title = (request.form.get("title") or "").strip()
    if title != note.title:
        # Пустой заголовок тоже передаётся ядру: правило «title не пустой» живёт там.
        changes["title"] = title

    content = request.form.get("content") or ""
    if content != note.content:
        changes["content"] = content

    attachments = collect_attachments()
    if attachments != note.attachments:
        changes["attachments"] = attachments

    return changes


# ------------------------------------------------------------------ factory
def create_app(
    *,
    storage_path: str | os.PathLike[str] | None = None,
    secret_key: str | None = None,
) -> Flask:
    """Создаёт приложение Flask, подключённое к ядру NoteManager."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = (
        secret_key or os.environ.get("NOTEMASTER_SECRET") or secrets.token_hex(16)
    )

    configure_logging()

    # ----------------------------------------------------------------- запросы
    @app.before_request
    def _start_timer() -> None:
        g.started_at = time.perf_counter()

    @app.after_request
    def _log_request(response: Response) -> Response:
        started = getattr(g, "started_at", time.perf_counter())
        logger.info(
            "%s %s -> %s (%.1f ms)",
            request.method,
            request.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.context_processor
    def _inject_globals() -> dict[str, Any]:
        return {
            "flashes": get_flashed_messages(with_categories=True),
            "format_timestamp": format_timestamp,
            "short_id": short_id,
            "current_year": datetime.now().year,
        }

    # ------------------------------------------------------------------- routes
    @app.route("/")
    def index() -> str:
        """Главная: карточки заметок, кнопка добавления, действия над заметками."""
        form: dict[str, str] | None = None
        try:
            notes = _manager(storage_path).get_all_notes()
        except StorageError as exc:
            logger.error("Хранилище недоступно: %s", exc)
            flash(f"Хранилище недоступно: {exc}", "error")
            notes = []

        return render_template("index.html", notes=notes, form=form)

    @app.route("/note/<note_id>")
    def note_view(note_id: str) -> Response | str:
        """Просмотр одной заметки."""
        try:
            manager = _manager(storage_path)
        except StorageError as exc:
            logger.error("Хранилище недоступно: %s", exc)
            flash(f"Хранилище недоступно: {exc}", "error")
            return redirect(url_for("index"))

        note = manager.get_note_by_id(note_id.strip())
        if note is None:
            logger.warning("Заметка не найдена: id=%r", note_id)
            flash(f"Заметка «{note_id}» не найдена", "error")
            return redirect(url_for("index"))

        return render_template("note.html", note=note, form=None)

    @app.route("/add", methods=["POST"])
    def add_note() -> Response | str | tuple[str, int]:
        """Создание заметки: title обязателен, проверка — в ядре."""
        title = (request.form.get("title") or "").strip()
        content = request.form.get("content") or ""
        attachments = collect_attachments()

        try:
            note = _manager(storage_path).create_note(title, content, attachments)
        except NoteValidationError as exc:
            logger.warning("ADD отклонён: %s", exc)
            flash(f"Не удалось создать заметку: {exc}", "error")
            return render_template(
                "index.html", notes=_safe_notes(), form=_form_snapshot()
            ), 422
        except StorageError as exc:
            logger.error("ADD: хранилище недоступно: %s", exc)
            flash(f"Хранилище недоступно: {exc}", "error")
            return redirect(url_for("index"))

        logger.info("Web add: создана заметка id=%s title=%r", note.id, note.title)
        flash(f"Заметка «{note.title}» создана", "success")
        return redirect(url_for("note_view", note_id=note.id))

    @app.route("/edit/<note_id>", methods=["POST"])
    def edit_note(note_id: str) -> Response | str:
        """Редактирование заметки по id: изменяются только переданные поля."""
        try:
            manager = _manager(storage_path)
        except StorageError as exc:
            logger.error("EDIT: хранилище недоступно: %s", exc)
            flash(f"Хранилище недоступно: {exc}", "error")
            return redirect(url_for("index"))

        note = manager.get_note_by_id(note_id.strip())
        if note is None:
            logger.warning("EDIT: заметка не найдена: id=%r", note_id)
            flash(f"Заметка «{note_id}» не найдена", "error")
            return redirect(url_for("index"))

        changes = _collect_changes(note)
        if not changes:
            flash("Изменений не найдено — заметка осталась прежней", "warning")
            return redirect(url_for("note_view", note_id=note.id))

        try:
            updated = manager.update_note(note.id, **changes)
        except (NoteValidationError, NoteNotFoundError, EmptyStorageError) as exc:
            logger.warning("EDIT %s отклонён: %s", note_id, exc)
            flash(f"Не удалось обновить заметку: {exc}", "error")
            return redirect(url_for("note_view", note_id=note.id))
        except StorageError as exc:
            logger.error("EDIT %s: хранилище недоступно: %s", note_id, exc)
            flash(f"Хранилище недоступно: {exc}", "error")
            return redirect(url_for("note_view", note_id=note.id))

        logger.info(
            "Web edit: обновлена заметка id=%s поля=%s", updated.id, sorted(changes)
        )
        flash(f"Заметка «{updated.title}» обновлена", "success")
        return redirect(url_for("note_view", note_id=updated.id))

    @app.route("/delete/<note_id>", methods=["POST"])
    def delete_note(note_id: str) -> Response | str:
        """Удаление заметки (подтверждение выполняется на стороне JS)."""
        try:
            manager = _manager(storage_path)
        except StorageError as exc:
            logger.error("DELETE: хранилище недоступно: %s", exc)
            flash(f"Хранилище недоступно: {exc}", "error")
            return redirect(url_for("index"))

        try:
            removed = manager.delete_note(note_id.strip())
        except EmptyStorageError:
            flash("Заметок нет — удалять нечего", "warning")
            return redirect(url_for("index"))
        except (NoteNotFoundError, NoteValidationError) as exc:
            logger.warning("DELETE %s: %s", note_id, exc)
            flash(f"Не удалось удалить: {exc}", "error")
            return redirect(url_for("index"))
        except StorageError as exc:
            logger.error("DELETE %s: хранилище недоступно: %s", note_id, exc)
            flash(f"Хранилище недоступно: {exc}", "error")
            return redirect(url_for("index"))

        logger.info(
            "Web delete: удалена заметка id=%s title=%r", removed.id, removed.title
        )
        flash(f"Заметка «{removed.title}» удалена", "success")
        return redirect(url_for("index"))

    # ------------------------------------------------------------------ errors
    @app.errorhandler(404)
    def handle_404(_error: Any) -> tuple[str, int]:
        logger.warning("404: %s %s", request.method, request.path)
        return render_template("error.html", code=404, title="Страница не найдена"), 404

    @app.errorhandler(405)
    def handle_405(_error: Any) -> tuple[str, int]:
        logger.warning("405: %s %s", request.method, request.path)
        return (
            render_template(
                "error.html",
                code=405,
                title="Метод не поддерживается",
                detail=(
                    f"{request.method} не разрешён для {request.path}. "
                    "Изменения заметок выполняются только через формы (POST)."
                ),
            ),
            405,
        )

    @app.errorhandler(500)
    def handle_500(error: Any) -> tuple[str, int]:
        logger.error("500: %s", error)
        return render_template("error.html", code=500, title="Внутренняя ошибка"), 500

    return app


# ------------------------------------------------------------------ helpers
def _safe_notes(storage_path: str | os.PathLike[str] | None = None) -> list[Note]:
    """Список заметок для повторного рендера формы; ошибка хранилища не роняет страницу."""
    try:
        return _manager(storage_path).get_all_notes()
    except StorageError as exc:
        logger.error("Хранилище недоступно: %s", exc)
        flash(f"Хранилище недоступно: {exc}", "error")
        return []


def _form_snapshot() -> dict[str, str]:
    """Введённые в форме значения, чтобы пользователь их не потерял."""
    return {
        "title": request.form.get("title", ""),
        "content": request.form.get("content", ""),
        "attachments": ", ".join(collect_attachments()),
    }


# ------------------------------------------------------------------- logging
def configure_logging(*, quiet: bool = False) -> None:
    """Логирует HTTP-запросы и ошибки в stderr и, при наличии переменной, в файл."""
    web_logger = logging.getLogger(LOGGER_NAME)
    web_logger.setLevel(logging.WARNING if quiet else logging.INFO)
    web_logger.propagate = False

    if not web_logger.handlers:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        web_logger.addHandler(console)

    log_file = os.environ.get("NOTEMASTER_LOG_FILE")
    if log_file and not any(
        isinstance(handler, logging.FileHandler) for handler in web_logger.handlers
    ):
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
            )
            web_logger.addHandler(file_handler)
        except OSError as exc:
            print(f"Не удалось открыть файл логов {log_file}: {exc}", file=sys.stderr)


# ------------------------------------------------------------------- запуск
def main(argv: Sequence[str] | None = None) -> None:
    """Точка входа: ``python web/app.py --port 8080 --debug``."""
    parser = argparse.ArgumentParser(
        prog="notemaster-web", description="Веб-интерфейс NoteMaster"
    )
    parser.add_argument("--host", default="127.0.0.1", help="адрес (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="порт (по умолчанию 5000)")
    parser.add_argument("--debug", action="store_true", help="режим отладки Flask")
    parser.add_argument("--storage", default=None, metavar="PATH", help="путь к notes.json")
    args = parser.parse_args(argv)

    app = create_app(storage_path=args.storage)
    print(f"NoteMaster Web: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


#: Экземпляр для ``flask --app web.app run`` и ``python -m web.app``.
app = create_app()


if __name__ == "__main__":
    main()
