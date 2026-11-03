"""Терминальный слой NoteMaster: цвета, таблицы и ввод.

Модуль не содержит бизнес-логики. Его задачи:
    * единообразно оформлять вывод (успех, ошибка, предупреждение);
    * рисовать таблицу заметок и карточку заметки;
    * нормализовать пользовательский ввод (id, вложения, подтверждения).

Цвета включаются только в интерактивном терминале и отключаются переменной
окружения ``NO_COLOR``. colorama нужна лишь для старых консолей Windows;
без неё модуль продолжает работать.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from typing import Callable, Iterable, Sequence

from app.core.models import Note

try:  # pragma: no cover - зависит от окружения
    from colorama import just_fix_windows_console  # type: ignore[import-untyped]
except ImportError:  # colorama не обязательна
    just_fix_windows_console = None
else:  # pragma: no cover - на не-Windows вызов ничего не делает
    just_fix_windows_console()

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

#: Функция ввода: подменяется в тестах и в интерактивном режиме.
InputFunc = Callable[[str], str]


class InputError(Exception):
    """Введённые данные нельзя применить к хранилищу (id, вложения)."""


# --------------------------------------------------------------------- цвета
def _colors_enabled(stream: object) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.name == "nt" and just_fix_windows_console is None:
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _paint(text: str, code: str, *, stream: object | None = None) -> str:
    target = stream or sys.stdout
    if not _colors_enabled(target):
        return text
    return f"{code}{text}{RESET}"


def bold(text: str) -> str:
    return _paint(text, BOLD)


# --------------------------------------------------------------------- вывод
def echo(message: str = "") -> None:
    print(message)


def success(message: str) -> None:
    print(_paint(f"[OK] {message}", GREEN))


def error(message: str) -> None:
    print(_paint(f"[ОШИБКА] {message}", RED), file=sys.stderr)


def warning(message: str) -> None:
    print(_paint(f"[!] {message}", YELLOW))


def info(message: str) -> None:
    print(_paint(f"[i] {message}", CYAN))


def muted(message: str) -> None:
    print(_paint(message, DIM))


def section(title: str) -> None:
    """Заголовок раздела: пустая строка, название и разделитель."""
    print()
    print(bold(title))
    print("-" * max(len(title), 48))


# --------------------------------------------------------------- форматирование
def format_timestamp(value: str) -> str:
    """ISO-8601 -> читаемая локальная дата. Некорректное значение не ломает вывод."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%d.%m.%Y %H:%M")


def short_id(note_id: str, width: int = 8) -> str:
    return note_id if len(note_id) <= width else note_id[:width]


def truncate(text: str, width: int) -> str:
    """Схлопывает пробелы и обрезает строку до width с многоточием."""
    single_line = " ".join(str(text).split())
    if len(single_line) <= width:
        return single_line
    if width <= 3:
        return single_line[:width]
    return single_line[: width - 3] + "..."


def format_attachments(attachments: Sequence[str]) -> str:
    return ", ".join(attachments) if attachments else "-"


def render_notes_table(notes: Sequence[Note]) -> str:
    """Таблица заметок: номер, id, заголовок, дата, вложения."""
    id_width = 8
    time_width = 16
    terminal = max(80, shutil.get_terminal_size((120, 24)).columns)
    attach_width = 24 if terminal >= 110 else 18
    title_width = min(
        44,
        max(18, terminal - (3 + 2 + id_width + 2 + 2 + time_width + 2 + attach_width)),
    )

    header = (
        f"{'#':>3}  {'ID':<{id_width}}  {'ЗАГОЛОВОК':<{title_width}}  "
        f"{'СОЗДАНА':<{time_width}}  {'ВЛОЖЕНИЯ':<{attach_width}}"
    )
    lines = [header, "-" * len(header)]
    for index, note in enumerate(notes, start=1):
        lines.append(
            f"{index:>3}  {short_id(note.id):<{id_width}}  "
            f"{truncate(note.title, title_width):<{title_width}}  "
            f"{format_timestamp(note.timestamp):<{time_width}}  "
            f"{truncate(format_attachments(note.attachments), attach_width):<{attach_width}}"
        )
    return "\n".join(lines)


def render_note(note: Note) -> str:
    """Подробная карточка одной заметки."""
    lines = [
        f"ID:        {note.id}",
        f"Заголовок: {note.title}",
        f"Создана:   {format_timestamp(note.timestamp)}  ({note.timestamp})",
    ]
    if note.attachments:
        lines.append(f"Вложения:  {note.attachments[0]}")
        lines.extend(f"           {path}" for path in note.attachments[1:])
    else:
        lines.append("Вложения:  (нет)")

    lines.append("Текст:")
    if note.content:
        lines.extend(f"  {line}" for line in note.content.splitlines())
    else:
        lines.append("  (пусто)")
    return "\n".join(lines)


def print_note(note: Note) -> None:
    print(render_note(note))


def print_notes(notes: Sequence[Note]) -> None:
    print(render_notes_table(notes))


# ------------------------------------------------------------------ ввод
def ask_text(
    prompt: str,
    *,
    input_func: InputFunc = input,
    allow_empty: bool = False,
    default: str | None = None,
) -> str:
    """Запрашивает непустую строку, повторяя вопрос при пустом вводе."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input_func(f"{prompt}{suffix}: ")
        value = raw.strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ""
        warning("Значение не может быть пустым, попробуйте ещё раз.")


def ask_optional_text(
    prompt: str,
    current: str,
    *,
    input_func: InputFunc = input,
    max_current: int = 60,
) -> str | None:
    """Пустой ввод означает «оставить как есть» (None)."""
    shown = truncate(current, max_current) if current else "пусто"
    raw = input_func(f"{prompt} [{shown}]: ")
    return raw.strip() or None


def ask_multiline(
    prompt: str,
    *,
    input_func: InputFunc = input,
    allow_keep: bool = False,
) -> str | None:
    """Многострочный текст.

    allow_keep=False: пустая строка или '.' завершают ввод.
    allow_keep=True:  пустая первая строка — оставить текущий текст (None),
                      '-' — очистить, '.' — завершить ввод.
    """
    info(prompt)
    if allow_keep:
        muted("  Пустая строка — оставить текущий текст, '-' — очистить, '.' — завершить ввод.")
    else:
        muted("  Пустая строка или '.' — завершить ввод.")

    lines: list[str] = []
    while True:
        raw = input_func("  > ")
        stripped = raw.strip()
        if not lines:
            if allow_keep and not stripped:
                return None
            if allow_keep and stripped == "-":
                return ""
            if allow_keep and stripped == ".":
                return None
            if not allow_keep and not stripped:
                return ""
        if stripped == ".":
            break
        if not stripped and lines:
            break
        lines.append(raw.rstrip())
    return "\n".join(lines).strip()


def parse_attachments(raw: str) -> list[str]:
    """Разбирает строку «a.txt, b.png; c.md» в список уникальных путей."""
    result: list[str] = []
    for part in re.split(r"[,;]", raw):
        path = part.strip().strip("\"'")
        if path and path not in result:
            result.append(path)
    return result


def ask_attachments(
    current: Sequence[str] | None = None,
    *,
    allow_keep: bool = False,
    input_func: InputFunc = input,
) -> list[str] | None:
    """Вложения через запятую.

    allow_keep=True: пустой ввод — оставить текущие (None), '-' — очистить.
    allow_keep=False: пустой ввод — пустой список (режим создания).
    """
    if allow_keep:
        muted(f"  Текущие вложения: {format_attachments(current or [])}")
        hint = "Вложения через запятую [Enter — оставить, '-' — очистить]: "
    else:
        hint = "Вложения через запятую (Enter — пропустить): "

    raw = input_func(hint)
    value = raw.strip()
    if not value:
        return None if allow_keep else []
    if value == "-":
        return []
    return parse_attachments(value)


def confirm(prompt: str, *, default: bool = False, input_func: InputFunc = input) -> bool:
    """Подтверждение y/n с поддержкой русских ответов."""
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input_func(f"{prompt} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "д", "да"):
            return True
        if raw in ("n", "no", "н", "нет"):
            return False
        warning("Ответьте 'y' (да) или 'n' (нет).")


def resolve_note_id(notes: Iterable[Note], raw_id: str) -> str:
    """Полный id или уникальный префикс -> полный id.

    Минимизирует ввод: в таблице id показан сокращённо, и этого достаточно,
    пока префикс однозначен.
    """
    value = (raw_id or "").strip()
    if not value:
        raise InputError("Укажите id заметки.")

    candidates = list(notes)
    exact = next((note for note in candidates if note.id == value), None)
    if exact is not None:
        return exact.id

    matches = [note for note in candidates if note.id.startswith(value)]
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        raise InputError(
            f"id {value!r} неоднозначен: подходит заметок — {len(matches)}. "
            "Введите больше символов."
        )
    raise InputError(f"Заметка с id={value!r} не найдена.")
