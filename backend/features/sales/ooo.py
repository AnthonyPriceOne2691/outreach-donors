"""Автоответ в треде продаж: цепочка идёт дальше, следующий шаг переносится.

«Я в отпуске до 14.10» не значит «мне неинтересно» (`replies/outcome.py`):
автоответ цепочку не останавливает. Но писать раньше даты возвращения
незачем: следующий шаг продаж ждёт её, если она есть в тексте дословно, иначе —
`SALES_OOO_DELAY_DAYS` от получения автоответа. Только переносится: срок,
который и так позже, не трогается, а пустым срок не становится — остановить
цепочку автоответ не может.

**Дата ищется правилами, без модели** (вид автоответа решают правила приёма):
число с месяцем — 14.10, 14.10.2026, 14/10, 2026-10-14 — и месяц словом
по-английски, по-русски и по-немецки: «14 October», «October 14th»,
«14 октября», «14. Oktober». Неоднозначное (10/11 — октябрь или ноябрь?)
и даты вне окна от дня автоответа до 120 дней вперёд не берутся: тогда — срок
по умолчанию. Дат несколько («с 10.10 по 14.10») — берётся поздняя: это и есть
возвращение. Читается написанное рукой, без цитаты: в цитате — наше письмо.
"""

from __future__ import annotations

import re
from calendar import monthrange
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.replies.inbound import MAX_TEXT_CHARS
from backend.features.replies.quoting import written_by_hand

#: Дальше этого дата в автоответе — не возвращение, а опечатка или чужое число.
WINDOW_DAYS = 120

MONTHS: dict[str, int] = {
    **dict.fromkeys(("january", "jan", "januar", "jänner", "января"), 1),
    **dict.fromkeys(("february", "feb", "februar", "февраля"), 2),
    **dict.fromkeys(("march", "mar", "märz", "марта"), 3),
    **dict.fromkeys(("april", "apr", "апреля"), 4),
    **dict.fromkeys(("may", "mai", "мая"), 5),
    **dict.fromkeys(("june", "jun", "juni", "июня"), 6),
    **dict.fromkeys(("july", "jul", "juli", "июля"), 7),
    **dict.fromkeys(("august", "aug", "августа"), 8),
    **dict.fromkeys(("september", "sep", "sept", "сентября"), 9),
    **dict.fromkeys(("october", "oct", "oktober", "октября"), 10),
    **dict.fromkeys(("november", "nov", "ноября"), 11),
    **dict.fromkeys(("december", "dec", "dezember", "декабря"), 12),
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?\.?"
_DAY_MONTH = re.compile(rf"(?<!\d){_DAY}\s+(?:of\s+)?({_MONTH})\.?(?:,?\s+(\d{{4}}))?\b", re.I)
_MONTH_DAY = re.compile(rf"\b({_MONTH})\.?\s+{_DAY}(?:,?\s+(\d{{4}}))?(?!\d)", re.I)
_ISO = re.compile(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")
_DOTTED = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?![\d.]*\d)")
_SLASHED = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/]*\d)")


def _full_year(raw: str | None) -> int | None:
    if not raw:
        return None
    year = int(raw)
    return 2000 + year if year < 100 else year


def _candidates(text: str) -> Iterator[tuple[int, int, int | None]]:
    """(день, месяц, год или None) — всё, что похоже на дату, без проверки окна."""
    for day, month, year in _DAY_MONTH.findall(text):
        yield int(day), MONTHS[month.lower()], _full_year(year)
    for month, day, year in _MONTH_DAY.findall(text):
        yield int(day), MONTHS[month.lower()], _full_year(year)
    for year, month, day in _ISO.findall(text):
        yield int(day), int(month), int(year)
    for day, month, year in _DOTTED.findall(text):
        yield int(day), int(month), _full_year(year)
    for first, second, year in _SLASHED.findall(text):
        a, b = int(first), int(second)
        if a > 12 >= b:
            yield a, b, _full_year(year)
        elif b > 12 >= a:
            yield b, a, _full_year(year)
        # оба ≤ 12 — день или месяц первым, не понять: такую дату не берём


def _dated(day: int, month: int, year: int | None, received: date) -> date | None:
    """Дата в окне возвращения. Год не назван — ближайший, с которым она не в прошлом.
    Не дата (10.30 — время, 30.02) — `None`."""
    for guess in (year,) if year else (received.year, received.year + 1):
        if not (1 <= month <= 12 and 1 <= day <= monthrange(guess, month)[1]):
            return None
        found = date(guess, month, day)
        if received <= found <= received + timedelta(days=WINDOW_DAYS):
            return found
    return None


def return_date(text: str, received: date) -> date | None:
    """Дата возвращения из автоответа — поздняя из найденных в окне. `None` — даты нет."""
    found = [
        dated
        for day, month, year in _candidates(text)
        if (dated := _dated(day, month, year, received)) is not None
    ]
    return max(found, default=None)


@dataclass(frozen=True, slots=True)
class Postponed:
    """Куда ушёл следующий шаг и почему."""

    until: datetime
    #: Дата возвращения из письма. Пусто — срок по умолчанию.
    back: date | None
    #: Сколько сроков сдвинуто. Ноль — следующего шага нет или он и так позже.
    moved: int

    @property
    def words(self) -> str:
        source = (
            "дата возвращения из письма" if self.back else "даты в письме нет — срок по умолчанию"
        )
        return f"автоответ: следующий шаг не раньше {self.until:%d.%m.%Y} ({source}), сдвинуто сроков — {self.moved}"


async def postpone(
    session: AsyncSession, reply: ReplyModel, *, delay_days: int, now: datetime
) -> Postponed:
    """Перенести следующий шаг диалога автоответа — только вперёд, никогда не в пустоту."""
    received = reply.created_at or now
    back = return_date(written_by_hand(reply.raw_body[:MAX_TEXT_CHARS]), received.date())
    until = (
        datetime.combine(back, time.min, tzinfo=UTC)
        if back
        else received + timedelta(days=delay_days)
    )
    rows = await session.execute(
        select(MessageModel)
        .where(MessageModel.thread_id == reply.thread_id)
        .where(MessageModel.next_action_at.is_not(None))
    )
    moved = 0
    for message in rows.scalars().all():
        due = message.next_action_at
        if due is not None and due < until:
            message.next_action_at = until
            moved += 1
    return Postponed(until=until, back=back, moved=moved)
