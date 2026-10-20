"""«Искать глубже»: поиск адреса одному донору с исходом «адреса нет» — по кнопке.

Обычный поиск идёт по сотням доменов и экономит на каждом: восемь страниц, двадцать
четыре запроса, браузер — только у закрывшегося сайта. Его «адреса нет» — это «не
нашли за этот бюджет», а не «адреса на сайте нет». Глубже — та же лестница
(`ladder.ContactLadder`, `deep=True`) для одного домена: шире потолки обхода,
разделы из карты сайта, браузер и у открытого сайта, потолок времени на домен.

**Платная ступень глубже не зовётся.** Обычный поиск её уже спрашивал или спросит
по своему сроку, а за тот же домен она ответила бы тем же — и взяла бы кредит.

**Кому — правило одно на кнопку, карточку и задачу** (`deeper_rule`): донор, пишем
ему, адресов у домена нет, обычный поиск кончился «адреса нет», и глубже после этого
поиска ещё не искали. Глубже — раз на обычный поиск: снова — после следующего, иначе
кнопка звала бы ту же лестницу по тому же сайту с тем же итогом. Почему нельзя —
словами (`deeper_refusal`), до нажатия, как у «Найти адрес» (`repository.refusal_of`).

**Исход — словами** (`deeper_words`): нашёлся адрес и где, нашлась только форма, не
пустил сайт, сколько страниц прочитано, был ли браузер. Он остаётся у донора
(`contact_deeper_note`) и виден в карточке и завтра, а не только у кнопки.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import ColumnElement, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as cfg
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.repository import ContactRepository, has_address, not_entitled
from backend.features.contacts.search import NO_BROWSER, SearchReport, search_contacts
from backend.features.core.domain import ContactStatus, DonorStatus
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.donors.standing import is_donor
from backend.features.runs.exclusions import not_writing, not_writing_reason

logger = logging.getLogger(__name__)

#: Длиннее слова исхода не храним (`donors.contact_deeper_note`).
NOTE_MAX = 512

#: Потолок задачи поиска глубже в очереди адресов: срок лестницы на домен
#: (`CONTACTS_DEEP_TIMEOUT_SEC`) и запас на подъём браузера, MX и запись. Общий потолок
#: очереди — час (`shared/queue.JOB_TIMEOUT`), а процесс очереди адресов один: зависшая
#: задача держала бы общий поиск адресов весь этот час.
JOB_TIMEOUT_SEC = int(cfg.DEEP_TIMEOUT_SEC) + 180


def deeper_rule() -> ColumnElement[bool]:
    """Кому можно искать глубже — одно условие на кнопку, карточку, список и задачу."""
    return and_(
        DonorModel.status == DonorStatus.SUITABLE,
        is_donor(),
        ~not_writing(),
        ~has_address(DonorModel.domain_id),
        DonorModel.contact_status == ContactStatus.NOT_FOUND,
        or_(
            DonorModel.contact_deeper_at.is_(None),
            DonorModel.contact_deeper_at < DonorModel.contact_attempted_at,
        ),
    )


class DeeperQueue(ContactRepository):
    """Очередь поиска глубже: один донор, своё правило «кому» — остальное как у доноров.

    Исход пишется тем же путём (`ContactRepository.save`): адрес, форма, отметка
    попытки. Сверху — отметка «глубже искали» в той же фиксации: задача, упавшая
    после записи исхода, не оставит донора «готовым к поиску глубже» по тому же итогу.
    """

    #: Лестница глубже (`search._is_deep`).
    deep_search = True

    def __init__(self, session: AsyncSession, donor_id: int) -> None:
        super().__init__(session, donor_id=donor_id)
        self._donor = donor_id
        self.results: list[LadderResult] = []

    def _waiting(self, now: datetime, ttl_days: int) -> ColumnElement[bool]:
        """Своё правило «кому» вместо срока обычного поиска: глубже — по исходу, а не по
        дате (`deeper_rule`)."""
        del now, ttl_days
        return and_(deeper_rule(), DonorModel.id == self._donor)

    async def save(self, results: Sequence[LadderResult], *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        saved = await super().save(results, now=moment)
        self.results.extend(results)
        await self._session.execute(
            update(DonorModel).where(DonorModel.id == self._donor).values(contact_deeper_at=moment)
        )
        return saved


async def deeper_ready(session: AsyncSession, donor_ids: Sequence[int]) -> set[int]:
    """Кому из этих доноров можно искать глубже — для строк списка одним запросом."""
    if not donor_ids:
        return set()
    found = await session.scalars(
        select(DonorModel.id).where(DonorModel.id.in_(list(donor_ids)), deeper_rule())
    )
    return set(found)


#: Почему глубже нельзя по исходу обычного поиска, кроме «адреса нет» (`_after_search`).
_AFTER = {
    ContactStatus.FORM_ONLY: "На сайте нашлась форма — донор в очереди «Формы».",
    ContactStatus.NO_ANSWER: "Сайт не ответил — обычный поиск повторится сам, по сроку.",
    ContactStatus.PAID_OFF: (
        "В прошлый раз платный поиск был недоступен — глубже ищут после обычного поиска "
        "вместе с ним."
    ),
}


def _after_search(donor: DonorModel) -> str | None:
    """Почему глубже нельзя по исходу обычного поиска; `None` — исход «адреса нет»."""
    status = donor.contact_status
    if status is None:
        return "Глубже ищут после обычного поиска — сначала «Найти адрес»."
    if status is not ContactStatus.NOT_FOUND:
        return _AFTER.get(status, "Обычный поиск не закончен — он повторится сам.")
    deeper_at, note = donor.contact_deeper_at, donor.contact_deeper_note
    attempted = donor.contact_attempted_at
    if deeper_at is not None and (attempted is None or deeper_at >= attempted):
        said = f": {note}" if note else ""
        return (
            f"Глубже уже искали {deeper_at:%d.%m.%Y}{said}. Снова — после следующего "
            "обычного поиска: та же лестница по тому же сайту дала бы тот же итог."
        )
    return None


async def deeper_refusal(session: AsyncSession, donor: DonorModel) -> str | None:
    """Почему этому донору нельзя искать глубже — словами; `None` — можно.

    Решает правило (`deeper_rule`) запросом, слова только называют невыполненное
    условие — как у «Найти адрес» (`repository.search_refusal`).
    """
    if await deeper_ready(session, [donor.id]):
        return None
    never = not_entitled(donor)
    if never is not None:
        return never
    off = await not_writing_reason(session, donor.domain_id)
    if off is not None:
        return f"Адрес не ищут: {off}."
    known = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == donor.domain_id).limit(1)
    )
    if known is not None:
        return "У донора есть адрес — искать глубже незачем."
    reason = _after_search(donor)
    if reason is None:
        logger.warning("контакты: донор №%s не ждёт поиска глубже, причина не названа", donor.id)
        return "Искать глубже сейчас нельзя — причину назвать не удалось."
    return reason


def _path(url: str | None) -> str:
    return (urlsplit(url).path or "/") if url else "/"


def deeper_words(result: LadderResult | None, report: SearchReport) -> str:
    """Чем кончился поиск глубже — словами для карточки и строки задачи."""
    counters = report.counters
    if result is None:
        return "искать было некого: донор перестал ждать поиска, пока задача стояла в очереди"
    if result.found and result.contact is not None:
        return f"адрес нашёлся на странице {_path(result.contact.page_url)}"
    if result.status is ContactStatus.FORM_ONLY:
        return "адреса нет, но нашлась форма — донор встал в очередь «Формы»"
    if result.status is ContactStatus.NO_ANSWER:
        return f"сайт не ответил: {result.reason or 'причина не записана'} — повтор по сроку"
    parts = [f"адреса нет: {result.reason}" if result.reason else "адреса нет"]
    parts.append(
        f"прочитано страниц {result.pages_read}, запросов к сайту {counters.get('pages_fetched', 0)}"
    )
    if counters.get("sitemap_sections"):
        parts.append(f"разделов из карты сайта {counters['sitemap_sections']}")
    if NO_BROWSER in report.notes:
        parts.append("браузера на сервере нет — страницы, которые рисует скрипт, не открывались")
    elif counters.get("browser_pages"):
        parts.append(f"браузером открыто страниц {counters['browser_pages']}")
    return "; ".join(parts)


@dataclass(frozen=True, slots=True)
class DeeperOutcome:
    """Итог задачи поиска глубже — для строки задачи на экране."""

    donor_id: int
    status: str | None
    note: str
    report: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "donor_id": self.donor_id,
            "status": self.status,
            "note": self.note,
            **self.report,
        }


async def search_deeper(session: AsyncSession, donor_id: int) -> DeeperOutcome:
    """Пройти лестницу глубже по одному донору и записать исход словами.

    Правило «кому» проверяется здесь заново: между нажатием и исполнением донору
    могли вписать адрес или найти его общим поиском — тогда искать нечего.
    """
    queue = DeeperQueue(session, donor_id)
    report = await search_contacts(session, limit=1, use_browser=True, no_paid=True, queue=queue)
    result = queue.results[0] if queue.results else None
    note = deeper_words(result, report)[:NOTE_MAX]
    if result is not None:
        await session.execute(
            update(DonorModel).where(DonorModel.id == donor_id).values(contact_deeper_note=note)
        )
        await session.commit()
    logger.info("контакты: поиск глубже донора №%s — %s", donor_id, note)
    return DeeperOutcome(
        donor_id=donor_id,
        status=result.status.value if result is not None else None,
        note=note,
        report=report.as_dict(),
    )
