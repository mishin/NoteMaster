"""Flask-приложение NoteMaster — веб-слой над ядром.

Слой не содержит бизнес-логики и не работает с JSON напрямую: все операции
выполняются через ``NoteManager`` из ``app.core``. Единая точка правды — файл
``data/notes.json``.

Запуск:
    python web/app.py
    python -m web.app                       # модуль сам вызывает main()
    flask --app web.app run                 # через Flask CLI

Аргументы запуска: --host, --port, --debug, --storage PATH,
--attachments-dir PATH, --max-upload MB

Вложения выбираются только через диалог браузера (кнопка «Выбрать файлы»):
в заметку попадает имя файла, сам файл сохраняется в каталог вложений, а
ссылка /attachment/<имя> отдаёт его браузеру для предпросмотра.
"""

from __future__ import annotations

import argparse
import logging
import mimetypes
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
    send_file,
    url_for,
)  # type: ignore[import-untyped]
from flask.typing import ResponseReturnValue

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


#: Чем является вложение по расширению: kind -> набор расширений.
EXTENSION_KINDS: dict[str, tuple[str, ...]] = {
    "image": ("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif", "tiff"),
    "pdf": ("pdf",),
    "text": (
        "txt", "md", "log", "csv", "json", "yaml", "yml", "toml", "ini", "cfg",
        "xml", "html", "css", "js", "ts", "py", "sql", "sh", "bat",
    ),
    "doc": ("doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "epub"),
    "archive": ("zip", "rar", "7z", "tar", "gz", "bz2", "xz"),
    "audio": ("mp3", "wav", "ogg", "flac", "aac", "m4a"),
    "video": ("mp4", "webm", "mov", "avi", "mkv", "m4v"),
}

#: Иконка для карточки вложения.
KIND_ICONS: dict[str, str] = {
    "image": "🖼",
    "pdf": "📕",
    "text": "📄",
    "doc": "📘",
    "archive": "🗜",
    "audio": "🎵",
    "video": "🎬",
    "other": "📎",
}

#: Типы, для которых важно явно указать кодировку, иначе кириллица «съедается».
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_EXACT = ("application/json", "application/javascript", "application/xml")


def attachment_kind(name: str) -> str:
    """Вид вложения по расширению — определяет иконку и стиль карточки."""
    extension = Path(name).suffix.lower().lstrip(".")
    for kind, extensions in EXTENSION_KINDS.items():
        if extension in extensions:
            return kind
    return "other"


def guess_mimetype(path: Path) -> str:
    """MIME-тип файла по имени: браузер по нему решает, показывать или скачивать."""
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def human_size(num_bytes: int) -> str:
    """Размер файла в человекочитаемом виде: 1.4 МБ."""
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"  # pragma: no cover


def split_attachments(text: str) -> list[str]:
    """Список имён из скрытого поля формы (через запятую) -> уникальные имена."""
    result: list[str] = []
    for part in re.split(r"[,;]", text or ""):
        name = part.strip().strip("\"'")
        if name and name not in result:
            result.append(name)
    return result


#: Обратный слэш как есть, чтобы не путать читателя четырьмя слепами в исходнике.
BACKSLASH = chr(92)


def safe_attachment_name(filename: str) -> str:
    """Имя файла так, как его отдаёт браузер: только basename, без пути.

    Настоящий локальный путь браузер не передаёт (в value остаётся служебная
    приставка fakepath), поэтому в заметке сохраняется именно имя файла.
    Запрещённые в Windows символы заменяются, чтобы файл можно было положить
    на диск и отдать по ссылке предпросмотра.
    """
    name = (filename or "").replace(BACKSLASH, "/").rsplit("/", 1)[-1].strip().strip('"')
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).rstrip(". ")
    return name[:120]


def resolve_attachment(value: str, roots: Sequence[Path]) -> Path | None:
    """Реальный файл вложения или None.

    Абсолютный путь берётся как есть (так заметку можно создать из CLI),
    относительный ищется по очереди в разрешённых корнях. Выход за пределы
    корня (``../``) отсекается — наружу отдаётся только содержимое roots.
    """
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None

    for root in roots:
        try:
            target = (root / candidate).resolve()
            target.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if target.is_file():
            return target
    return None


def attachment_meta(value: str, roots: Sequence[Path]) -> dict[str, Any]:
    """Данные для карточки вложения в UI: подпись, иконка, размер, ссылка.

    Вычисляется на рендере: файл мог появиться или исчезнуть на диске, а
    бизнес-правил в этом нет — заметка хранит адрес как есть.
    """
    path = resolve_attachment(value, roots)
    extension = Path(value).suffix.lower().lstrip(".")
    kind = attachment_kind(value)

    size: str | None = None
    if path is not None:
        try:
            size = human_size(path.stat().st_size)
        except OSError:  # pragma: no cover - гонка при удалении файла
            size = None

    return {
        "name": value,
        "label": Path(value).name,
        "extension": extension.upper() if extension else "FILE",
        "kind": kind,
        "icon": KIND_ICONS[kind],
        "size": size,
        "available": path is not None,
        "url": url_for("attachment_preview", name=value),
    }


def collect_attachments(attachments_dir: Path) -> list[str]:
    """Вложения из формы: сохранённые чипы плюс файлы, выбранные в диалоге.

    Выбранный браузером файл копируется в ``attachments_dir`` — иначе о нём
    известно только имя, и предпросмотр открыть нельзя. В JSON уходит только
    имя (адрес) файла, его содержимое в заметке не хранится.
    """
    attachments = split_attachments(request.form.get("attachments", ""))

    for uploaded in request.files.getlist("attachment_files"):
        name = safe_attachment_name(uploaded.filename)
        if not name:
            continue
        target = attachments_dir / name
        if not target.exists():
            try:
                attachments_dir.mkdir(parents=True, exist_ok=True)
                uploaded.save(target)
            except OSError as exc:
                logger.error("Не удалось сохранить вложение %r: %s", name, exc)
                flash(f"Не удалось сохранить файл «{name}»: {exc}", "error")
                continue
        if name not in attachments:
            attachments.append(name)

    return attachments


# ------------------------------------------------------------------ core access
def _manager(storage_path: str | os.PathLike[str] | None = None) -> NoteManager:
    """Менеджер на запрос: состояние всегда читается из файла, кэш не устаревает."""
    return NoteManager(storage_path=storage_path)


def _attachments_root(storage_path: str | os.PathLike[str] | None) -> Path:
    """Каталог вложений по умолчанию — рядом с хранилищем заметок (data/attachments)."""
    manager = _manager(storage_path)
    base = manager.storage_path.parent if manager.storage_path else Path.cwd()
    return base / "attachments"


def _collect_changes(
    note: Note, attachments_dir: Path
) -> dict[str, Any]:
    """Только реально изменённые поля — частичное обновление выполняет ядро."""
    changes: dict[str, Any] = {}

    title = (request.form.get("title") or "").strip()
    if title != note.title:
        # Пустой заголовок тоже передаётся ядру: правило «title не пустой» живёт там.
        changes["title"] = title

    content = request.form.get("content") or ""
    if content != note.content:
        changes["content"] = content

    attachments = collect_attachments(attachments_dir)
    if attachments != note.attachments:
        changes["attachments"] = attachments

    return changes


# ------------------------------------------------------------------ factory
def create_app(
    *,
    storage_path: str | os.PathLike[str] | None = None,
    secret_key: str | None = None,
    attachments_dir: str | os.PathLike[str] | None = None,
    max_upload_mb: int = 32,
) -> Flask:
    """Создаёт приложение Flask, подключённое к ядру NoteManager.

    ``attachments_dir`` — каталог, куда кладутся выбранные браузером файлы и
    откуда они отдаются на предпросмотр. По умолчанию ``data/attachments``.
    """
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = (
        secret_key or os.environ.get("NOTEMASTER_SECRET") or secrets.token_hex(16)
    )
    app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024

    files_dir = Path(attachments_dir) if attachments_dir else _attachments_root(storage_path)
    # Относительный адрес из заметки ищется только в каталоге вложений: раздача
    # произвольных файлов проекта (notes.json, исходники) не входит в задачу.
    # Абсолютный путь используется как есть — так работают заметки из CLI.
    preview_roots: list[Path] = [files_dir]

    configure_logging()
    logger.info("Вложения: каталог %s", files_dir)

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
            "attachment_meta": lambda value: attachment_meta(value, preview_roots),
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
    def note_view(note_id: str) -> ResponseReturnValue:
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
    def add_note() -> ResponseReturnValue:
        """Создание заметки: title обязателен, проверка — в ядре."""
        title = (request.form.get("title") or "").strip()
        content = request.form.get("content") or ""
        attachments = collect_attachments(files_dir)

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
    def edit_note(note_id: str) -> ResponseReturnValue:
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

        changes = _collect_changes(note, files_dir)
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
    def delete_note(note_id: str) -> ResponseReturnValue:
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

    @app.route("/attachment/<path:name>")
    def attachment_preview(name: str) -> ResponseReturnValue:
        """Предпросмотр вложения: браузер сам показывает PDF, картинку, текст.

        `as_attachment=False` даёт Content-Disposition: inline — файл открывается
        во вкладке, а не скачивается. Приложение локальное и однопользовательское,
        поэтому отдаём файл как есть: браузер сам решает, что он умеет показывать.
        """
        path = resolve_attachment(name, preview_roots)
        if path is None:
            logger.warning("PREVIEW: файл не найден: %r", name)
            return (
                render_template(
                    "error.html",
                    code=404,
                    title="Файл не найден",
                    detail=(
                        f"Вложение «{name}» недоступно для предпросмотра. "
                        f"Файлы, выбранные через «Выбрать файлы», лежат в {files_dir}; "
                        "абсолютный путь из заметки открывается как есть."
                    ),
                ),
                404,
            )

        mimetype = guess_mimetype(path)
        logger.info("PREVIEW: %s -> %s (%s)", name, path.name, mimetype)
        response = send_file(
            path, as_attachment=False, mimetype=mimetype, conditional=True
        )
        if mimetype.startswith("text/"):
            # Без явной кодировки кириллица в .txt/.md рисуется «кракозябрами».
            response.headers["Content-Type"] = f"{mimetype}; charset=utf-8"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # ------------------------------------------------------------------ errors
    @app.errorhandler(413)
    def handle_413(_error: Any) -> tuple[str, int]:
        logger.warning("413: %s %s — превышен лимит запроса", request.method, request.path)
        return (
            render_template(
                "error.html",
                code=413,
                title="Слишком большой файл",
                detail=(
                    f"Форма с вложениями весит больше {max_upload_mb} МБ. "
                    "Выберите файлы меньшего размера или увеличьте лимит "
                    "аргументом --max-upload."
                ),
            ),
            413,
        )

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
    """Введённые в форме значения, чтобы пользователь их не потерял.

    Файлы из request.files не сохраняются повторно — список имён остаётся тем же,
    что ушёл в ядро до ошибки валидации.
    """
    names = split_attachments(request.form.get("attachments", ""))
    for uploaded in request.files.getlist("attachment_files"):
        name = safe_attachment_name(uploaded.filename)
        if name and name not in names:
            names.append(name)

    return {
        "title": request.form.get("title", ""),
        "content": request.form.get("content", ""),
        "attachments": ",".join(names),
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
    parser.add_argument(
        "--storage",
        default=None,
        metavar="PATH",
        help="путь к notes.json (по умолчанию data/notes.json)",
    )
    parser.add_argument(
        "--attachments-dir",
        default=None,
        metavar="PATH",
        help="каталог вложений (по умолчанию data/attachments)",
    )
    parser.add_argument(
        "--max-upload",
        type=int,
        default=32,
        metavar="MB",
        help="максимальный размер формы с файлами в МБ (по умолчанию 32)",
    )
    args = parser.parse_args(argv)

    app = create_app(
        storage_path=args.storage,
        attachments_dir=args.attachments_dir,
        max_upload_mb=args.max_upload,
    )
    print(f"NoteMaster Web: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


#: Экземпляр для ``flask --app web.app run`` и ``python -m web.app``.
app = create_app()


if __name__ == "__main__":
    main()
