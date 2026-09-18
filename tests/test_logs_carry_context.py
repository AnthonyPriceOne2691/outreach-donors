"""Логи обязаны доносить `extra` и идентификатор прогона до вывода.

Гейт `unstructured-log` требует класть значения в `extra={...}`. Цена
платится на каждом вызове логгера, а получить её можно только если
хендлер эти поля печатает: стандартный форматтер их молча выбрасывает,
и тогда дисциплина существует, а пользы от неё нет. Проверяется поэтому
не наличие настройки, а сам вывод.
"""

from __future__ import annotations

import json
import logging

import pytest
from backend.shared.logs import current_run_id, run_context, setup_logging


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved:
        root.addHandler(handler)
    root.setLevel(level)


def _emit(capsys, fmt: str, **extra: object) -> str:
    setup_logging(level="INFO", fmt=fmt)
    logging.getLogger("test").info("сообщение", extra=extra)
    return capsys.readouterr().err.strip()


def test_extra_fields_reach_json_output(capsys) -> None:
    payload = json.loads(_emit(capsys, "json", step="mx", found=3))
    assert payload["step"] == "mx"
    assert payload["found"] == 3
    assert payload["message"] == "сообщение"


def test_extra_fields_reach_text_output(capsys) -> None:
    line = _emit(capsys, "text", step="rdap", found=0)
    assert "step=rdap" in line
    # Ноль — это ответ, а не отсутствие ответа: «ступень ничего не нашла»
    # обязано быть видно, иначе неработающая ступень выглядит как молчащая.
    assert "found=0" in line


def test_run_id_marks_every_record_including_foreign_loggers(capsys) -> None:
    setup_logging(level="INFO", fmt="json")
    # Имя своё, а не `httpx`: уровень чужого логгера мог быть поднят где-то
    # ещё в наборе, и тогда запись просто не выйдет — тест упадёт на пустом
    # выводе, хотя проверяемое свойство цело. Ловили в полном прогоне.
    foreign = logging.getLogger("сторонняя.библиотека.проба")
    foreign.setLevel(logging.NOTSET)
    foreign.propagate = True
    with run_context(47):
        foreign.info("чужая запись")
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["run_id"] == "47"


def test_run_id_is_empty_outside_a_run() -> None:
    assert current_run_id() == ""
    with run_context(1):
        assert current_run_id() == "1"
    assert current_run_id() == ""


def test_setup_is_idempotent(capsys) -> None:
    """Повторный вызов не удваивает строки — иначе ломается запуск из тестов."""
    setup_logging(level="INFO", fmt="json")
    setup_logging(level="INFO", fmt="json")
    logging.getLogger("test").info("один раз")
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_unserializable_value_does_not_break_logging(capsys) -> None:
    """Падение логгера на чужом объекте — худший исход из возможных."""
    payload = json.loads(_emit(capsys, "json", obj=object()))
    assert "obj" in payload
