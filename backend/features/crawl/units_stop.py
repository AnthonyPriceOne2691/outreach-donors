"""Стоп Этапа 2 без юнитов Ahrefs: обходы не начинаются, пока DR новых кандидатов спросить не на что.

**Зачем.** Кандидат без DR — возможно, крупный сайт, которому не пишем (`big_sites.py`). Пока юниты
кончались посреди цикла, обходы шли дальше, а кандидаты приходили с пометкой «DR не проверен» и могли
дойти до письма (слово Anthony 06.11.2026: «если юнитов нет, стопаем всё»). Теперь Этап 2 ведёт себя
как прогон Этапа 1 (`runs/planning.py`): не помещается — не начинается, и причина названа словами.

**Что стоит:** кнопки обхода отказывают (`ensure_units`, тем же отказом, что у прогона), очередь
загрузки не выдаёт (`backlog.feed`), ночной круг не начинается (`night.tick`), перевод не берёт
кандидатов без DR (`dr_settled`). **Что идёт:** начатые обходы доходят до конца, а письма уже
проверенным рекламодателям уходят: юниты им не нужны.

**Порог — самый дешёвый запрос DR** (`MIN_REQUEST_UNITS`): меньше него `big_sites.ahrefs_ratings` не
спросит ни одного домена.

**Считается без запроса к Ahrefs:** снимок ключа и журнал расхода (`runs/ahrefs_cap.key_cap`) минус
обещанное идущими прогонами: очередь загрузки спрашивает об этом каждую минуту, а снимок держит свежим
сторож (`ops/key_watch.py`). Поэтому стоп снимается сам: обнуление ключа или поднятый лимит окажутся в
снимке со следующим опросом, и обходы пойдут без человека.

**Ключа нет — стопа нет.** DR спросить не у кого в принципе: это настройка, а не кончившийся остаток,
и вердикты такого стенда живут без DR (`big_sites.py`, шапка).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement, true
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import ahrefs as ahrefs_cfg
from backend.features.ahrefs.units import MIN_REQUEST_UNITS
from backend.features.core.models.advertiser import CandidateModel
from backend.features.ops.key_watch import HOW_TO_RAISE, binding_words, until_words
from backend.features.runs.ahrefs_cap import KeyCap, key_cap
from backend.features.runs.budget import CapExceededError
from backend.features.runs.repository import RunRepository
from backend.shared.numbers import spaced

#: Почему стоят, словами: заголовок экрана и строка ночного круга.
NO_UNITS = "закончились юниты Ahrefs"
UNKNOWN = "остаток юнитов Ahrefs неизвестен"

#: Что стоит и что идёт — одними словами при любой причине.
WHAT_STANDS = (
    "Без DR не видно, крупный ли сайт, поэтому новые обходы, очередь загрузки и ночной обход стоят, а "
    "кандидаты без DR к письму не идут. Начатые обходы дойдут до конца, письма уже проверенным "
    "рекламодателям уходят как обычно."
)


@dataclass(frozen=True, slots=True)
class UnitsStop:
    """Почему обходы стоят: одни слова у экрана, отказа кнопки, ночного круга и тревоги."""

    #: Причина коротко: «закончились юниты Ahrefs».
    why: str
    #: Сколько свободно, во что упирается, что стоит и когда снимется.
    detail: str
    #: Что сделать — для пометки тревоги.
    todo: str

    @property
    def title(self) -> str:
        return f"Обходы остановлены: {self.why}"


def _unknown(cap: KeyCap) -> UnitsStop:
    return UnitsStop(
        UNKNOWN,
        f"{cap.unknown}. {WHAT_STANDS} Снимется само, как только Ahrefs ответит.",
        "ждать ответа Ahrefs: сторож спрашивает его сам раз в десять минут",
    )


def _no_units(cap: KeyCap, left: int) -> UnitsStop:
    until, how = until_words(cap), HOW_TO_RAISE[cap.binding or "cap"]
    return UnitsStop(
        NO_UNITS,
        f"Свободно {spaced(left)}, а самый дешёвый запрос DR стоит {MIN_REQUEST_UNITS} юнитов. "
        f"Упирается {binding_words(cap)}. {WHAT_STANDS} Снимется само: {until}. Чтобы раньше: {how}.",
        f"ждать: {until}, или {how}",
    )


async def units_stop(session: AsyncSession) -> UnitsStop | None:
    """Почему обходы стоят; `None` — идут. Ahrefs не спрашивается: снимок и журнал (шапка)."""
    if not ahrefs_cfg.API_KEY:
        return None
    cap = await key_cap(session)
    if cap.units_cap is None:
        return _unknown(cap)
    left = max(0, cap.left - await RunRepository(session).claimed_units())
    return _no_units(cap, left) if left < MIN_REQUEST_UNITS else None


def dr_settled() -> ColumnElement[bool]:
    """DR не держит кандидата у перевода (`promote.py`): спрошен или «пишем» сказал человек. Ключа
    Ahrefs нет — спросить не у кого, и условие ничего не отсекает."""
    if not ahrefs_cfg.API_KEY:
        return true()
    return CandidateModel.dr_checked_at.is_not(None) | CandidateModel.confirmed.is_(True)


async def ensure_units(session: AsyncSession) -> None:
    """Отказ кнопке обхода, если обходы стоят: тем же отказом, что у прогона (409 со словами)."""
    stop = await units_stop(session)
    if stop is not None:
        raise CapExceededError(f"{stop.title}. {stop.detail}")
