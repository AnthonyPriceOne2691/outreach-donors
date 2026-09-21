"""Чем был ответ: человеком, автоответчиком, отказом доставки, отпиской.

От этого зависит, останавливать ли цепочку, и обе ошибки стоят дорого
в разные стороны.

**Автоответчик, принятый за ответ, обрывает цепочку ни на чём.**
«Я в отпуске до понедельника» не значит «мне неинтересно», а добивки
после него не уйдут — и половина цепочек умрёт молча.

**Ответ, принятый за автоответчик, продолжает добивки человеку, который
уже ответил.** Он справедливо сочтёт это неуважением.

Поэтому правила лежат списком и разбираются по порядку, а не цепочкой
`if`: список видно целиком, и переставить в нём строку — значит
изменить правило осознанно. Каждое правило называет себя, и название
попадает в отчёт: по нему видно, чем именно мы отличаем одно от другого,
и какая доля писем не подошла ни под одно.

**Смотрим на то, что написал человек, а не на письмо целиком.**
В цитате лежит наше собственное письмо со словом «unsubscribe» в юр.
блоке — поиск по всему тексту пометил бы отпиской каждый ответ подряд.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from backend.features.core.domain import ReplyKind
from backend.features.replies.inbound import Incoming
from backend.features.replies.quoting import written_by_hand


@dataclass(frozen=True, slots=True)
class Verdict:
    """Чем был ответ и по какому признаку мы это решили."""

    kind: ReplyKind
    #: Название сработавшего правила. Пусто — не сработало ни одно,
    #: и письмо считается написанным человеком.
    rule: str | None = None

    @property
    def guessed(self) -> bool:
        """Решено по умолчанию, а не по признаку.

        Отдельное число в отчёте: доля «не знаю» у ступени, которая ничего
        не отсеивает, — единственный способ заметить, что она сломалась.
        """
        return self.rule is None


_Check = Callable[[Incoming, str], bool]


def _header_says(incoming: Incoming, name: str, *values: str) -> bool:
    got = incoming.header(name).strip().lower()
    return bool(got) and any(v in got for v in values)


def _bounce_by_headers(incoming: Incoming, _: str) -> bool:
    """Отказ доставки узнаётся по служебным заголовкам, а не по тексту.

    Пустой обратный путь (`Return-Path: <>`) — признак письма, на которое
    отвечать некому: так почта помечает свои собственные уведомления.
    """
    empty_return_path = incoming.header("Return-Path").strip() == "<>"
    return (
        empty_return_path
        or bool(incoming.header("X-Failed-Recipients"))
        or _header_says(incoming, "Content-Type", "report-type=delivery-status")
    )


_BOUNCE_SUBJECT = re.compile(
    r"undelivered|delivery (status notification|has failed|failure)|"
    r"mail delivery (failed|subsystem)|returned to sender|"
    r"не доставлено|недоставленное",
    re.I,
)
_BOUNCE_BODY = re.compile(
    r"\b5\.[0-7]\.\d\b|\b55\d\b|mailbox (is )?unavailable|"
    r"user unknown|recipient address rejected|does not exist|no such user",
    re.I,
)
_UNSUBSCRIBE = re.compile(
    r"\bunsubscribe\b|\bremove me\b|take me off|stop (emailing|contacting)|"
    r"do not (contact|email) (me|us)|не пиш[иу]те|отпишите|удалите меня",
    re.I,
)
_AUTO_SUBJECT = re.compile(
    r"out of (the )?office|auto(matic)?[ -]?(reply|response)|autoreply|"
    r"vacation|away from|в отпуске|автоответ",
    re.I,
)

#: Правило: имя, вид ответа и проверка. Порядок и есть договорённость.
#:
#: Отказ доставки первым: уведомление почты несёт и признаки
#: автоответчика — служебные заголовки у них общие.
#:
#: Отписка раньше автоответчика: требование «больше не пишите», присланное
#: автоматом, остаётся требованием, и последствие у него сильнее.
RULES: tuple[tuple[str, ReplyKind, _Check], ...] = (
    ("отказ доставки по заголовкам", ReplyKind.BOUNCE, _bounce_by_headers),
    (
        "отказ доставки по теме",
        ReplyKind.BOUNCE,
        lambda inc, _: bool(_BOUNCE_SUBJECT.search(inc.subject or "")),
    ),
    (
        "отказ доставки по коду в тексте",
        ReplyKind.BOUNCE,
        lambda _inc, body: bool(_BOUNCE_BODY.search(body)),
    ),
    (
        "отписка в тексте",
        ReplyKind.UNSUBSCRIBE,
        lambda _inc, body: bool(_UNSUBSCRIBE.search(body)),
    ),
    (
        "автоответчик по заголовкам",
        ReplyKind.AUTO_REPLY,
        lambda inc, _: (
            _header_says(inc, "Auto-Submitted", "auto-generated", "auto-replied")
            or bool(inc.header("X-Autoreply"))
            or bool(inc.header("X-Autorespond"))
            or _header_says(inc, "Precedence", "auto_reply", "bulk", "junk")
        ),
    ),
    (
        "автоответчик по теме",
        ReplyKind.AUTO_REPLY,
        lambda inc, _: bool(_AUTO_SUBJECT.search(inc.subject or "")),
    ),
    (
        "автоответчик по тексту",
        ReplyKind.AUTO_REPLY,
        lambda _inc, body: bool(_AUTO_SUBJECT.search(body)),
    ),
)


def classify(incoming: Incoming) -> Verdict:
    """Чем был ответ. Ни одно правило не сработало — значит, человек."""
    body = written_by_hand(incoming.text)
    for name, kind, matches in RULES:
        if matches(incoming, body):
            return Verdict(kind=kind, rule=name)
    return Verdict(kind=ReplyKind.HUMAN)


#: Виды, которые останавливают цепочку добивок.
#:
#: Автоответчик не останавливает намеренно: «я в отпуске до понедельника»
#: не значит «мне неинтересно», и оборвать на нём цепочку — потерять
#: донора ни на чём. Отказ доставки останавливает, но по другой причине:
#: писать на несуществующий адрес значит жечь репутацию своего домена.
STOPS_THE_CHAIN = frozenset({ReplyKind.HUMAN, ReplyKind.UNSUBSCRIBE, ReplyKind.BOUNCE})


def stops_chain(kind: ReplyKind) -> bool:
    return kind in STOPS_THE_CHAIN
