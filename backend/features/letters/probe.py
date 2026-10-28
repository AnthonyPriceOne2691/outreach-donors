"""Пробное письмо себе: проверка домена отправки, которая не тратит донора.

До него настройку домена можно было проверить только первым письмом
донору — то есть сжечь донора: письмо на донора одно за этап, и письмо
с битой подписью или с адресом ответа в никуда второго шанса не даёт.

**Письмо то же, что уйдёт донору, кроме адресата.** Текст — шаблон этапа
по умолчанию с пробными подстановками, From — ящик рассылки, адрес ответа
с меткой, свой `Message-ID` и ссылка отписки собираются тем же кодом,
что у настоящей отправки (`sending.own_headers`, `sending.check_ready`).
Проверяется ровно то, что получит донор, а не похожее письмо.

**Модель не зовётся.** Переписывание зон — платный вызов, а проверяется
не текст, а то, как письмо доходит: подпись, адрес ответа, заголовки.

**Номер письма 0 зарезервирован.** Номера писем в базе начинаются
с единицы, и метка `m0` не укажет ни на одно настоящее письмо: ответ
на пробное письмо примется как непривязанный и встанет на вкладку
«Не привязаны» экрана диалогов с причиной «ответ на пробное письмо»
(`replies/unbound.py`) — этого и достаточно, чтобы убедиться, что ответы
доходят.
События доставки с номером 0 не найдут письма и ничего не изменят
(`events.py`, «без письма»).

**Номер донора 0 — по той же причине.** Ссылка отписки с меткой `u0`
ведёт на страницу, которая на метку несуществующего донора отвечает
«ссылка не работает» и ничего не пишет — ни при открытии, ни при нажатии,
ни при отписке в один клик из почтового клиента (`api/unsubscribe/routes.py`).
Поэтому заголовок `List-Unsubscribe` в пробном письме есть — такой же,
как у донора.

**Адресат судится стоп-листом, как у настоящего письма этапа** (28.10.2026):
проба уходит транспорту мимо `Sending.send`, и правило отправки проверяется
здесь же, до сборки письма (`check_stoplist`). У продаж правило своё, шире
общего, — его даёт консоль (`StopRule`): почта модуль продаж не импортирует.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.letters import compose, guards, stoplist, template, unsubscribe
from backend.features.letters.sending import SuppressedError, check_ready, own_headers
from backend.features.letters.transport import Outgoing

logger = logging.getLogger(__name__)

#: Номер письма в метке адреса ответа и в `Message-ID`. Писем с таким
#: номером не бывает: последовательность номеров в базе начинается с единицы.
PROBE_MESSAGE_ID = 0

#: Номер донора в ссылке отписки — по той же причине.
PROBE_DOMAIN_ID = 0

#: Сайт, о котором пишет пробное письмо. Зарезервированный домен примеров
#: (RFC 2606): в тексте он никого не задевает и никуда не ведёт.
PROBE_HOST = "example.org"

#: Найденная ссылка для оффера рекламодателю — его шаблон без неё не собирается.
PROBE_LINK = compose.FoundLink(
    donor_host=PROBE_HOST,
    page_url=f"https://{PROBE_HOST}/blog/sample-article",
    anchor="sample anchor",
)


@dataclass(frozen=True, slots=True)
class Probe:
    """Пробное письмо и то, чего в нём нет и почему."""

    outgoing: Outgoing
    #: Почему в письме нет заголовка отписки: имя недостающей настройки.
    #: Пусто — заголовок есть.
    missing_unsubscribe: str = ""


@dataclass(frozen=True, slots=True)
class StopRule:
    """Своё правило стоп-листа у этапа, чьё письмо стоп-лист держит шире общего, — вместо
    условия этапа (`stoplist.holding(rule=…)`). Даёт его консоль (`cli/mail_test.py`):
    правило продаж живёт в модуле продаж, а почта его не импортирует."""

    #: Какие записи держат письмо этапа — условием SQL, со сроком.
    holds: ColumnElement[bool]
    #: Почему держит запись другого этапа — словами отказа.
    why: str


def letter_for(stage: Stage) -> compose.Letter:
    """Текст пробного письма: шаблон этапа по умолчанию, подстановки пробные.

    Громкие метки на месте незаданного остаются, как в письме донору:
    по ним `check_ready` и откажет — тем же объяснением.
    """
    link = PROBE_LINK if template.first_letter(stage).link else None
    rendered = compose.render(
        template.for_stage(stage),
        compose.values_for(host=PROBE_HOST, domain_id=PROBE_DOMAIN_ID, link=link),
    )
    letter = compose.assemble(rendered, {})
    guards.assert_no_metrics(letter.body)
    return letter


def compose_probe(*, to: str, sender_email: str, stage: Stage, real: bool) -> Probe:
    """Собрать пробное письмо. Ничего не пишет и никуда не шлёт.

    Отказы — те же, что у настоящей отправки (`SendError`): текст не готов,
    нет секрета для метки, у ящика нет домена.
    """
    letter = letter_for(stage)
    check_ready(letter.body, what="Пробное письмо")
    own = own_headers(PROBE_MESSAGE_ID, sender_email=sender_email, real=real)
    link = unsubscribe.url_for(PROBE_DOMAIN_ID)
    return Probe(
        outgoing=Outgoing(
            message_id=PROBE_MESSAGE_ID,
            to=to.strip(),
            from_email=sender_email,
            from_name=compose.values_for(host=PROBE_HOST)["sender_name"],
            reply_to=own.reply_to,
            subject=letter.subject,
            body=letter.body,
            internet_message_id=own.message_id,
            unsubscribe_url=link,
        ),
        missing_unsubscribe="" if link else unsubscribe.missing_setting(),
    )


async def check_stoplist(
    session: AsyncSession, *, to: str, stage: Stage, rule: StopRule | None = None
) -> None:
    """Пробное письмо — только адресу, которого не держит стоп-лист этапа `stage`.

    Правило — настоящего письма этапа. Общее — отправки (`stoplist.holding`): держит запись
    на адрес и запись на его домен — она держит все адреса домена; запись без этапа держит
    все этапы, истёкшая — никого. Своё правило этапа (`rule`) заменяет условие этапа: у
    продаж держат и записи других этапов, где человек сам отписался или пожаловался.
    Домен адреса — строка `domains` по хосту адреса, тем ключом, которым стоп-лист заводит
    домен (`stoplist.site_of`): поддомен почты и `www.` сводятся к корню сайта. Домена
    в базе нет — держать может только запись на адрес. Только читает: транзакция консоли
    открыта на чтение (`cli/mail_test.py`). Отказ — `SuppressedError`, как у отправки.
    """
    email = to.strip().lower()
    host = _site(email)
    domain_id = (
        await session.scalar(select(DomainModel.id).where(DomainModel.host == host))
        if host
        else None
    )
    held = await stoplist.holding(
        session,
        stage=stage,
        domain_ids=[] if domain_id is None else [domain_id],
        emails=[email],
        rule=None if rule is None else rule.holds,
    )
    if held:
        # Запись на сам адрес — первой: она называет его, а не весь домен.
        row = min(held, key=lambda found: (found.email != email, found.id))
        raise SuppressedError(_held_words(row, email=email, host=host, stage=stage, rule=rule))


def _site(email: str) -> str:
    """Домен адреса тем ключом, которым его пишет база (`stoplist.site_of`). Не домен — пусто."""
    try:
        return stoplist.site_of(email.rpartition("@")[2])
    except stoplist.StopListError:
        # Хост длиннее предела DNS: такого домена в базе не бывает, и держать может только
        # запись на адрес — как у строки файла поставщиков без домена (`supplier_import`).
        logger.debug("пробное письмо: у адреса %.80r хост — не домен", email)
        return ""


def _held_words(
    row: SuppressionModel, *, email: str, host: str, stage: Stage, rule: StopRule | None
) -> str:
    """Что держит пробу и почему — словами отказа: запись на адрес или на домен адреса.
    Запись другого этапа держит только своим правилом этапа — и слова называют его."""
    scope = "на всех этапах" if row.stage is None else f"этапа {row.stage.value}"
    why = f"{scope} (причина «{row.reason.value}»)"
    if rule is not None and row.stage not in (None, stage):
        why = f"{why} — {rule.why}"
    held = (
        f"адрес {email} в стоп-листе {why}: писать на него нельзя"
        if row.email == email
        else f"домен {host} в стоп-листе {why}: запись на домен держит все его адреса, "
        f"и {email} тоже"
    )
    return f"{held}. Пробное письмо — себе: на свой ящик, которого в списке нет"
