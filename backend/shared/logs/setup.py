"""Настройка логирования: поля из `extra` обязаны дожить до вывода.

Почему это отдельный модуль, а не строчка `basicConfig` в точке входа.

**Гейт на вызов без хендлера — плаченный и не полученный.** Правило
«значение идёт в `extra={...}`, а не вплавляется в текст» стоит дисциплины
на каждом вызове логгера. Но стандартный форматтер печатает только
`%(message)s`: всё, что передано в `extra`, он молча выбрасывает. То есть
цену за структурность платит автор кода, а получить её не может никто —
в выводе этих полей просто нет. Класс ошибки тот же, что у пропущенной
проверки: выглядит работающим, потому что ничего не ломает.

**Без сквозного идентификатора прогона логи не отвечают на главный вопрос.**
«Что происходило в прогоне 47» — это не поиск по времени: прогоны идут
параллельно, их строки перемешаны. Идентификатор кладётся в контекст один
раз на входе и попадает в каждую запись сам, включая те, что пишут
библиотеки внутри наших вызовов.

**Два формата, потому что два читателя.** `text` — человек в терминале,
поля дописываются в конец строки. `json` — прод, где строку читает не
человек, а поиск по полю. Переключается `LOG_FORMAT`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from backend.config import observability as cfg

#: Идентификатор текущего прогона. Пустая строка — код вызван вне прогона
#: (разовая команда, тест), и это законно: поле тогда просто не печатается.
_run_id: ContextVar[str] = ContextVar("run_id", default="")

#: Поля, которые `logging` кладёт в запись сам. Всё, чего здесь нет, пришло
#: из `extra` и должно быть напечатано.
_STANDARD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def current_run_id() -> str:
    """Идентификатор прогона, к которому относится текущий код."""
    return _run_id.get()


@contextmanager
def run_context(run_id: str | int) -> Iterator[None]:
    """Пометить все записи внутри блока идентификатором прогона."""
    token = _run_id.set(str(run_id))
    try:
        yield
    finally:
        _run_id.reset(token)


class RunIdFilter(logging.Filter):
    """Проставляет `run_id` каждой записи, включая чужие.

    Фильтр на хендлере, а не на логгере: записи от библиотек идут через
    свои логгеры, и на них наше поле иначе не попадёт.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        return True


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    """Всё, что автор положил в `extra`, плюс `run_id`."""
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_FIELDS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """Одна запись — одна строка JSON. Поля из `extra` остаются полями."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # `default=str`: в extra попадают доменные объекты и исключения,
        # и падение логгера на несериализуемом поле — худший из исходов.
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Человеческая строка, но поля из `extra` не теряются — они в хвосте."""

    def __init__(self) -> None:
        super().__init__("%(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = {k: v for k, v in _extra_fields(record).items() if v not in ("", None)}
        if not fields:
            return line
        tail = " ".join(f"{k}={v}" for k, v in sorted(fields.items()))
        return f"{line} | {tail}"


def setup_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Собрать корневой логгер. Вызывается один раз в точке входа.

    Повторный вызов заменяет хендлеры, а не добавляет вторые: иначе каждая
    запись печатается дважды, и это первое, что ломается при запуске
    команды из тестов.
    """
    chosen_level = (level or cfg.LOG_LEVEL).upper()
    chosen_format = (fmt or cfg.LOG_FORMAT).lower()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if chosen_format == "json" else TextFormatter())
    handler.addFilter(RunIdFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(chosen_level)
