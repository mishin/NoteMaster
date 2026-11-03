"""CLI NoteMaster на argparse.

Слой знает только о ``NoteManager`` и терминальном представлении: аргументы,
вызов метода ядра, понятный вывод. JSON-файл напрямую не читается и не
пишется — этим занимается driver.

Запуск:
    python -m app.cli add "Заголовок" --content "Текст"
    python -m app.cli list
    python -m app.cli edit <id> --title "Новый заголовок"
    python -m app.cli delete <id> --yes
    python -m app.cli --ui
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

# Прямой запуск файла: python app/cli.py ...
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import __version__
from app.console import (
    InputError,
    confirm,
    error,
    muted,
    print_note,
    render_notes_table,
    resolve_note_id,
    section,
    success,
    warning,
)
from app.core import NoteManager, NoteMasterError

APP_LOGGER_NAME = "app"
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ logging
def reset_logging() -> None:
    """Закрывает обработчики, созданные CLI (нужно для повторных запусков и тестов)."""
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    for handler in list(app_logger.handlers):
        if getattr(handler, "_notemaster", False):
            app_logger.removeHandler(handler)
            handler.close()


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    log_file: str | None = None,
) -> None:
    """Настраивает логгер приложения, не вмешиваясь в корневой логгер.

    Консоль по умолчанию показывает только предупреждения и ошибки, чтобы не
    дублировать вывод CLI. Файл (если указан) получает все действия пользователя.
    """
    reset_logging()

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    app_logger.setLevel(logging.DEBUG)
    app_logger.propagate = False

    console_level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.WARNING
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    console._notemaster = True  # type: ignore[attr-defined]
    app_logger.addHandler(console)

    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
        except OSError as exc:
            error(f"Не удалось открыть файл логов {log_file}: {exc}")
        else:
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
            )
            file_handler._notemaster = True  # type: ignore[attr-defined]
            app_logger.addHandler(file_handler)


# ------------------------------------------------------------------ helpers
def _stdin_is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _clean_attachments(values: Sequence[str]) -> list[str]:
    """Убирает пустые пути и дубликаты, сохраняя порядок."""
    cleaned: list[str] = []
    for value in values:
        path = value.strip()
        if not path:
            raise InputError("Путь вложения не может быть пустым.")
        if path not in cleaned:
            cleaned.append(path)
    return cleaned


# ------------------------------------------------------------------ команды
def cmd_add(args: argparse.Namespace, manager: NoteManager) -> int:
    attachments = _clean_attachments(args.attachment or [])
    note = manager.create_note(args.title, args.content or "", attachments)

    logger.info("CLI add: создана заметка id=%s title=%r", note.id, note.title)
    success(f"Заметка создана: {note.id}")
    print_note(note)
    return 0


def cmd_list(args: argparse.Namespace, manager: NoteManager) -> int:
    notes = manager.get_all_notes()

    if args.json:
        print(json.dumps([note.to_dict() for note in notes], ensure_ascii=False, indent=2))
        return 0

    if not notes:
        warning('Заметок пока нет. Добавьте первую: python -m app.cli add "Заголовок"')
        return 0

    section(f"Заметки: {len(notes)}")
    print(render_notes_table(notes))
    muted("В командах используйте номер из колонки #, полный id или уникальный префикс.")
    return 0


def cmd_show(args: argparse.Namespace, manager: NoteManager) -> int:
    notes = manager.get_all_notes()
    note_id = resolve_note_id(notes, args.id)
    note = manager.get_note_by_id(note_id)
    if note is None:  # защита от изменения хранилища между вызовами
        error(f"Заметка с id={note_id!r} не найдена.")
        return 1

    if args.json:
        print(json.dumps(note.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print_note(note)
    return 0


def cmd_edit(args: argparse.Namespace, manager: NoteManager) -> int:
    notes = manager.get_all_notes()
    note_id = resolve_note_id(notes, args.id)
    note = manager.get_note_by_id(note_id)
    if note is None:
        error(f"Заметка с id={note_id!r} не найдена.")
        return 1

    if args.no_attachments and args.attachment:
        error("Нельзя одновременно использовать --attachment и --no-attachments.")
        return 2

    changes: dict[str, object] = {}
    if args.title is not None:
        changes["title"] = args.title
    if args.content is not None:
        changes["content"] = args.content
    if args.no_attachments:
        changes["attachments"] = []
    elif args.attachment is not None:
        changes["attachments"] = _clean_attachments(args.attachment)

    if not changes:
        warning("Не передано ни одного поля для изменения.")
        muted(
            "Используйте --title, --content, --attachment или --no-attachments, "
            "либо интерактивный режим --ui."
        )
        return 2

    section("Текущее значение")
    print_note(note)

    updated = manager.update_note(note.id, **changes)
    logger.info("CLI edit: обновлена заметка id=%s поля=%s", updated.id, sorted(changes))
    success("Заметка обновлена.")
    print_note(updated)
    return 0


def cmd_delete(args: argparse.Namespace, manager: NoteManager) -> int:
    notes = manager.get_all_notes()
    note_id = resolve_note_id(notes, args.id)
    note = manager.get_note_by_id(note_id)
    if note is None:
        error(f"Заметка с id={note_id!r} не найдена.")
        return 1

    section("Удаление заметки")
    print_note(note)

    if not args.yes:
        if not _stdin_is_interactive():
            error("Нет терминала для подтверждения. Используйте флаг --yes.")
            return 2
        if not confirm(f"Удалить заметку «{note.title}»?", default=False):
            warning("Удаление отменено.")
            return 0

    removed = manager.delete_note(note.id)
    logger.info("CLI delete: удалена заметка id=%s title=%r", removed.id, removed.title)
    success(f"Заметка удалена: {removed.id}")
    return 0


def cmd_demo(args: argparse.Namespace, manager: NoteManager) -> int:
    """Короткий сценарий: создание, частичное обновление, удаление."""
    section("Демо NoteMaster")

    note = manager.create_note(
        "Демо-заметка",
        "Эта заметка создана демонстрационной командой.",
        ["readme.md"],
    )
    logger.info("CLI demo: создана заметка id=%s", note.id)
    success(f"Создана: {note.id}")
    print_note(note)

    updated = manager.update_note(note.id, content="Текст обновлён демо-командой.")
    success("Обновлена частично: изменён только content.")
    print_note(updated)

    manager.delete_note(note.id)
    success("Удалена. Остальные заметки не затронуты.")
    return 0


# ------------------------------------------------------------------ argparse
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notemaster",
        description="NoteMaster — управление заметками из терминала.",
        epilog="Интерактивный режим: python -m app.cli --ui",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ui", action="store_true", help="запустить интерактивный режим")
    parser.add_argument("--storage", metavar="PATH", default=None, help="путь к notes.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробное логирование")
    parser.add_argument("-q", "--quiet", action="store_true", help="только ошибки в логах")
    parser.add_argument("--log-file", metavar="PATH", default=None, help="писать логи в файл")
    parser.add_argument("--version", action="version", version=f"NoteMaster {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="КОМАНДА")

    p_add = sub.add_parser("add", help="создать заметку")
    p_add.add_argument("title", help="заголовок (обязательно)")
    p_add.add_argument("-c", "--content", default=None, help="текст заметки")
    p_add.add_argument(
        "-a",
        "--attachment",
        action="append",
        default=None,
        metavar="PATH",
        help="путь к вложению (можно повторять)",
    )
    p_add.set_defaults(handler=cmd_add)

    p_list = sub.add_parser("list", help="показать все заметки")
    p_list.add_argument("--json", action="store_true", help="вывести в формате JSON")
    p_list.set_defaults(handler=cmd_list)

    p_show = sub.add_parser("show", aliases=["get"], help="показать заметку по id")
    p_show.add_argument("id", help="номер из списка, полный id или уникальный префикс")
    p_show.add_argument("--json", action="store_true", help="вывести в формате JSON")
    p_show.set_defaults(handler=cmd_show)

    p_edit = sub.add_parser("edit", aliases=["update"], help="изменить заметку")
    p_edit.add_argument("id", help="номер из списка, полный id или уникальный префикс")
    p_edit.add_argument("-t", "--title", default=None, help="новый заголовок")
    p_edit.add_argument("-c", "--content", default=None, help="новый текст")
    p_edit.add_argument(
        "-a",
        "--attachment",
        action="append",
        default=None,
        metavar="PATH",
        help="новые вложения (заменяют текущие, можно повторять)",
    )
    p_edit.add_argument(
        "--no-attachments",
        action="store_true",
        help="очистить список вложений",
    )
    p_edit.set_defaults(handler=cmd_edit)

    p_delete = sub.add_parser("delete", aliases=["remove"], help="удалить заметку")
    p_delete.add_argument("id", help="номер из списка, полный id или уникальный префикс")
    p_delete.add_argument("-y", "--yes", action="store_true", help="без подтверждения")
    p_delete.set_defaults(handler=cmd_delete)

    p_demo = sub.add_parser("demo", help="демонстрационный сценарий")
    p_demo.set_defaults(handler=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI. Коды: 0 — успех, 1 — ошибка ядра, 2 — ошибка ввода."""
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)

    try:
        manager = NoteManager(storage_path=args.storage)

        if args.ui:
            from app import ui

            return ui.run(manager)

        if not args.command:
            parser.print_help()
            return 0

        return args.handler(args, manager)

    except InputError as exc:
        logger.warning("Ошибка ввода: %s", exc)
        error(str(exc))
        return 2
    except NoteMasterError as exc:
        logger.error("Ошибка ядра: %s", exc)
        error(str(exc))
        return 1
    except KeyboardInterrupt:  # pragma: no cover - интерактивный пользователь
        print()
        warning("Прервано пользователем.")
        return 130
    except Exception as exc:  # pragma: no cover - последняя линия защиты
        logger.exception("Непредвиденная ошибка CLI")
        error(f"Непредвиденная ошибка: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
