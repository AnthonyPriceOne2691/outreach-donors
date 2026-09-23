"""Что должно случиться после ответа.

Решение отделено от его применения нарочно: применение — это запросы
к базе, а решение — правила, и правила должны проверяться без базы.
Здесь их шесть, и каждое оплачено чужим опытом.

**Ответ останавливает цепочку добивок.** Писать дальше человеку, который
уже ответил, — неуважение, и он справедливо так это и воспримет.

**Автоответчик не останавливает.** «Я в отпуске до понедельника» не значит
«мне неинтересно»; оборвав на нём цепочку, мы потеряем донора ни на чём.

**Отписка блокирует адрес, а не донора.** Требование «больше не пишите»
сильнее и распространяется на весь сайт, но решает это человек: разница
между «уберите меня из рассылки» и «мы не работаем с рекламой» видна
из текста, а не из факта отписки.

**Отказ доставки метит контакт и открывает следующий адрес.** Писать
на несуществующий ящик — значит жечь репутацию своего домена, а у донора
обычно есть и другие адреса.

**Адрес, с которого ответили, становится предпочтительным.** Дальше пишем
тому, кто отвечает, а не в ящик, где письмо пролежало неделю.

**Цена ниже порога уверенности не попадает в базу.** Она остаётся
при ответе и ждёт человека: приёмка требует не более 5% ошибок, и без
этой ветки порог не держится.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import outreach as cfg
from backend.features.core.domain import ReplyKind
from backend.features.replies.extract import Extracted


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
) -> Consequences:
    """Последствия одного ответа."""
    limit = cfg.PRICE_CONFIDENCE_THRESHOLD if threshold is None else threshold

    if kind is ReplyKind.BOUNCE:
        # Ящика нет — писать туда больше некуда, следующий адрес берём сразу.
        return Consequences(stop_chain=True, mark_contact_dead=True)

    if kind is ReplyKind.UNSUBSCRIBE:
        return Consequences(stop_chain=True, suppress_email=True)

    if kind is ReplyKind.AUTO_REPLY:
        # Ничего: ни остановки, ни отметок. Автоответчик — это тишина,
        # а не событие.
        return Consequences()

    # Ответил человек.
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


def waiting_for_review(kind: ReplyKind, confidence: float | None, *, reviewed: bool) -> bool:
    """Ждёт ли разбор человека.

    Считается, а не хранится отдельным полем: второе поле разошлось бы
    с уверенностью при первой же правке порога, и очередь показывала бы
    не то, что в ней есть.

    Ждут только ответы людей. У автоответчика и отказа доставки разбирать
    нечего, и держать их в очереди значит топить её тем, по чему решений
    не принимают.
    """
    if reviewed or kind is not ReplyKind.HUMAN:
        return False
    return (confidence or 0.0) < cfg.PRICE_CONFIDENCE_THRESHOLD
