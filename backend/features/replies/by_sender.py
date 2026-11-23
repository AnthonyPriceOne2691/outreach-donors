"""Привязка по отправителю — третья, запасная ступень после метки и цепочки (`binding`).

Человек открыл почту и написал на адрес From ящика заново: ни метки в адресе, ни заголовков
цепочки (`Unbound.NO_LABEL`). Такой ответ уходил человеку во вкладку «Не привязаны», хотя кому
мы писали с этого адреса, база знает. Ступень привязывает его — но только там, где ошибиться
нечем; иначе ответ остаётся человеку, как раньше.

- **Только без метки и цепочки.** Чужая цепочка, неверная подпись и письмо, которого нет, —
  свои случаи со своим действием человека (`Unbound`), а привязанное не трогается.
- **Направление — по нашему адресу, на который пришёл ответ** (`recipient_stage`): ящики
  доноров, рекламодателей и продаж разные, и ответ на ящик продаж адресата среди доноров
  не ищет. Нашего адреса нет — искать не в чем.
- **Отправитель — не наш ящик.** Письмо с нашего домена рассылки — пробное, пересылка коллеги
  или петля, а не ответ адресата.
- **Отправитель — подтверждённый.** «От кого» подделать легко: это просто поле письма, а метка
  подписана. Засчитывается одно из двух: подпись DKIM с доменом из «От кого» и итогом pass —
  или SPF pass, когда домен обратного адреса конверта тот же. Домены сравниваются точно, без
  регистра: подпись поддомена или домена организации — не подпись домена «От кого», и такой
  ответ — человеку. Проверка — до поисков: неподтверждённому отправителю адресата не ищем.
- **Адрес — целиком**, без регистра и краевых пробелов, как сверяет получателя сверка событий
  доставки (`letters/events.py`). Тот же домен — не тот же человек: `info@` и `editor@`
  одного сайта — разные ящики.
- **Адресат — один.** Считаются адресаты, а не письма: строка контакта или лида. Один адресат
  с письмами в двух рассылках направления — один, и ответ ложится к его последнему письму.
  Две строки с тем же адресом (два сайта, две гипотезы) — два адресата: какой диалог
  продолжает человек, по адресу не понять.

Метка и цепочка по-прежнему первые: пересланное письмо с нового адреса здесь не находит никого
и остаётся человеку, а донор определяется по получателю (`binding`). Отправитель решает, только
когда больше решать нечему, и только однозначно и подтверждённо (`docs/OUTREACH_THREADS.md`).
Итоги проверок подлинности приходят в нейтральном виде (`Incoming.dkim_passed` и соседи): вид
платформы знает только `mime.py`.

**Адресат письма продаж — лид переписки, и знает его модуль продаж**, а почта модуль продаж
не импортирует (контракт `mail-does-not-know-sales` в `.importlinter`). Поэтому поиски
внедряются: здесь — тип поиска, поиск по контактам (`mail_letters`) и правило, поиск по лидам —
в продажах (`sales/reply_sender.py`), а соединяет их маршрут приёма (`api/inbound/routes.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import assert_never

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.replies import recipient_stage
from backend.features.replies.binding import Binding, BindingWay, Unbound
from backend.features.replies.inbound import Incoming

logger = logging.getLogger(__name__)

#: Поиск адресатов по адресу отправителя (строчными, без краевых пробелов) среди наших ушедших
#: писем этапа: пары «номер письма — ключ адресата», последнее письмо первым (`sent_at`, затем
#: номер — по убыванию). Ключ называет и таблицу: `contact:7` и `lead:7` — два адресата.
SenderLookup = Callable[[AsyncSession, str, Stage], Awaitable[list[tuple[int, str]]]]
#: Поиски, которыми приём ищет адресата (`Inbox(lookups=…)`).
SenderLookups = Sequence[SenderLookup]


async def mail_letters(session: AsyncSession, address: str, stage: Stage) -> list[tuple[int, str]]:
    """Адресаты-контакты: письмо доноров и рекламодателей уходит контакту письма (`contact_id`).

    У продаж — пусто: письмо продаж уходит лиду переписки (`core/stages.recipient`), а контакт
    письма продаж, если он есть, — тот, кто ответил, а не адресат (`repository.mark_dead`)."""
    if not _to_contact(stage):
        return []
    rows = await session.execute(
        select(MessageModel.id, MessageModel.contact_id)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .join(ContactModel, ContactModel.id == MessageModel.contact_id)
        .where(
            MessageModel.sent_at.is_not(None),
            CampaignModel.stage == stage,
            func.lower(func.trim(ContactModel.email)) == address,
        )
        .order_by(MessageModel.sent_at.desc(), MessageModel.id.desc())
    )
    return [(letter, f"contact:{contact}") for letter, contact in rows.tuples()]


#: Поиски по умолчанию — только контакты: лида знает модуль продаж, и его поиск добавляет маршрут
#: приёма (`api/inbound/routes.py`).
MAIL: tuple[SenderLookup, ...] = (mail_letters,)


async def bind(
    session: AsyncSession, incoming: Incoming, bound: Binding, lookups: SenderLookups
) -> Binding:
    """Ответ без метки и цепочки — к последнему письму единственного адресата направления
    с адресом отправителя (`BindingWay.SENDER`), если отправитель подтверждён (`_confirmed`).
    Иначе — `bound` как был: ответ — человеку."""
    if bound.unbound is not Unbound.NO_LABEL:
        return bound
    address = incoming.from_email.strip().lower()
    stage = await _direction(session, incoming.to, address) if address else None
    if stage is None or not _confirmed(incoming, address, stage):
        return bound
    found = [pair for lookup in lookups for pair in await lookup(session, address, stage)]
    letter = _the_one(found, stage)
    return bound if letter is None else Binding(message_id=letter, way=BindingWay.SENDER)


async def _direction(session: AsyncSession, to: Sequence[str], sender: str) -> Stage | None:
    """Направление ответа — по нашему адресу, на который он пришёл. `None` — искать не в чем:
    нашего адреса нет или отправитель — наш же ящик, то есть его домен — наш домен рассылки или
    его поддомен (то же правило, `recipient_stage.stage_of`). Домены — одним вопросом на обе
    проверки."""
    domains = await recipient_stage.known_domains(session)
    stage = recipient_stage.stage_of(to, domains)
    if stage is None or recipient_stage.stage_of((sender,), domains) is None:
        return stage
    logger.info(
        "приём: ответ от нашего ящика — по отправителю не привязан (направление %s)",
        stage.value,
        extra={"stage": stage.value},
    )
    return None


def _confirmed(incoming: Incoming, address: str, stage: Stage) -> bool:
    """Подтверждён ли отправитель: подпись DKIM домена его адреса прошла — или прошёл SPF,
    и обратный адрес конверта того же домена. Домены — точно, без регистра: поддомен и домен
    организации — другие домены (ослабленного выравнивания DMARC нет)."""
    domain = _domain(address)
    signed = {_bare(name) for name in incoming.dkim_passed}
    spf = incoming.spf_passed and _domain(incoming.envelope_from) == domain
    if domain and (domain in signed or spf):
        return True
    logger.info(
        "приём: по отправителю не привязан — отправитель не подтверждён (dkim/SPF), направление %s",
        stage.value,
        extra={
            "stage": stage.value,
            "dkim_passed": len(signed),
            "spf_passed": incoming.spf_passed,
        },
    )
    return False


def _domain(address: str) -> str:
    """Домен адреса для сравнения (`_bare`). Нет «@» — пусто: домен не назван."""
    _, at, domain = address.rpartition("@")
    return _bare(domain) if at else ""


def _bare(domain: str) -> str:
    """Домен для сравнения — строчными, без краевых пробелов и точки в конце: то же имя."""
    return domain.strip().rstrip(".").lower()


def _the_one(found: Sequence[tuple[int, str]], stage: Stage) -> int | None:
    """Последнее письмо единственного адресата (`SenderLookup` отдаёт его первым). `None` —
    адресата нет или их несколько: какой диалог продолжает человек, по адресу не понять."""
    addressees = len({addressee for _, addressee in found})
    if addressees > 1:
        logger.info(
            "приём: по отправителю не привязан — адресатов с этим адресом в направлении %s: %s",
            stage.value,
            addressees,
            extra={"stage": stage.value, "addressees": addressees},
        )
    if addressees != 1:
        return None
    letter = found[0][0]
    logger.info(
        "приём: ответ привязан по отправителю — письмо №%s, направление %s",
        letter,
        stage.value,
        extra={"letter": letter, "stage": stage.value},
    )
    return letter


def _to_contact(stage: Stage) -> bool:
    """Уходит ли письмо этапа контакту письма. Этап разобран целиком, как у моста этапов
    (`core/stages`): новый этап не станет «как доноры» молча."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return True
        case Stage.SALES:
            return False
        case _:
            assert_never(stage)
