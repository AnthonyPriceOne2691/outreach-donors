"""Путь ответа лида продаж по виду — решение кода, без базы.

Вид называет модель (`reply_kind.py`), путь решает код (урок соседней системы: «нужен ли
ответ» решает код): таблица `ROUTES`; ниже порога уверенности (`SALES_REPLY_CONFIDENCE`) —
ручная очередь продаж, какой бы вид ни назвали. Что путь делает сразу и что из этого
вышло — задача ответа (`replies.py`).

**Дал телефон — передача** (слово владельца 26.10): номер для связи, который проверил код
(`reply_kind.phones_of`), значит, с человеком говорить, — лида передают телемаркетологу при
любой уверенности в виде. Кроме отказа, «не сейчас» и «не пишите» (`NO_CALL`): номер в их
подписи — не просьба позвонить. Вид не разобран — человек, как без номера. Номер рядом с
чужим адресом — тоже передача: адрес назван словами, новый лид на него не заводится.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.features.sales.reply_kind import KindFound, SalesKind


class Route(StrEnum):
    """Куда ответ уходит после вида."""

    HANDOFF = "handoff"  # хочет говорить — передача лида на созвон
    AGENT = "agent"  # вопрос, интерес — ответит агент; пока — человек
    REFERRAL = "referral"  # назвал другого — новый лид
    CLOSED = "closed"  # не интересно, не сейчас — диалог закрыт
    UNSUBSCRIBE = "unsubscribe"  # просит не писать — адрес закрыт
    MANUAL = "manual"  # вид не разобран, ниже порога, модель не ответила


#: Путь вида при уверенности не ниже порога.
ROUTES: dict[SalesKind, Route] = {
    SalesKind.WANTS_TO_TALK: Route.HANDOFF,
    SalesKind.QUESTION: Route.AGENT,
    SalesKind.INTERESTED: Route.AGENT,
    SalesKind.REFERRAL: Route.REFERRAL,
    SalesKind.NOT_INTERESTED: Route.CLOSED,
    SalesKind.NOT_NOW: Route.CLOSED,
    SalesKind.UNSUBSCRIBE: Route.UNSUBSCRIBE,
    SalesKind.PARSE_FAILED: Route.MANUAL,
}

#: Виды, при которых номер телефона в ответе — не повод звонить: отказ, «не сейчас», «не пишите».
NO_CALL = frozenset({SalesKind.NOT_INTERESTED, SalesKind.NOT_NOW, SalesKind.UNSUBSCRIBE})

#: Пути, после которых ответ человека не ждёт (если путь удался — `replies._follow`).
SETTLED = frozenset({Route.CLOSED, Route.REFERRAL, Route.UNSUBSCRIBE})

#: Пути, после которых агент продаж готовит черновик ответа (`workers/sales_jobs.py`), если
#: он включён тумблером `SALES_AGENT_ENABLED` и настроен: «ответит агент» — вопрос и интерес.
#: «Хочет говорить» — без черновика: лида передают телемаркетологу (`replies.pass_on`); решение ждёт
#: подтверждения владельца, и черновик там — `Route.HANDOFF` в этот набор, одной строкой.
#: Отправить такой черновик не даст отдельное правило: лиду, переданному телемаркетологу,
#: письма продаж не идут (`mail.unwritable`) — при смене решения его решают вместе.
DRAFTED = frozenset({Route.AGENT})

#: Вид словами — для причины в карточке.
KIND_WORDS: dict[SalesKind, str] = {
    SalesKind.WANTS_TO_TALK: "хочет говорить",
    SalesKind.QUESTION: "задал вопрос",
    SalesKind.INTERESTED: "интересуется",
    SalesKind.REFERRAL: "назвал другого человека",
    SalesKind.NOT_INTERESTED: "не интересно",
    SalesKind.NOT_NOW: "не сейчас",
    SalesKind.UNSUBSCRIBE: "просит не писать",
    SalesKind.PARSE_FAILED: "вид ответа не разобран",
}

#: Что дальше по пути — словами.
ROUTE_WORDS: dict[Route, str] = {
    Route.HANDOFF: "передать лида на созвон; пока — человек",
    Route.AGENT: "ответит агент; пока — человек",
    Route.REFERRAL: "новый лид на названный адрес",
    Route.CLOSED: "диалог закрыт",
    Route.UNSUBSCRIBE: "адрес закрыт во всех направлениях",
    Route.MANUAL: "решает человек",
}

#: Почему передача, когда человек дал телефон, — словами; первая часть — и в итоге передачи
#: (`reply_handoff._settled`): «задал вопрос, дал телефон для связи: передан телемаркетологу».
GAVE_PHONE = "дал телефон для связи"
PHONE_WORDS = f"{GAVE_PHONE} — передать на созвон"


@dataclass(frozen=True, slots=True)
class Decision:
    """Путь ответа, ждёт ли он человека и почему — словами."""

    route: Route
    waits: bool
    reason: str


def _percent(share: float) -> int:
    return round(share * 100)


def decide(found: KindFound, threshold: float) -> Decision:
    """Путь по виду — решение кода, без базы. Дал телефон — передача при любой уверенности,
    кроме `NO_CALL`; иначе ниже порога — человек."""
    words = KIND_WORDS[found.kind]
    notes = "".join(f"; {note}" for note in found.notes)
    if found.kind is SalesKind.PARSE_FAILED:
        return Decision(Route.MANUAL, True, f"{words}{notes} — {ROUTE_WORDS[Route.MANUAL]}")
    if gave_phone(found):
        return Decision(Route.HANDOFF, True, _by_phone(found, threshold))
    if found.confidence < threshold:
        level = f"уверенность {_percent(found.confidence)}% ниже порога {_percent(threshold)}%"
        return Decision(Route.MANUAL, True, f"модель: {words}, {level}{notes} — решает человек")
    route = ROUTES[found.kind]
    return Decision(route, route not in SETTLED, f"{words}: {ROUTE_WORDS[route]}")


def gave_phone(found: KindFound) -> bool:
    """Человек дал номер для связи, и вид — не отказ, не «не сейчас» и не «не пишите»."""
    return bool(found.phones) and found.kind not in NO_CALL


def _by_phone(found: KindFound, threshold: float) -> str:
    """Почему передача по телефону — словами: вид и уверенность, если она ниже порога; чужой
    адрес назван, новый лид на него не заводится — звонит телемаркетолог."""
    doubt = ""
    if found.confidence < threshold:
        doubt = f" (уверенность {_percent(found.confidence)}% ниже порога {_percent(threshold)}%)"
    reason = f"{KIND_WORDS[found.kind]}{doubt}, {PHONE_WORDS}"
    if found.contact:
        reason += f"; назван адрес {found.contact} — новый лид не заводится"
    return reason
