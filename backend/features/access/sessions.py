"""Сеанс на сервере: выход и простой (аудит 20.11.2026, №6 и №7).

До этого дня пропуск жил свои сутки целиком. «Выйти» стирал его только в браузере, и
скопированный пропуск — из журнала, со снимка экрана, с чужого компьютера — открывал данные до
конца суток; вкладка, забытая открытой на ночь, работала до утра.

**Выход закрывает пропуск и на сервере** (`close`). Номер пропуска (`jti`, `tokens.py`) ложится
в Redis до конца срока самого пропуска, и «кто я» на каждом запросе (`api/deps.py`) его больше
не пускает: «Вы вышли — войдите снова». Дольше пропуска ключ не живёт — дальше закрывает срок.

**Простой закрывает сеанс** (`admit`). Без действий человека дольше `ACCESS_IDLE_MINUTES` (час)
пропуск больше не пускает: «Сеанс закончился: долго не было действий — войдите снова». Метка
последнего действия — в Redis по номеру пропуска, пишется не чаще раза в минуту
(`SEEN_EVERY_SEC`): запись на каждый запрос стоила бы столько записей, сколько запросов, а
точность в минуту часу не мешает. Метки ещё нет — последним действием считается выдача пропуска
(`iat`): вход и есть действие, и писать его отдельно незачем. Пропуск, закрытый простоем,
закрывается тем же ключом, что выход, с причиной «простой»: метка, потерянная Redis, его не оживит.

**Запрос, который экран сделал сам, — не действие** (`BACKGROUND_HEADER`). Экран спрашивает
сервер и без человека: числа у меню — раз в минуту на любом экране (`frontend/src/layout/work.ts`),
тревоги, ход прогона. Считай сервер их действиями, открытая вкладка не простаивала бы никогда.
Экран помечает запрос заголовком, когда человек последнюю минуту не касался ни мыши, ни клавиатуры
(`frontend/src/auth/presence.ts`): такой запрос проверяется как любой, но метку не двигает. Это
защита от забытой вкладки, а не от вора пропуска: вор заголовка не пришлёт — но и при счёте по
всем запросам он держал бы сеанс своими.

**Пропуск без номера** — выданный до выкатки 20.11.2026 — доживает свои сутки как раньше: закрыть
его нечем, простой не считается. Иначе выкатка выбросила бы всех разом.

**Redis не ответил — пропуск пускается**, и строка журнала уровня ошибки: выход и простой —
второй замок поверх суточного срока, и молчание Redis не должно запирать всех. Строка — не чаще
раза в минуту на процесс (`COMPLAINT_EVERY_SEC`), иначе её писал бы каждый запрос. Redis ждём
секунду (`REDIS_TIMEOUT_SEC`), а не пять, как очередь: его ждёт каждый запрос. Потеря данных
Redis — не молчание: метки нет, и простой считается с выдачи пропуска — пропуск старше часа
попросит войти снова, а выход, записанный после последнего снимка Redis на диск, забудется.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from functools import cache

from redis import Redis
from redis.exceptions import RedisError

from backend.config import access as cfg
from backend.features.access.permissions import SignedOutError
from backend.features.access.tokens import TokenPayload
from backend.shared import queue

logger = logging.getLogger(__name__)

#: Заголовок запроса, который экран сделал сам: человек за ним последнюю минуту ничего не делал.
#: Значение — «1». Имя — одно с экраном (`BACKGROUND_HEADER` в `frontend/src/api/client.ts`).
BACKGROUND_HEADER = "X-Background"

#: Пропуск закрыт: значение — причина (`LOGOUT`, `IDLE`), срок — до конца срока пропуска.
CLOSED_KEY = "outreach:access:closed:{token_id}"
#: Последнее действие человека с этим пропуском — секунды эпохи; срок тот же.
SEEN_KEY = "outreach:access:seen:{token_id}"

LOGOUT = "logout"
IDLE = "idle"

#: Отказ закрытому пропуску по причине — «входа нет» (`SignedOutError`): экран ведёт на вход.
SAID: dict[str, str] = {
    LOGOUT: "Вы вышли — войдите снова",
    IDLE: "Сеанс закончился: долго не было действий — войдите снова",
}

#: Выход не записан: Redis не ответил, и пропуск закроется только своим сроком.
NOT_CLOSED = (
    "Выход на сервере не записан: хранилище сеансов не ответило — пропуск закроется сам "
    "к концу своего срока"
)

#: Метка действия — не чаще раза в столько секунд на пропуск.
SEEN_EVERY_SEC = 60
#: Сколько ждать Redis сеансов — подключения и ответа.
REDIS_TIMEOUT_SEC = 1.0
#: Строка журнала о молчании Redis — не чаще раза в столько секунд на процесс.
COMPLAINT_EVERY_SEC = 60.0


@cache
def connection() -> Redis:
    """Клиент Redis сеансов — тот же, что у очереди (`shared/queue.connection`: адрес из
    настроек и пределы ожидания), но свой предел и один на процесс: проверка стоит на каждом
    запросе, и новое соединение на запрос стоило бы лишнего круга. Отдельной функцией — тесты
    подменяют её хранилищем в памяти (`tests/conftest.py`)."""
    return queue.connection(timeout=REDIS_TIMEOUT_SEC)


def clock() -> float:
    """Нынешнее время, секунды эпохи: метки делят процессы, и монотонные часы одного процесса
    для них не годятся. Отдельной функцией — тесты двигают её вперёд."""
    return time.time()


@dataclass
class Complaints:
    """Когда последний раз писали о молчании Redis: строка — не чаще `COMPLAINT_EVERY_SEC`."""

    last: float = -math.inf

    def due(self, now: float) -> bool:
        if now - self.last < COMPLAINT_EVERY_SEC:
            return False
        self.last = now
        return True


complaints = Complaints()


async def admit(payload: TokenPayload, *, background: bool) -> None:
    """Пустить пропуск по сеансу — или отказать «входа нет»: вышли — «Вы вышли», простой —
    «Сеанс закончился». `background` — запрос сделал экран сам: проверяется, но метку действия
    не двигает. Пропуск без номера — как раньше, без проверки. Redis не ответил — пускает.

    Клиент Redis синхронный — заход в пуле потоков: зависший Redis не держит цикл событий."""
    if payload.token_id is None:
        return
    reason = await asyncio.to_thread(_verdict, payload.token_id, payload, background, clock())
    if reason is not None:
        raise SignedOutError(SAID.get(reason, SAID[LOGOUT]))


async def close(payload: TokenPayload) -> bool:
    """Выход: пропуск закрыт до конца своего срока. `False` — Redis не ответил (строка журнала),
    и пропуск закроется только сроком. Пропуск без номера закрыть нечем: он выдан до выкатки и
    истечёт сам за сутки, а экран стирает его, как и раньше, — это не отказ выходу."""
    if payload.token_id is None:
        logger.info(
            "выход: у пропуска нет номера — закрыть нечем, истечёт сам",
            extra={"user_id": payload.user_id},
        )
        return True
    key = CLOSED_KEY.format(token_id=payload.token_id)
    closed = await asyncio.to_thread(_put, key, LOGOUT, payload, clock())
    if closed:
        logger.info("выход: пропуск закрыт", extra={"user_id": payload.user_id})
    return closed


def _verdict(token_id: str, payload: TokenPayload, background: bool, now: float) -> str | None:
    """Почему пропуск не пускать — причина закрытия; `None` — пускать. Метку действия двигает
    здесь же, чтобы проверка и запись шли одним заходом в поток."""
    closed_key = CLOSED_KEY.format(token_id=token_id)
    seen_key = SEEN_KEY.format(token_id=token_id)
    try:
        closed, seen = connection().mget(closed_key, seen_key)
    except RedisError as exc:
        _complain("выход и простой не проверены, пропуск пущен", exc)
        return None
    if closed is not None:
        return _text(closed)
    idle_sec = cfg.IDLE_MINUTES * 60
    if not idle_sec:
        return None
    last = _moment(seen, fallback=payload.issued_at.timestamp())
    if now - last > idle_sec:
        _put(closed_key, IDLE, payload, now)
        logger.info(
            "сеанс: простой дольше %s мин — пропуск закрыт",
            cfg.IDLE_MINUTES,
            extra={"user_id": payload.user_id},
        )
        return IDLE
    if not background and now - last >= SEEN_EVERY_SEC:
        _put(seen_key, str(int(now)), payload, now)
    return None


def _text(raw: object) -> str:
    """Причина закрытия из Redis: клиент отдаёт байты."""
    return raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)


def _moment(raw: object, *, fallback: float) -> float:
    """Метка действия из Redis — секунды эпохи; нет её или она испорчена — `fallback`."""
    if not isinstance(raw, bytes | str):
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def _put(key: str, value: str, payload: TokenPayload, now: float) -> bool:
    """Записать до конца срока пропуска: дольше пропуска ни метке, ни закрытию жить незачем.
    `False` — Redis не ответил (строка журнала)."""
    ttl = max(1, math.ceil(payload.expires_at.timestamp() - now))
    try:
        connection().set(key, value, ex=ttl)
    except RedisError as exc:
        _complain("запись сеанса не легла", exc)
        return False
    return True


def _complain(what: str, exc: RedisError) -> None:
    """Строка журнала о молчании Redis — уровнем ошибки, но не чаще раза в минуту."""
    if complaints.due(time.monotonic()):
        logger.error("сеансы: Redis не ответил — %s", what, extra={"error": str(exc)})
