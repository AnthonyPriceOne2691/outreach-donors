#!/usr/bin/env python3
"""Правила AST-гейта про роуты и async: безграничный список и CPU в event loop.

Часть `check_ast_gate.py` (`cqg@1.83`). Оба правила смотрят не на тело вообще, а
на КОНТРАКТ функции: что она отдаёт наружу и чем занимает цикл событий. Правила
про тело — в `ast_rules.py`.
"""

from __future__ import annotations

import ast

_ROUTE_DECOR = ("get", "post", "put", "patch", "delete", "route", "api_route")
_BOUND_PARAMS = ("limit", "page_size", "per_page", "size", "count", "top", "first",
                 "max_results")
_LIST_TYPES = ("list", "List", "Sequence", "Iterable")
_UNBOUNDED_OK_MARKER = "# unbounded-ok:"


def _is_route(fn: ast.AST) -> bool:
    """Декоратор вида `@router.get(...)` / `@app.route(...)`."""
    for dec in fn.decorator_list:
        call = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(call, ast.Attribute) and call.attr in _ROUTE_DECOR:
            return True
    return False


def _returns_list(fn: ast.AST) -> bool:
    """Аннотация возврата — именно список, а не конверт со страницей."""
    ret = fn.returns
    if isinstance(ret, ast.Name):
        return ret.id == "list"
    if isinstance(ret, ast.Subscript):
        base = ret.value
        if isinstance(base, ast.Name):
            return base.id in _LIST_TYPES
        if isinstance(base, ast.Attribute):
            return base.attr in _LIST_TYPES
    return False


def _has_bound_param(fn: ast.AST) -> bool:
    """Граница объявлена И ИСПОЛЬЗУЕТСЯ в теле.

    Второе условие — не придирка, а защита от того, что проверка сама же
    подсказывает: приписать `limit: int = 50` в подпись и не передать его вниз.
    Такой эндпоинт отдаёт всё по-прежнему, но выглядит исправленным — и в OpenAPI
    параметр даже виден. Слабая форма ПРОВЕРЕНА подстановкой: без этого условия
    гейт печатал OK на `list_all(q)` с объявленным и проигнорированным `limit`.
    Цена ложного срабатывания тут низкая: параметр, который правда используется,
    в теле упоминается — иначе он не влияет ни на что.
    """
    a = fn.args
    declared = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)
                if x.arg.lower() in _BOUND_PARAMS}
    if not declared:
        return False
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    # `**kwargs`-проброс: имя параметра в теле не появляется, но граница уходит вниз.
    if a.kwarg is not None and a.kwarg.arg in used:
        return True
    return bool(declared & used)


def _unbounded_ok(fn: ast.AST, src_lines: list[str]) -> bool:
    start = fn.decorator_list[0].lineno if fn.decorator_list else fn.lineno
    end = fn.body[-1].end_lineno if fn.body else fn.lineno
    return any(_UNBOUNDED_OK_MARKER in src_lines[i]
               for i in range(start - 1, min(end, len(src_lines))))


def find_unbounded_list(tree: ast.AST, src_lines: list[str]) -> list[int]:
    """Эндпоинт отдаёт список без границы — выборка растёт с корпусом.

    Признак составной, и каждая часть нужна:
      • это РОУТ — во внутреннем хелпере «отдать всё» законно;
      • аннотация возврата — список, а не конверт (`ItemsPage` с `total`/`items`
        обычно уже описывает страницу);
      • нет ни одного параметра границы из `_BOUND_PARAMS`.

    lab-12, ловушка T4: обе независимые арки написали ПОЧТИ ДОСЛОВНО одинаковый
    `list_all()` — при том что в том же приложении рядом лежал сосед с лимитом
    (`/sessions/interviews?limit=5` отдавал 5, новый `?limit=5` отдавал 300 и 137).
    То есть образец правильного кода в том же файле slip не предотвратил, а оракула
    этого класса в стеке не было ни одного вида. На живом проекте это ещё и вектор
    отказа, а не только медленный ответ.

    Осознанный случай называется словом: `# unbounded-ok: <причина>` — например
    перечисление статусов, которое по устройству не растёт.
    """
    out: list[int] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route(fn) or not _returns_list(fn):
            continue
        if _has_bound_param(fn) or _unbounded_ok(fn, src_lines):
            continue
        out.append(fn.lineno)
    return out


_CPU_CALLS = {
    ("json", "loads"), ("json", "dumps"),
    ("re", "search"), ("re", "match"), ("re", "findall"), ("re", "finditer"),
    ("re", "compile"), ("re", "sub"),
    ("pickle", "loads"), ("pickle", "dumps"),
    ("yaml", "safe_load"), ("yaml", "load"),
    ("base64", "b64decode"), ("base64", "b64encode"),
    ("hashlib", "md5"), ("hashlib", "sha256"),
}
_OFFLOAD = ("to_thread", "run_in_executor", "run_in_threadpool", "run_sync", "gather")
_CPU_OK_MARKER = "# cpu-ok:"


def _offloaded(fn: ast.AST) -> bool:
    """В теле корутины есть перенос работы с цикла — правило не про неё."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in _OFFLOAD:
                return True
            if isinstance(f, ast.Name) and f.id in _OFFLOAD:
                return True
    return False


def _loop_body(node: ast.AST) -> list[ast.AST] | None:
    """Тело ЦИКЛА, если узел — цикл; None — не цикл, смотреть нечего.

    Шов по данным: из ветвления наружу выходило одно имя — `loop_body`, а
    ветка `else` не значила ничего, кроме «цикла тут нет». Comprehension
    отдаётся целиком: его «тело» — он сам.
    """
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        return list(node.body)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return [node]
    return None


def _cpu_calls_in(inner: ast.AST, src_lines: list[str]) -> list[int]:
    """Строки CPU-вызовов из `_CPU_CALLS` внутри узла, кроме помеченных `# cpu-ok:`.

    Шов по сложности: отбор вызовов стоил правилу половины ветвлений, а наружу
    отдаёт один список номеров строк — больше из этой глубины ничего не идёт.
    """
    out: list[int] = []
    for call in ast.walk(inner):
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
            continue
        if (f.value.id, f.attr) not in _CPU_CALLS:
            continue
        line = src_lines[call.lineno - 1] if call.lineno <= len(src_lines) else ""
        if _CPU_OK_MARKER in line:
            continue
        out.append(call.lineno)
    return out


def find_cpu_in_async(tree: ast.AST, src_lines: list[str]) -> list[int]:
    """CPU-работа в ЦИКЛЕ внутри `async def` — голодание event loop.

    Признак составной, и каждая часть нужна:
      • `async def` — в обычной функции это не дефект;
      • ЦИКЛ (`for`/`while`/comprehension) — один `json.loads` дёшев, беда в том,
        что стоимость растёт с размером выборки;
      • вызов из списка `_CPU_CALLS` — разбор/сериализация/регулярка/хеш.

    Не срабатывает, если в теле есть перенос на пул (`to_thread`,
    `run_in_threadpool`, …) или стоит `# cpu-ok: <причина>` — осознанный случай
    называется словом, как `# silent-ok:` у соседнего правила.

    lab-12: единственный класс, подтверждённый как slipped ОБЕИМИ арками. Обе
    независимо написали `for row in await list_all(): json.loads(row.…)` в
    async-хендлере; канарейка «чистый CPU в async» под конфигом обеих арен дала
    exit 0 — в стеке не было оракула этого класса ни одного вида. ASYNC-правила
    ruff ловят блокирующий IO (`open`, `sleep`), а чистый CPU — нет.
    """
    out: list[int] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef) or _offloaded(fn):
            continue
        for node in ast.walk(fn):
            body = _loop_body(node)
            if body is None:
                continue
            for inner in body:
                out += _cpu_calls_in(inner, src_lines)
    return sorted(set(out))


