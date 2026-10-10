"""Стоп-лист: кто в нём, как туда попадают руками и как оттуда выходят.

Правило ширины и то, чем отписка кнопкой отличается от отписки письмом,
описано в `okf/unsubscribe.md`; здесь исполнение и работа человека
со списком.

**Снятие записи об отписке требует причины.** Это единственное действие
сервиса, которое разрешает написать тому, кто просил не писать: донор
пишет «пишите всё-таки», и вернуть его надо уметь, — но след обязан
остаться, иначе через полгода на вопрос «почему мы ему писали» ответить
будет нечем. Причина уходит в журнал вместе с именем снявшего.

**Запись человека снимается без объяснений.** Список поставщиков и
ручные исключения — наше собственное решение, а не чужая просьба:
требовать объяснение за отмену своего же решения значит приучить
писать «не нужно» в поле, которое потом читают как согласие донора.

**Срок ставится только на своё решение.** Требование исключает тех,
у кого агентство размещалось «за последние 12 месяцев», — это окно,
а не приговор. Отписка и жалоба срока не получают ни при каких
условиях: их заводит не человек, а страница отписки и приём ответов,
и поля они не заполняют вовсе. Здесь это ещё и запрещено явно —
причина с чужим решением руками не заводится.

**Домен — тем ключом, которым его пишет база.** Ссылка, `www.`, поддомен,
порт и регистр сводятся к корню сайта тем же `donors/host.py`, что у прогона.
До этого стоп-лист срезал только схему и косую черту и искал домен точным
совпадением: `www.` и поддомен донора заводились новыми строками `domains`,
донор оставался открыт, а экран говорил «в стоп-листе» (проверка QA 10.10.2026).

**Запись этапа продаж — только с правом «Продажи»** (решение Anthony 10.10.2026, П2б):
без права её нет в списке и в числах над ним (`rows(stages=…)`), а снять или завести
такую запись маршрут не даст. Запись без этапа держит все этапы — её видят все, как было.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import stages
from backend.features.core.domain import (
    MessageStatus,
    Stage,
    SuppressionReason,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel
from backend.features.donors.host import normalize_host
from backend.shared.net.url_parts import split_url

#: Причины, за которыми стоит решение адресата, а не наше. Снять такую
#: запись можно, но только назвав причину: письмо после неё уходит тому,
#: кто просил не писать.
DONOR_DECISION = (SuppressionReason.UNSUBSCRIBED, SuppressionReason.COMPLAINED)

#: Причины, которые человек заводит сам с экрана. Отписка и жалоба сюда
#: не входят: их заводят страница отписки и приём ответов.
HAND_REASONS = (SuppressionReason.MANUAL, SuppressionReason.SUPPLIER)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Метка домена: буквы любого алфавита, цифры, дефис не с краю, до 63 знаков.
#: Кириллица — потому что база хранит домен в том написании, в каком его дал
#: источник (`normalize_host` его не меняет), и `пример.рф` в ней бывает, а
#: проверка одной латиницей отказывала ему «не похоже на домен».
_LABEL = r"[^\W_](?:(?:[^\W_]|-){0,61}[^\W_])?"
_HOST_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")

#: Длиннее не бывает: домен — предел DNS и ширина `domains.host`, адрес — ширина
#: `suppressions.email`. Без своей проверки отказывал разбор запроса по-английски,
#: а мимо него — база пятисоткой.
HOST_MAX = 253
EMAIL_MAX = 255
#: Вписанное целиком: ссылка с путём длиннее своего домена, и предел у неё свой.
ENTERED_MAX = 2048


class StopListError(ValueError):
    """Со списком так нельзя. Сообщение говорит, почему."""


@dataclass(frozen=True, slots=True)
class StopRow:
    """Строка списка в том виде, в каком её читает человек."""

    id: int
    host: str | None
    email: str | None
    reason: SuppressionReason
    stage: Stage | None
    created_by: str | None
    created_at: datetime
    #: Докуда держит. Пусто — навсегда.
    expires_at: datetime | None = None

    @property
    def target(self) -> str:
        return self.host or self.email or "—"

    def expired(self, moment: datetime) -> bool:
        """Срок вышел: запись видна в списке, но письма больше не держит.

        Такие строки не удаляются сами. Запись исчезла бы вместе
        с ответом на вопрос «почему ему полгода не писали», а он
        и есть главный вопрос к этому экрану.
        """
        return self.expires_at is not None and self.expires_at <= moment

    @property
    def donor_decision(self) -> bool:
        """Решение адресата, а не наше: снимается только с причиной."""
        return self.reason in DONOR_DECISION


@dataclass(frozen=True, slots=True)
class AddedRow(StopRow):
    """Строка, только что заведённая руками: знаком ли адресат базе и что запись сняла."""

    #: Домена в базе не было, запись завела его сама. Донора с таким доменом
    #: нет: вписан сайт, которого прогоны ещё не видели, опечатка или поддомен
    #: в зоне, корня которой список суффиксов не знает. Экран говорит это
    #: словами — «письма сняты с очереди» здесь читалось как «донор закрыт».
    new_domain: bool = False
    #: Адрес базе не знаком (`_Addressee.known`): ни у одного донора или рекламодателя
    #: его нет, писем на него не было. Тот же ответ, что у домена: незнакомый адрес
    #: заводился зелёным «письма сняты с очереди» (проверка прода 10.10.2026).
    new_address: bool = False
    #: Сколько писем запись сняла — из очереди и со сроков добивок (`stop_pending`).
    #: Экран называет число, а не обещает «сняты», когда снимать было нечего.
    stopped: int = 0


async def rows(session: AsyncSession, *, stages: Collection[Stage]) -> list[StopRow]:
    """Весь список видимых этапов (`stages`), свежие сверху: записи без этапа и записи
    этапов из `stages`. Умолчания у этапов нет (ревью продаж к #304)."""
    found = await session.execute(
        select(SuppressionModel, DomainModel.host)
        .outerjoin(DomainModel, DomainModel.id == SuppressionModel.domain_id)
        .where(or_(SuppressionModel.stage.is_(None), SuppressionModel.stage.in_(stages)))
        .order_by(SuppressionModel.created_at.desc(), SuppressionModel.id.desc())
    )
    return [
        StopRow(
            id=row.id,
            host=host,
            email=row.email,
            reason=row.reason,
            stage=row.stage,
            created_by=row.created_by,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )
        for row, host in found.all()
    ]


async def add(
    session: AsyncSession,
    target: str,
    *,
    reason: SuppressionReason,
    stage: Stage | None = None,
    expires_at: datetime | None = None,
    author: str,
) -> AddedRow:
    """Завести запись руками: домен целиком или один адрес.

    Домен — сайт целиком: ссылка, `www.` и поддомен сводятся к корню, как
    у прогона (`site_of`). Домен, которого мы ещё не видели, заводится
    строкой в `domains`: список поставщиков приходит раньше первого прогона,
    и ждать, пока донор найдётся сам, значит написать ему до того.

    Срок необязателен и по умолчанию его нет: запись держит, пока её
    не снимут. С ним запись перестаёт держать сама — так выражается
    «размещались за последние 12 месяцев».
    """
    if reason not in HAND_REASONS:
        raise StopListError(
            f"Причину «{reason.value}» ставит сам сервис, руками её не заводят. "
            "Руками — «вручную» и «поставщик»"
        )
    if expires_at is not None and expires_at <= datetime.now(UTC):
        raise StopListError(
            "Срок записи уже прошёл — такая запись не удержит ни одного письма. "
            "Поставьте будущую дату или оставьте поле пустым: пусто значит «навсегда»"
        )
    entered = target.strip().lower()
    if len(entered) > ENTERED_MAX:
        raise StopListError(
            f"Вписано длиннее {ENTERED_MAX} знаков — здесь ждут домен, ссылку на сайт "
            "или адрес почты"
        )
    if _EMAIL_RE.match(entered):
        return await _add_email(
            session, entered, reason=reason, stage=stage, expires_at=expires_at, author=author
        )
    host = site_of(entered)
    if host:
        return await _add_host(
            session, host, reason=reason, stage=stage, expires_at=expires_at, author=author
        )
    raise StopListError(f"«{target.strip()}» не похоже ни на домен, ни на адрес почты")


def site_of(entered: str) -> str:
    """Домен сайта тем ключом, которым его пишет база. Не домен — пустая строка.

    `https://WWW.Blog.Example.co.uk:8080/path` → `example.co.uk`: схема, путь,
    порт, регистр и поддомен уходят тем же `normalize_host`, что у прогона, — его
    отбор и проверка перед отправкой ищут запись по этому ключу. Зоны, которой
    нет в списке суффиксов (`.test`, `.local`), корнем не свести: от хоста
    отходит только `www.`, как у обхода (`crawl/links.py`).

    Знак «@» — не домен: `a@b@c.test` разбор адреса прочёл бы как `c.test`.
    """
    if "@" in entered:
        return ""
    host = _hostname(entered)
    if len(host) > HOST_MAX:
        raise StopListError(
            f"Домен длиннее {HOST_MAX} знаков — таких не бывает. Проверьте, что вставилось в поле"
        )
    site = normalize_host(host) or host.removeprefix("www.")
    return site if _HOST_RE.match(host) and _HOST_RE.match(site) else ""


def _hostname(entered: str) -> str:
    """Хост из ссылки или голого домена: схема, путь, порт и точка в конце — прочь."""
    split = split_url(entered if "//" in entered else f"//{entered}")
    return ((split.hostname if split else None) or "").rstrip(".")


async def remove(session: AsyncSession, row_id: int, *, reason: str | None) -> StopRow:
    """Снять запись. Решение адресата снимается только с причиной."""
    row = await session.get(SuppressionModel, row_id)
    if row is None:
        raise StopListError(f"Записи №{row_id} в стоп-листе нет")

    host = None
    if row.domain_id is not None:
        domain = await session.get(DomainModel, row.domain_id)
        host = domain.host if domain is not None else None

    taken = StopRow(
        id=row.id,
        host=host,
        email=row.email,
        reason=row.reason,
        stage=row.stage,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )
    if taken.donor_decision and not (reason or "").strip():
        raise StopListError(
            f"«{taken.target}» просил больше не писать (причина «{row.reason.value}»). "
            "Снять такую запись можно, но надо написать, почему: причина уйдёт в журнал"
        )
    await session.delete(row)
    return taken


async def stop_pending(
    session: AsyncSession, *, domain_id: int | None = None, email: str | None = None
) -> int:
    """Снять с очереди и со сроков всё, что этому адресату предстояло.

    Одной строки стоп-листа мало. Проверка перед отправкой откажет, но
    письмо до тех пор висит в очереди как готовое, а добивка живёт
    не в очереди, а сроком у уже отправленного письма: пока срок цел,
    адресат остаётся в планах. Видно это стало бы только отказом
    в момент отправки — то есть человеку, а не в базе.

    Адресат по адресу — строка `contacts` у доноров и рекламодателей; у писем
    продаж её нет, адрес живёт у лида. Их переписки называет модуль продаж
    через мост (`stages.sales_threads_to`): почта продажи не импортирует.
    """
    if domain_id is not None:
        return await _stop(session, MessageModel.domain_id == domain_id)
    return await _stop(session, (await _addressee(session, email)).letters())


@dataclass(frozen=True, slots=True)
class _Addressee:
    """Чей это адрес в базе: строки `contacts` доноров и рекламодателей и переписки продаж,
    чей лид — этот адрес. Одно правило и для «знаком ли адрес», и для «какие письма снять»:
    иначе экран назвал бы знакомым адрес, письма которому запись не нашла, или наоборот."""

    contacts: tuple[int, ...]
    sales_threads: tuple[int, ...]

    @property
    def known(self) -> bool:
        """Знаком ли адрес. Лида, которому ещё не писали, знает только модуль продаж: мост
        называет переписки, а не лидов, — и такой адрес здесь незнаком, писем ему не было."""
        return bool(self.contacts or self.sales_threads)

    def letters(self) -> ColumnElement[bool]:
        """Письма этому адресу: по строке `contacts` или в переписке продаж."""
        addressed: ColumnElement[bool] = MessageModel.contact_id.in_(self.contacts)
        if self.sales_threads:
            addressed = or_(addressed, MessageModel.thread_id.in_(self.sales_threads))
        return addressed


async def _addressee(session: AsyncSession, email: str | None) -> _Addressee:
    if not email:
        return _Addressee(contacts=(), sales_threads=())
    contacts = await session.scalars(select(ContactModel.id).where(ContactModel.email == email))
    sales = await stages.sales_threads_to(session, email)
    return _Addressee(contacts=tuple(contacts), sales_threads=tuple(sales))


async def _stop(session: AsyncSession, addressed: ColumnElement[bool]) -> int:
    """Снять письма адресату: из очереди — в «снято», у ушедших — срок добивки."""
    found = await session.execute(
        select(MessageModel).where(
            or_(
                MessageModel.status == MessageStatus.QUEUED,
                MessageModel.next_action_at.is_not(None),
            ),
            addressed,
        )
    )
    stopped = 0
    for message in found.scalars().all():
        if message.status is MessageStatus.QUEUED:
            message.status = MessageStatus.STOPPED
        message.next_action_at = None
        stopped += 1
    return stopped


async def _add_host(
    session: AsyncSession,
    host: str,
    *,
    reason: SuppressionReason,
    stage: Stage | None,
    expires_at: datetime | None,
    author: str,
) -> AddedRow:
    found = await session.execute(select(DomainModel).where(DomainModel.host == host))
    domain = found.scalars().first()
    new_domain = domain is None
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()

    await _refuse_duplicate(session, SuppressionModel.domain_id == domain.id, stage, host)
    row = SuppressionModel(
        domain_id=domain.id,
        reason=reason,
        stage=stage,
        expires_at=expires_at,
        created_by=author,
    )
    session.add(row)
    await session.flush()
    stopped = await stop_pending(session, domain_id=domain.id)
    return AddedRow(
        id=row.id,
        host=host,
        email=None,
        reason=reason,
        stage=stage,
        created_by=author,
        created_at=row.created_at,
        expires_at=expires_at,
        new_domain=new_domain,
        stopped=stopped,
    )


async def _add_email(
    session: AsyncSession,
    email: str,
    *,
    reason: SuppressionReason,
    stage: Stage | None,
    expires_at: datetime | None,
    author: str,
) -> AddedRow:
    if len(email) > EMAIL_MAX:
        raise StopListError(f"Адрес длиннее {EMAIL_MAX} знаков — таких не бывает")
    await _refuse_duplicate(session, SuppressionModel.email == email, stage, email)
    row = SuppressionModel(
        email=email, reason=reason, stage=stage, expires_at=expires_at, created_by=author
    )
    session.add(row)
    await session.flush()
    # Знаком ли адрес и что ему снять — одним ответом: модуль продаж спрошен один раз.
    addressee = await _addressee(session, email)
    stopped = await _stop(session, addressee.letters())
    return AddedRow(
        id=row.id,
        host=None,
        email=email,
        reason=reason,
        stage=stage,
        created_by=author,
        created_at=row.created_at,
        expires_at=expires_at,
        new_address=not addressee.known,
        stopped=stopped,
    )


async def _refuse_duplicate(
    session: AsyncSession, same_target: object, stage: Stage | None, shown: str
) -> None:
    """Второй записи на того же адресата не заводим.

    Молчаливое согласие завело бы список, где один донор лежит трижды,
    и снятие одной записи выглядело бы как возврат, которым оно не было.

    Истёкшая запись помехой не считается: она уже никого не держит,
    и отказ завести новую означал бы «поставщика, у которого кончился
    срок, вернуть нельзя» — то есть срок, который нельзя продлить.
    """
    found = await session.execute(
        select(SuppressionModel.id).where(
            same_target,  # type: ignore[arg-type]
            or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == stage),
            SuppressionModel.in_force(datetime.now(UTC)),
        )
    )
    if found.first() is not None:
        raise StopListError(f"«{shown}» уже в стоп-листе")
