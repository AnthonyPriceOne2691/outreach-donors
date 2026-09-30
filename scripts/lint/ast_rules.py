#!/usr/bin/env python3
"""Правила AST-гейта: молчащий except и вшитый промпт.

Часть `check_ast_gate.py` (`cqg@1.83`) — гейт разрезан по планке 300 строк
(Delivery §9.1a п.5). Здесь два правила про ТЕЛО функции; правила про роуты и
async живут в `ast_web_rules.py`, а обход дерева и снимок — во входном скрипте.

Каждая функция — оракул одного класса: получает разобранное дерево и строки
исходника, возвращает список номеров строк-нарушений.
"""

from __future__ import annotations

import ast

SILENT_OK_MARKER = "# silent-ok:"

# ПРИНЦИП «что такое логгер», общий с грепом (`unstructured-log`): имя
# ОКАНЧИВАЕТСЯ словом log/logger/logging, отделённым началом имени или
# подчёркиванием; регистр не важен; цепочка (`self.logger`, `cls._log`) судится
# по последнему звену. По принципу отвечают про НОВЫЙ случай, не спрашивая
# автора: `LOG`, `_log`, `app_logger`, `audit_log` — логгер; `catalog`, `dialog`,
# `blog`, `lock_manager` — нет, совпадения подстроки мало (слабая форма держится
# прогоном `tests/test_silent_except_logger.py`).
#
# Список имён вместо принципа давал ЛОЖНОЕ КРАСНОЕ на двух самых частых формах:
# `LOG = logging.getLogger(__name__)` (сравнение было регистрозависимым) и
# `self.logger` (база — `ast.Attribute`, а не `ast.Name`). Дыра была ровно в
# размер трёх методов: `.debug()`, `.info()`, `.critical()` не начинаются ни с
# одного префикса ниже, а `.error`/`.warning`/`.exception` спасал именно он.
# Цена тут несимметрична: штатный ответ на ложное красное — вписать
# `# silent-ok:`, то есть ВЫКЛЮЧИТЬ правило на живом логирующем обработчике.
_LOGGER_WORDS = {"log", "logger", "logging"}
# Канал следа, логгером НЕ являющийся, и потому вынесенный из общего понятия:
# `warnings.warn(...)`. Граница названа, чтобы расхождение с братом было
# решением, а не забывчивостью: `unstructured-log` про `warnings` молчит
# законно — `extra=` у него нет, и требовать его значило бы держать правило
# красным на легальном коде (§4.3b).
_TRACE_MODULES = {"warnings"}
_LOG_ATTR_PREFIXES = ("log", "warn", "exception", "error", "record", "capture", "notify")
_PROMPT_MARKERS = ("ты —", "ты -", "you are", "you classify", "act as", "роль:", "system:")
_PROMPT_MIN_LINES = 8


def _is_broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    if isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"):
        return True
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in ("Exception", "BaseException") for e in t.elts)
    return False


def _module_has_logging(tree: ast.AST) -> bool:
    """Импортирует ли модуль logging (в любой форме)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "logging" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "logging":
                return True
    return False


def _print_shows_the_error(handler: ast.ExceptHandler) -> bool:
    """print(), печатающий САМУ ошибку, — след не хуже лога.

    Расхождение братьев, найденное обновлением чужого проекта: соседнее правило
    `unstructured-log` про print говорит прямо — «в CLI и скриптах он законен», и
    потому его не ловит. А `silent-except` следом его не считал, и краснел на
    обработчике, который ошибку ПЕЧАТАЕТ с контекстом. Два правила одного канона
    смотрели на один и тот же print и расходились.

    Признано следом узко, и каждое сужение нужно:
      · только в модуле БЕЗ `logging` — там print и есть канал вывода. Модуль,
        который логгер импортировал, обязан им пользоваться;
      · только если печатается связанное имя ошибки (`except … as e` + `e` в
        аргументах). `print("не вышло")` следом не является: он не отличает
        причины друг от друга, а весь смысл правила в контексте.
    """
    if handler.name is None:
        return False
    for node in ast.walk(handler):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        for arg in ast.walk(node):
            if isinstance(arg, ast.Name) and arg.id == handler.name:
                return True
    return False


def _is_logger_name(name: str) -> bool:
    """Логгер ли это — по принципу выше, а не по списку имён."""
    return name.lstrip("_").rsplit("_", 1)[-1].lower() in _LOGGER_WORDS


def _call_base(node: ast.AST) -> str:
    """На чём висит вызов: `LOG` у `LOG.debug`, `logger` у `self.logger.info`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _handler_leaves_trace(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = _call_base(func.value)
                if _is_logger_name(base) or base in _TRACE_MODULES:
                    return True
                if func.attr.startswith(_LOG_ATTR_PREFIXES):
                    return True
            if isinstance(func, ast.Name) and func.id.startswith(("log", "record", "capture", "notify")):
                return True
    return False


def _handler_has_silent_ok(handler: ast.ExceptHandler, src_lines: list[str]) -> bool:
    end = handler.body[-1].end_lineno if handler.body else handler.lineno
    for i in range(handler.lineno - 1, min(end, len(src_lines))):
        if SILENT_OK_MARKER in src_lines[i]:
            return True
    return False


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def find_silent_except(tree: ast.AST, src_lines: list[str]) -> list[int]:
    out = []
    has_logging = _module_has_logging(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ExceptHandler) and _is_broad(node)):
            continue
        if _handler_leaves_trace(node) or _handler_has_silent_ok(node, src_lines):
            continue
        # print с самой ошибкой — след, но только в модуле-скрипте (без logging)
        if not has_logging and _print_shows_the_error(node):
            continue
        out.append(node.lineno)
    return out


def find_inline_prompt(tree: ast.AST, src_lines: list[str]) -> list[int]:  # noqa: ARG001
    doc_ids = _docstring_ids(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            if node.value.count("\n") + 1 < _PROMPT_MIN_LINES:
                continue
            head = node.value.strip().lower()
            if head.startswith(_PROMPT_MARKERS):
                out.append(node.lineno)
    return out


