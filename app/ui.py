"""Интерактивный режим NoteMaster.

Цикл ``while True`` поверх тех же операций ядра, что и CLI. Здесь живут только
подсказки и сценарии ввода; бизнес-правила остаются в ``NoteManager``.
"""

from __future__ import annotations

import logging
from typing import Callable

from app import __version__
from app.console import (
    InputError,
    ask_attachments,
    ask_multiline,
    ask_optional_text,
    ask_text,
    confirm,
    error,
    info,
    muted,
    print_note,
    render_notes_table,
    resolve_note_id,
    section,
    success,
    warning,
)
from app.core import Note, NoteManager, NoteMasterError

logger = logging.getLogger(__name__)

InputFunc = Callable[[str], str]

PROMPT = "NoteMaster> "
ID_PROMPT = "номер или id заметки"

HELP_ROWS: tuple[tuple[str, str], ...] = (
    ("add", "создать заметку (пошаговый ввод)"),
    ("list", "показать все заметки"),
    ("show [N|id]", "показать заметку"),
    ("edit [N|id]", "изменить заметку (Enter — оставить поле)"),
    ("delete [N|id]", "удалить заметку с подтверждением"),
    ("help", "показать эту справку"),
    ("exit", "выйти из интерактивного режима"),
)

ALIASES: dict[str, str] = {
    "add": "add",
    "new": "add",
    "list": "list",
    "ls": "list",
    "show": "show",
    "get": "show",
    "edit": "edit",
    "update": "edit",
    "delete": "delete",
    "remove": "delete",
    "del": "delete",
    "help": "help",
    "?": "help",
    "h": "help",
    "exit": "exit",
    "quit": "exit",
    "q": "exit",
}


# ------------------------------------------------------------------ вывод
def print_help() -> None:
    section("Команды")
    for name, description in HELP_ROWS:
        print(f"  {name:<14} {description}")
    muted("N — номер из колонки # в списке (например: show 2, delete 3).")
    muted("Вместо номера можно указать полный id или уникальный префикс.")


def _print_banner(manager: NoteManager) -> None:
    section(f"NoteMaster {__version__} — интерактивный режим")
    storage = manager.storage_path
    muted(f"Хранилище: {storage if storage is not None else '(не файловое)'}")
    muted("help — список команд, exit — выход.")


# ------------------------------------------------------------------ операции
def add_note_interactive(manager: NoteManager, *, input_func: InputFunc = input) -> Note:
    section("Новая заметка")
    title = ask_text("Заголовок", input_func=input_func)
    content = ask_multiline("Текст заметки:", input_func=input_func) or ""
    attachments = ask_attachments(input_func=input_func) or []

    note = manager.create_note(title, content, attachments)
    logger.info("UI add: создана заметка id=%s title=%r", note.id, note.title)
    success(f"Заметка создана: {note.id}")
    print_note(note)
    return note


def list_notes(manager: NoteManager) -> list[Note]:
    notes = manager.get_all_notes()
    if not notes:
        warning("Заметок пока нет. Добавьте первую командой add.")
        return notes

    section(f"Заметки: {len(notes)}")
    print(render_notes_table(notes))
    muted("В командах используйте номер из колонки # (например: show 2).")
    muted("Также подходит полный id или уникальный префикс.")
    return notes


def show_note_interactive(
    manager: NoteManager,
    note_id: str | None = None,
    *,
    input_func: InputFunc = input,
) -> Note | None:
    notes = manager.get_all_notes()
    if not notes:
        warning("Заметок пока нет — показывать нечего.")
        return None

    raw_id = note_id or ask_text(ID_PROMPT, input_func=input_func)
    resolved = resolve_note_id(notes, raw_id)
    note = manager.get_note_by_id(resolved)
    if note is None:
        raise InputError(f"Заметка с id={resolved!r} не найдена.")

    print_note(note)
    return note


def edit_note_interactive(
    manager: NoteManager,
    note_id: str | None = None,
    *,
    input_func: InputFunc = input,
) -> Note | None:
    notes = manager.get_all_notes()
    if not notes:
        warning("Заметок нет — редактировать нечего.")
        return None

    raw_id = note_id or ask_text(ID_PROMPT, input_func=input_func)
    resolved = resolve_note_id(notes, raw_id)
    note = manager.get_note_by_id(resolved)
    if note is None:
        raise InputError(f"Заметка с id={resolved!r} не найдена.")

    section("Редактирование заметки")
    print_note(note)
    muted("Enter — оставить текущее значение.")

    changes: dict[str, object] = {}

    new_title = ask_optional_text("Заголовок", note.title, input_func=input_func)
    if new_title is not None:
        changes["title"] = new_title

    new_content = ask_multiline("Текст заметки:", input_func=input_func, allow_keep=True)
    if new_content is not None:
        changes["content"] = new_content

    new_attachments = ask_attachments(
        note.attachments, allow_keep=True, input_func=input_func
    )
    if new_attachments is not None:
        changes["attachments"] = new_attachments

    if not changes:
        info("Изменений нет — заметка осталась прежней.")
        return note

    updated = manager.update_note(note.id, **changes)
    logger.info("UI edit: обновлена заметка id=%s поля=%s", updated.id, sorted(changes))
    success("Заметка обновлена.")
    print_note(updated)
    return updated


def delete_note_interactive(
    manager: NoteManager,
    note_id: str | None = None,
    *,
    input_func: InputFunc = input,
) -> Note | None:
    notes = manager.get_all_notes()
    if not notes:
        warning("Заметок нет — удалять нечего.")
        return None

    raw_id = note_id or ask_text(ID_PROMPT, input_func=input_func)
    resolved = resolve_note_id(notes, raw_id)
    note = manager.get_note_by_id(resolved)
    if note is None:
        raise InputError(f"Заметка с id={resolved!r} не найдена.")

    section("Удаление заметки")
    print_note(note)
    if not confirm(f"Удалить заметку «{note.title}»?", default=False, input_func=input_func):
        warning("Удаление отменено.")
        return None

    removed = manager.delete_note(note.id)
    logger.info("UI delete: удалена заметка id=%s title=%r", removed.id, removed.title)
    success(f"Заметка удалена: {removed.id}")
    return removed


# ------------------------------------------------------------------ цикл
def _handle(manager: NoteManager, command: str, argument: str, input_func: InputFunc) -> None:
    if command == "add":
        add_note_interactive(manager, input_func=input_func)
    elif command == "list":
        list_notes(manager)
    elif command == "show":
        show_note_interactive(manager, argument or None, input_func=input_func)
    elif command == "edit":
        edit_note_interactive(manager, argument or None, input_func=input_func)
    elif command == "delete":
        delete_note_interactive(manager, argument or None, input_func=input_func)
    elif command == "help":
        print_help()


def run(manager: NoteManager, *, input_func: InputFunc = input) -> int:
    """Интерактивная сессия. EOF (Ctrl+D / Ctrl+Z) корректно завершает работу."""
    _print_banner(manager)

    while True:
        try:
            raw = input_func(PROMPT)
        except EOFError:
            print()
            info("Завершение работы.")
            return 0
        except KeyboardInterrupt:
            print()
            warning("Для выхода введите exit.")
            continue

        command_text, _, argument = raw.strip().partition(" ")
        command = ALIASES.get(command_text.lower())

        if command is None:
            if command_text:
                warning(f"Неизвестная команда: {command_text}. Введите help.")
            continue

        if command == "exit":
            info("До свидания!")
            return 0

        try:
            _handle(manager, command, argument.strip(), input_func)
        except InputError as exc:
            error(str(exc))
        except NoteMasterError as exc:
            logger.warning("Команда %s завершилась ошибкой: %s", command, exc)
            error(str(exc))
        except KeyboardInterrupt:
            print()
            warning("Операция отменена.")
        except Exception as exc:  # pragma: no cover - сессия не должна падать
            logger.exception("Непредвиденная ошибка команды %s", command)
            error(f"Непредвиденная ошибка: {exc}")
