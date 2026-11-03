# NoteMaster — заметки в терминале

NoteMaster развивается итерациями:

| Итерация | Содержание |
|----------|------------|
| «Ядро»  | модель `Note`, `NoteManager`, JSON-хранилище |
| «CLI»   | команды argparse, интерактивный режим, цветной вывод |

Ядро — самостоятельный слой: CLI не читает и не пишет JSON напрямую, а работает
только через `NoteManager`. Это позволяет позже подключить HTTP API, GUI или
другой драйвер хранения без изменения бизнес-логики.

## Краткая информация

| Параметр       | Значение                          |
|----------------|-----------------------------------|
| Язык           | Python 3.10+                      |
| Зависимости    | colorama (цвета; необязательна)   |
| Тесты          | unittest (stdlib) / pytest        |
| Структура      | слои + пакеты                     |
| Хранилище      | `data/notes.json`                 |

---

## Структура проекта

```
project_root/
├── app/                        # основное приложение
│   ├── __init__.py
│   ├── __main__.py             # запуск: python -m app
│   ├── main.py                 # совместимая точка входа
│   ├── cli.py                  # ─── CLI на argparse ───
│   ├── ui.py                   # ─── интерактивный режим ───
│   ├── console.py              # цвета, таблицы, подсказки, ввод
│   ├── core/                   # ─── ЯДРО ───
│   │   ├── __init__.py
│   │   ├── exceptions.py       # иерархия исключений
│   │   ├── models.py           # модель Note + правила валидации
│   │   └── note_manager.py     # бизнес-логика (CRUD)
│   └── driver/                 # ─── СЛОЙ ХРАНИЛИЩА ───
│       ├── __init__.py
│       └── file_driver.py      # JSON-файл (абстракция поверх ABC)
├── data/                       # пользовательские данные
│   └── notes.json              # создаётся автоматически
├── tests/
│   ├── __init__.py
│   ├── test_core.py            # тесты ядра
│   └── test_cli.py             # тесты CLI и интерактивного режима
├── requirements.txt            # colorama + pytest
├── .gitignore
└── README.md
```

---

## Назначение слоёв

### 1. `app.core` — ядро

Самостоятельная бизнес-логика. Внешние слои работают **только** с публичным API
ядра (`NoteManager`, `Note`, исключения).

| Модуль | Что делает |
|--------|------------|
| `exceptions.py` | Иерархия ошибок: `StorageError` → IO/JSON, `NoteValidationError` → поля, `NoteNotFoundError` → нет записи, `EmptyStorageError` → хранилище пусто |
| `models.py` | Модель `Note` с валидацией в `__post_init__`, методы `create`, `from_dict`, `to_dict`, `updated` |
| `note_manager.py` | CRUD-операции, ленивая загрузка, откат состояния при ошибке записи |

### 2. `app.driver` — абстракция хранения

Ядро не знает ни о файлах, ни о БД. `BaseNotesDriver` (ABC) — контракт:

```
load()  -> list[dict]
save()  -> None
```

Реализация `FileDriver` хранит заметки в JSON с атомарной записью
(временный файл + `os.replace`).

### 3. `app.console` — терминальное представление

Общий слой для CLI и UI: цвета, карточка заметки, таблица, подтверждения,
разбор вложений и разрешение сокращённого id. Бизнес-правил здесь нет.

### 4. `app.cli` — CLI на argparse

Парсит аргументы, вызывает методы `NoteManager` и печатает результат.
Команды: `add`, `list`, `show`, `edit`, `delete`, `demo`; флаг `--ui`
запускает интерактивный режим. Прямой работы с JSON нет.

### 5. `app.ui` — интерактивный режим

Цикл `while True` с командами `add`, `list`, `show`, `edit`, `delete`,
`help`, `exit`. Пустые обязательные поля запрашиваются повторно, перед
удалением требуется подтверждение, при редактировании показываются старые
значения.

### 6. `app.main`

Тонкая обёртка над `app.cli` для совместимости: `python app/main.py ...`
работает так же, как `python -m app.cli ...`.

### 7. `tests`

Все тесты используют unittest (stdlib), запускаются без установки зависимостей.
`InMemoryDriver` доказывает, что ядро не привязано к файловой системе.

---

## CLI

Точка входа — `python -m app.cli` (или `python app/main.py`, или `python -m app`).

### Команды

| Команда | Назначение | Пример |
|---------|------------|--------|
| `add` | создать заметку | `python -m app.cli add "Идеи" -c "Текст" -a plan.md` |
| `list` | показать все заметки | `python -m app.cli list` |
| `list --json` | вывод для скриптов | `python -m app.cli list --json` |
| `show` | заметка по id | `python -m app.cli show 3f2a1b` |
| `edit` | частичное изменение | `python -m app.cli edit 3f2a1b --title "Новое"` |
| `delete` | удаление по id | `python -m app.cli delete 3f2a1b --yes` |
| `demo` | демонстрационный сценарий | `python -m app.cli demo` |

Глобальные флаги указываются **до** команды:

```bash
python -m app.cli --storage ./my_notes.json list
python -m app.cli --log-file notemaster.log add "Заметка"
python -m app.cli -v list
python -m app.cli --ui
```

### Особенности CLI

- `id` можно указывать полностью или уникальным префиксом (`3f2a1b`).
- `edit` меняет только переданные поля; для очистки вложений есть
  `--no-attachments`.
- `delete` спрашивает подтверждение. В неинтерактивном режиме (скрипт, CI)
  нужно явно передать `--yes`.
- Ошибки выводятся красным и не приводят к падению: коды завершения
  `0` — успех, `1` — ошибка ядра/хранилища, `2` — ошибка ввода.
- Если файла `notes.json` нет, драйвер создаёт его автоматически.

### Интерактивный режим

```bash
python -m app.cli --ui
```

```
NoteMaster 0.2.0 — интерактивный режим
------------------------------------------------
Хранилище: .../data/notes.json
help — список команд, exit — выход.

NoteMaster> add
```

| Команда | Действие |
|---------|----------|
| `add` | пошаговое создание: заголовок, текст, вложения |
| `list` | таблица заметок |
| `show [id]` | карточка заметки |
| `edit [id]` | изменение; `Enter` оставляет поле как есть, `-` очищает текст/вложения |
| `delete [id]` | удаление с подтверждением `y/n` |
| `help` | справка |
| `exit` | выход (также `quit`, `q`) |

Пустой заголовок не принимается — вопрос повторяется. `Ctrl+D` (`Ctrl+Z` в
Windows) корректно завершает сессию, `Ctrl+C` отменяет текущую операцию.

### Цвета и оформление

- успех — зелёный, ошибка — красный, предупреждение — жёлтый;
- список выводится таблицей с выравниванием и разделителями;
- цвета автоматически отключаются при перенаправлении вывода и при
  `NO_COLOR=1`; принудительно включить можно через `FORCE_COLOR=1`.

---

## Бизнес-правила

| Правило | Исключение |
|---------|------------|
| `title` не может быть пустым | `NoteValidationError` |
| `attachments` — список строк | `NoteValidationError` |
| Нельзя редактировать пустое хранилище | `EmptyStorageError` |
| Нельзя удалять пустое хранилище | `EmptyStorageError` |
| Обновление только после получения по id | `NoteNotFoundError` |
| Обновление только разрешённых полей | `NoteValidationError` |
| Повреждённая запись в JSON не загружается | `StorageError` |

---

## Быстрый старт

```bash
# Виртуальное окружение (рекомендуется)
python -m venv .venv
. .venv/Scripts/activate    # Windows
source .venv/bin/activate   # Linux/macOS

# Зависимости (colorama; pytest — для тестов)
pip install -r requirements.txt

# Демо-сценарий
python -m app.cli demo

# Интерактивный режим
python -m app.cli --ui

# CLI-команды
python -m app.cli add "Заголовок" -c "Текст" -a path/to/file.txt
python -m app.cli list
python -m app.cli show <id-или-префикс>
python -m app.cli edit <id> --title "Новый заголовок"
python -m app.cli delete <id> --yes

# Тесты
python -m unittest discover -s tests -t .
# или
python -m pytest tests/
```

---

## Пример использования NoteManager

```python
from app.core import NoteManager

# По умолчанию данные в data/notes.json
manager = NoteManager()

# Создание
note = manager.create_note("Идеи", "1. Написать NoteMaster")
print(note.id, note.title)

# Чтение
all_notes = manager.get_all_notes()
note = manager.get_note_by_id("указанный-id")  # Note или None

# Частичное обновление (только переданные поля меняются)
updated = manager.update_note(note.id, content="1. Написать NoteMaster\n2. Добавить тесты")

# Удаление
removed = manager.delete_note(note.id)
```

---

## Расширение

Проект спроектирован так, чтобы добавлять новые слои без изменения ядра:

| Что добавить | Где |
|-------------|-----|
| HTTP API | `app/api/` — использовать `NoteManager` |
| GUI | `app/gui/` — использовать `NoteManager` |
| Новая команда CLI | `app/cli.py` — добавить subparser и обработчик |
| Новая команда UI | `app/ui.py` — добавить запись в `ALIASES` и `_handle` |
| БД вместо JSON | `app/driver/sql_driver.py` — реализовать `BaseNotesDriver` |
| Фоновые задания | `app/worker/` — использовать `NoteManager.reload()` |
| Кэш / поиск | `app/services/` — использовать `NoteManager.get_all_notes()` |

### Замена драйвера хранения

```python
from app.core import NoteManager
from app.driver.file_driver import BaseNotesDriver

class DatabaseDriver(BaseNotesDriver):
    def load(self) -> list[dict]: ...
    def save(self, records: list[dict]) -> None: ...

manager = NoteManager(driver=DatabaseDriver())
```

Ядро работает одинаково — ни о чём не подозревая.

---

## Логирование

Модули используют `logging.getLogger(__name__)`. CLI настраивает логгер `app`,
не вмешиваясь в корневой:

| Флаг | Поведение |
|------|-----------|
| по умолчанию | в консоль только `WARNING` и `ERROR` |
| `--verbose` / `-v` | в консоль `DEBUG` и выше |
| `--quiet` / `-q` | в консоль только `ERROR` |
| `--log-file PATH` | все действия (`INFO`) дублируются в файл |

Создание, изменение и удаление заметок логируются с id и заголовком; ошибки
ядра и ввода — с текстом исключения.

---

## Лицензия

MIT
