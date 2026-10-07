"""Что должно случиться после ответа.

Решение отделено от его применения нарочно: применение — это запросы
к базе, а решение — правила, и правила должны проверяться без базы.
Здесь их семь, и каждое оплачено чужим опытом.

**Ответ останавливает цепочку добивок.** Писать дальше человеку, который
уже ответил, — неуважение, и он справедливо так это и воспримет.

**Автоответчик не останавливает.** «Я в отпуске до понедельника» не значит
«мне неинтересно»; оборвав на нём цепочку, мы потеряем донора ни на чём.
Но автоответ с суммой в валюте ждёт человека: автоответы модель не
разбирает, и цена из прайса тикет-системы или отпускной подписи иначе
пропала бы молча.

**Отписка блокирует адрес, а не донора.** Требование «больше не пишите»
сильнее и распространяется на весь сайт, но решает это человек: разница
между «уберите меня из рассылки» и «мы не работаем с рекламой» видна
из текста, а не из факта отписки.

**Отказ доставки метит контакт и открывает следующий адрес.** Писать
на несуществующий ящик — значит жечь репутацию своего домена, а у донора
обычно есть и другие адреса.

**Адрес, с которого ответили, становится предпочтительным.** Дальше пишем
тому, кто отвечает, а не в ящик, где письмо пролежало неделю. Кроме робота:
noreply предпочтительным не становится (`robots`), иначе следующее письмо
донору ушло бы в ящик, который письма выбрасывает.

**Цена ниже порога уверенности не попадает в базу.** Она остаётся
при ответе и ждёт человека: приёмка требует не более 5% ошибок, и без
этой ветки порог не держится.

**Ответ рекламодателя — лид, а не цена.** Этап 2 предлагает ему покупать
размещения дешевле, и «мы платим $300 за статью» в ответе — его расход,
а не цена площадки. Разобранный как цена донора, он лёг бы в карточку
сайта, который заодно бывает донором, и стал бы ценой, которой никто
не называл. Такой ответ ведёт человек.

**Ответ лида продаж уходит своей очереди и ничего не пишет здесь.** Вид
ответа разбирает модуль продаж (`to_sales_queue`), а донорский разбор положил
бы сумму из него ценой в карточку донора, адрес — в контакты домена.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, assert_never

from backend.config import outreach as cfg
from backend.features.core.domain import ReplyKind, Stage
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import MAX_TEXT_CHARS
from backend.features.replies.money import amounts_in
from backend.features.replies.quoting import written_by_hand

#: Почему ответ рекламодателя ждёт человека — словами, для карточки.
ADVERTISER_LEAD = "ответ рекламодателя — лид: цену не разбираем, его ведёт человек"

#: Почему ответ лида продаж ждёт — словами, для карточки: вид ответа ещё не разобран.
SALES_WAITING = "ответ продаж ждёт разбора вида: задача в очереди продаж"

#: Почему автоответ ждёт человека — словами, для карточки.
AUTO_REPLY_WITH_SUM = (
    "автоответ с суммой в валюте — модель автоответы не разбирает, цену смотрит человек"
)


@dataclass(frozen=True, slots=True)
class Consequences:
    """Что делать с ответом. Ни одного запроса к базе, только решение."""

    #: Не слать добивки по этой цепочке.
    stop_chain: bool = False
    #: Адрес в стоп-лист.
    suppress_email: bool = False
    #: Пометить контакт негодным и открыть следующий адрес донора.
    mark_contact_dead: bool = False
    #: Запомнить адрес, с которого ответили, как предпочтительный.
    remember_answering_address: bool = False
    #: Перенести цену в карточку донора.
    store_price: bool = False
    #: Отправить разбор человеку.
    needs_review: bool = False
    #: Почему ждёт человека — словами, для карточки.
    review_reason: str | None = None
    #: Донор уверенно сказал «не продаём» — ответ ложится на домен.
    store_declines: bool = False
    #: Донор уверенно сказал «бесплатно возьмём» — тоже на домен, как согласие.
    store_free: bool = False
    #: Донор уверенно сказал «продаём», но цены не назвал: цену ждёт человек,
    #: а ответ «продаёт» на домен ложится сразу — он от цены не зависит.
    store_sells: bool = False


def decide(
    kind: ReplyKind,
    found: Extracted | None = None,
    *,
    threshold: float | None = None,
    stage: Stage = Stage.DONORS,
    names_a_sum: bool = False,
) -> Consequences:
    """Последствия одного ответа. Этап — этап рассылки, на письмо которой ответили;
    `names_a_sum` — есть ли в написанном донором сумма в валюте (`names_a_sum()`)."""
    limit = cfg.PRICE_CONFIDENCE_THRESHOLD if threshold is None else threshold

    if kind is ReplyKind.BOUNCE:
        # Ящика нет — писать туда больше некуда, следующий адрес берём сразу.
        return Consequences(stop_chain=True, mark_contact_dead=True)

    if kind is ReplyKind.UNSUBSCRIBE:
        return Consequences(stop_chain=True, suppress_email=True)

    if kind is ReplyKind.AUTO_REPLY:
        return _auto_reply(stage, names_a_sum=names_a_sum)

    return _human_answer(stage, found, limit)


def _human_answer(stage: Stage, found: Extracted | None, limit: float) -> Consequences:
    """Ответил человек. Что это значит, решает этап рассылки — разбором целиком."""
    match stage:
        case Stage.DONORS:
            return _donor_answer(found, limit)
        case Stage.ADVERTISERS:
            # Ответил рекламодатель: цепочка кончилась, дальше — человек.
            return Consequences(
                stop_chain=True,
                remember_answering_address=True,
                needs_review=True,
                review_reason=ADVERTISER_LEAD,
            )
        case Stage.SALES:
            # Цепочка кончилась и здесь; адрес — не контакт донора, и в
            # контакты домена его не пишем: следующее письмо донору ушло бы лиду.
            return Consequences(stop_chain=True, needs_review=True, review_reason=SALES_WAITING)
        case _:
            assert_never(stage)


def _auto_reply(stage: Stage, *, names_a_sum: bool) -> Consequences:
    """Автоответчик — это тишина, а не событие: ни остановки, ни отметок.

    Кроме суммы в валюте в том, что он написал. Автоответ с прайсом бывает —
    тикет-система с `Precedence: bulk`, отпускная подпись с тарифом, — и цена
    в нём тоже цена донора. Вид остаётся автоответом, цепочка добивок идёт,
    как шла, а ответ ждёт человека тем же путём, что неуверенный разбор:
    модель автоответы не разбирает, и без человека цена пропала бы молча.
    Ответ рекламодателя сюда не относится: его сумма — его расход, а не цена;
    у продаж цены нет вовсе.
    """
    match stage:
        case Stage.DONORS if names_a_sum:
            return Consequences(needs_review=True, review_reason=AUTO_REPLY_WITH_SUM)
        case Stage.DONORS | Stage.ADVERTISERS | Stage.SALES:
            return Consequences()
        case _:
            assert_never(stage)


def priced_by_model(stage: Stage | None) -> bool:
    """Отдаётся ли ответ человека разбору цены моделью — одно правило для приёма
    и повтора вебхука. Только ответ донора: у рекламодателя — лид, у продаж
    разбора ещё нет, непривязанный ответ сначала смотрит человек."""
    match stage:
        case Stage.DONORS:
            return True
        case Stage.ADVERTISERS | Stage.SALES | None:
            return False
        case _:
            assert_never(stage)


#: Что из треда продаж уходит модулю продаж: вид ответа человека (модель),
#: перенос шага по автоответу и отписка во всех направлениях (без модели).
#: Отказ доставки решают правила выше целиком, как у всех этапов.
TO_SALES = frozenset({ReplyKind.HUMAN, ReplyKind.AUTO_REPLY, ReplyKind.UNSUBSCRIBE})


def to_sales_queue(kind: ReplyKind, stage: Stage | None) -> bool:
    """Уходит ли ответ очереди продаж (`queue.SALES_REPLY_JOB`) — одно правило
    для приёма и повтора вебхука. Вид ответа (человек, автоответ, отписка)
    решили правила приёма; модель зовётся только для ответа человека."""
    return stage is Stage.SALES and kind in TO_SALES


def sales_review(snapshot: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Ждёт ли ответ продаж человека и почему — по снимку разбора вида.

    Вид и путь решает модуль продаж и кладёт в снимок ответа (`model_parse`
    с `"stage": "sales"`), ждёт ли ответ человека (`waits`) и почему (`reason`).
    Здесь они только читаются: почта продаж не знает (`mail-does-not-know-sales`).
    Снимка нет — вид ещё не разобран, ответ ждёт.
    """
    if not snapshot or snapshot.get("stage") != Stage.SALES.value:
        return True, SALES_WAITING
    reason = snapshot.get("reason")
    return snapshot.get("waits") is not False, str(reason) if reason else SALES_WAITING


def sales_closed_address(snapshot: Mapping[str, Any] | None) -> bool:
    """Закрыл ли модуль продаж адрес по ответу человека («просит не писать»
    словами, без человека) — диалог тогда «отписался», как при отписке правилами."""
    return bool(
        snapshot
        and snapshot.get("stage") == Stage.SALES.value
        and snapshot.get("route") == "unsubscribe"
        and snapshot.get("waits") is False
    )


def names_a_sum(text: str) -> bool:
    """Есть ли в письме сумма в валюте — в том, что написал донор.

    Та же часть письма, что читают правила вида и модель: начало, без
    цитаты — в цитате наше письмо. По сохранённому тексту считается так же,
    как при приёме, поэтому «ждёт человека» у автоответа не хранится,
    а выводится: правка словаря валют доходит и до старых ответов.
    """
    return bool(amounts_in(written_by_hand(text[:MAX_TEXT_CHARS])))


def _donor_answer(found: Extracted | None, limit: float) -> Consequences:
    """Ответил донор: цена, её отсутствие или ответ на главный вопрос без неё."""
    answered = _answer_without_price(found, limit)
    if answered is not None:
        return answered

    if found is None or not found.has_price:
        # Ответ без распознанной цены — это тоже очередь на разбор,
        # а не пустое место: «ответили, цена не распознана» стоит прямым
        # фильтром в docs/WEB_LAYER.md. Цена могла быть во вложении,
        # в картинке или просто сказана так, как модель не поняла.
        return Consequences(
            stop_chain=True,
            remember_answering_address=True,
            needs_review=True,
            review_reason="цена в ответе не распознана",
        )

    confident = found.confidence >= limit
    return Consequences(
        stop_chain=True,
        remember_answering_address=True,
        store_price=confident,
        needs_review=not confident,
        review_reason=None if confident else _why(found, limit),
    )


def _answer_without_price(found: Extracted | None, limit: float) -> Consequences | None:
    """Ответ на главный вопрос письма без цены: «не продаём», «бесплатно»
    или «продаём, но цену не назвал».

    Разбирать тут человеку нечего — цены не будет ни в одном из двух, — но
    это ответы, а не пустое место: для гест-постинга первый убирает домен
    из отбора, второй подтверждает его.
    """
    if found is None or found.has_price or found.confidence < limit:
        return None
    if found.placement == "declines":
        return Consequences(stop_chain=True, remember_answering_address=True, store_declines=True)
    if found.placement == "free":
        return Consequences(stop_chain=True, remember_answering_address=True, store_free=True)
    if found.placement == "sells":
        # «Продаём», а цены нет: её ждёт человек, но ответ «продаёт» от цены
        # не зависит и ложится на домен сразу.
        return Consequences(
            stop_chain=True,
            remember_answering_address=True,
            needs_review=True,
            review_reason="продаёт, но цену не назвал",
            store_sells=True,
        )
    return None


def _why(found: Extracted, limit: float) -> str:
    """Почему разбор ушёл человеку. Проценты, а не доли: это читает человек."""
    head = f"уверенность {round(found.confidence * 100)}% при пороге {round(limit * 100)}%"
    if not found.notes:
        return head
    return f"{head}; {'; '.join(found.notes)}"


def waiting_for_review(
    kind: ReplyKind,
    confidence: float | None,
    *,
    reviewed: bool,
    names_a_sum: bool = False,
) -> bool:
    """Ждёт ли разбор человека.

    Считается, а не хранится отдельным полем: второе поле разошлось бы
    с уверенностью при первой же правке порога, и очередь показывала бы
    не то, что в ней есть.

    Ждут ответы людей — и автоответ с суммой в валюте (`names_a_sum`,
    только на письма доноров — это решает вызывающий): его цену модель
    не разбирала. У остальных автоответов и у отказа доставки разбирать
    нечего, и держать их в очереди значит топить её тем, по чему решений
    не принимают.
    """
    if reviewed:
        return False
    if kind is ReplyKind.AUTO_REPLY:
        return names_a_sum
    if kind is not ReplyKind.HUMAN:
        return False
    return (confidence or 0.0) < cfg.PRICE_CONFIDENCE_THRESHOLD
