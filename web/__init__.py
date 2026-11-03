"""Веб-слой NoteMaster: Flask-приложение поверх ядра.

Модуль импортирует ``NoteManager`` из ``app.core`` и не обращается к JSON
напрямую. Единая точка правды — ``data/notes.json``.

Запуск: ``python web/app.py``, ``python -m web.app`` или ``flask --app web.app run``.
"""
