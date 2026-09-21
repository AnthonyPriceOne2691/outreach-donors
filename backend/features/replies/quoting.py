"""Что в ответе написал человек, а что процитировала почта.

Файл существует из-за одной ловушки, и она дороже, чем кажется.
**В цитате лежит наше собственное письмо** — вместе с юридическим блоком,
в котором написано «unsubscribe here». Поиск по всему тексту пометил бы
отпиской каждый ответ подряд, донор ушёл бы в стоп-лист, и виновата
была бы не почта, а мы.

То же и с ценой: в цитате наш вопрос про стоимость, а не ответ на него.
Модель, которой отдали письмо целиком, читает оба и отвечает на первый.

**Отрезаем по началу цитаты, а не по её содержимому.** Признаков цитаты
немного, они устойчивы и их видно целиком — список внизу. Угадывать,
какой абзац «похож на наш», значило бы отрезать написанное человеком.

**Если отрезать нечего, берём текст целиком.** Ответ снизу под цитатой —
законный обычай, и письмо, из которого мы вырезали всё, хуже письма
с лишней цитатой.
"""

from __future__ import annotations

import re

#: Строка, с которой начинается цитата. Порядок значения не имеет:
#: берём самое раннее совпадение по тексту.
_QUOTE_STARTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^>", re.M), "строка с «>»"),
    (re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.M | re.I), "Original Message"),
    (re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}", re.M | re.I), "Forwarded message"),
    # «On Mon, 21 Sep 2026 at 10:04, Anna Ro <…> wrote:» — и его переносы.
    (re.compile(r"^\s*On\b.{0,300}?\bwrote:\s*$", re.M | re.I | re.S), "On … wrote:"),
    (re.compile(r"^\s*\S.{0,200}?\bwrote:\s*$", re.M | re.I), "… wrote:"),
    # «21.09.2026, 10:04, Анна Ро <…> написал(а):»
    (re.compile(r"^\s*\d{1,2}[.:/]\d{1,2}.{0,200}?(написал|пишет)", re.M | re.I), "… написал:"),
    # Outlook: блок заголовков вместо фразы.
    (re.compile(r"^\s*From:\s+.+$(?=\n\s*(Sent|To|Subject):)", re.M | re.I), "блок From:"),
    (re.compile(r"^\s*От:\s+.+$(?=\n\s*(Отправлено|Кому|Тема):)", re.M | re.I), "блок От:"),
    (re.compile(r"^_{5,}\s*$", re.M), "черта из подчёркиваний"),
)

#: Подпись отправителя: всё после неё принадлежит ему, а не разговору.
#: Отрезается отдельно от цитаты, потому что стоит до неё.
_SIGNATURE = re.compile(r"^-- \s*$", re.M)


def quote_starts_at(text: str) -> tuple[int | None, str | None]:
    """Где начинается цитата и по какому признаку её узнали.

    Признак возвращается не для ветвления, а для отчёта: если цитаты
    вдруг перестали узнаваться, это видно по числу, а не по жалобе.
    """
    earliest: int | None = None
    reason: str | None = None
    for pattern, title in _QUOTE_STARTS:
        found = pattern.search(text)
        if found is not None and (earliest is None or found.start() < earliest):
            earliest, reason = found.start(), title
    return earliest, reason


def written_by_hand(text: str) -> str:
    """Та часть письма, которую написал человек.

    Пустой результат не возвращается никогда: письмо, из которого мы
    вырезали всё, хуже письма с лишней цитатой.
    """
    if not text:
        return ""

    at, _ = quote_starts_at(text)
    head = text[:at] if at is not None else text

    signature = _SIGNATURE.search(head)
    if signature is not None:
        head = head[: signature.start()]

    head = head.strip()
    return head or text.strip()
