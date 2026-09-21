"""Всё состояние, переживающее контейнер, попадает в бэкап.

Инвариант не про скрипт, а про **соответствие двух файлов**: том
появляется в `docker-compose.yml`, а помнить про него должен
`scripts/backup.sh`. Забыть легко: том добавляют ради одной задачи,
бэкап трогают раз в полгода, и узнают об этом в день восстановления.

Поэтому список томов берётся из компоуза, а не переписывается сюда:
переписанный разошёлся бы с настоящим ровно тогда, когда появился бы
новый том.

Способ взят из соседней системы, где он уже ловил забытый том.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE = _ROOT / "docker-compose.yml"
_BACKUP = _ROOT / "scripts" / "backup.sh"
_RESTORE = _ROOT / "scripts" / "restore.sh"

#: Тома, которые можно потерять без последствий, — и почему.
_REPRODUCIBLE = {
    # Очередь: задачи пересобираются запуском прогона, а недоделанная
    # задача из вчерашнего бэкапа хуже её отсутствия — она купит выдачу
    # второй раз.
    "redisdata",
}


def _volumes() -> set[str]:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return set(compose.get("volumes") or {})


def test_every_stateful_volume_is_named_in_the_backup() -> None:
    backup = _BACKUP.read_text(encoding="utf-8")

    forgotten = [volume for volume in _volumes() - _REPRODUCIBLE if volume not in backup]

    assert forgotten == [], (
        f"тома {forgotten} есть в компоузе, но не упомянуты в scripts/backup.sh — "
        "либо их надо бэкапить, либо объяснить в _REPRODUCIBLE, почему нет"
    )


def test_reproducible_volumes_are_still_volumes() -> None:
    """Список исключений не должен пережить сами тома.

    Убрали том — исключение остаётся и молча прикрывает следующий том
    с тем же именем, который заведут через полгода уже под данные.
    """
    assert _volumes() >= _REPRODUCIBLE


def test_restore_stops_everyone_who_writes() -> None:
    """Восстановление под живым воркером даёт базу, в которой половина
    строк из бэкапа, половина из работы. Список пишущих сервисов —
    в скрипте, и он должен покрывать все процессы компоуза."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    restore = _RESTORE.read_text(encoding="utf-8")

    writers = {
        name
        for name, service in (compose.get("services") or {}).items()
        # Пишут в базу все, кто поднят с кодом сервиса, кроме миграций:
        # их восстановление запускает само.
        if str(service.get("image", "")).startswith("${BACKEND_IMAGE") and name != "migrate"
    }
    named = next(line for line in restore.splitlines() if line.startswith("WRITERS="))

    missing = sorted(name for name in writers if name not in named)
    assert missing == [], f"сервисы {missing} пишут в базу, но restore.sh их не останавливает"
