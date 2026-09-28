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
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.features.core.domain import Stage
from backend.features.letters import compose, guards, template, unsubscribe
from backend.features.letters.sending import check_ready, own_headers
from backend.features.letters.transport import Outgoing

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


def letter_for(stage: Stage) -> compose.Letter:
    """Текст пробного письма: шаблон этапа по умолчанию, подстановки пробные.

    Громкие метки на месте незаданного остаются, как в письме донору:
    по ним `check_ready` и откажет — тем же объяснением.
    """
    link = PROBE_LINK if stage is Stage.ADVERTISERS else None
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
