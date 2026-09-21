"""Что именно выдумано: домены рассылки, доноры, переписка, письма.

Отделено от того, как это записывается в базу (`demo_data.py`). Причина
простая: правят здесь и там по разным поводам. Текст письма и набор
состояний диалога меняют, когда экран чего-то не показывает; порядок
записи — когда меняется схема. В одном файле эти поводы смешивались,
и файл перевалил за предел длины на первом же добавлении.

**Всё выдуманное помечено зоной `.example.test`** — признак один на весь
сервис (`backend/shared/demo.py`), и по нему же нулевой транспорт решает,
можно ли отправлять.
"""

from __future__ import annotations

from decimal import Decimal

from backend.features.core.domain import ReplyKind
from backend.shared import demo

DEMO_SUFFIX = demo.SUFFIX

#: Домены рассылки: разные состояния нарочно — свежий в разгоне, зрелый,
#: выключенный по отказам. Экран, на котором все домены одинаковы,
#: не показывает ничего.
SENDER_DOMAINS: list[tuple[str, int, int, int | None, bool, str | None]] = [
    # домен, ящиков, дневной кап, день разгона (None — разгон закончен), включён, причина паузы
    ("mail-alpha" + DEMO_SUFFIX, 2, 20, None, True, None),
    ("mail-beta" + DEMO_SUFFIX, 2, 20, 3, True, None),
    ("mail-gamma" + DEMO_SUFFIX, 1, 20, 1, True, None),
    ("mail-delta" + DEMO_SUFFIX, 1, 20, None, False, "доля отказов 7% — парковка"),
]

#: Доноры и то, чем закончился разговор с каждым. Набор подобран так,
#: чтобы на экране встретились все состояния диалога.
CONVERSATIONS: list[tuple[str, str, str, Decimal | None, Decimal | None]] = [
    # донор, адрес, чем кончилось, цена белая, цена серая
    (
        "digest-weekly" + DEMO_SUFFIX,
        "editor@digest-weekly" + DEMO_SUFFIX,
        "цена",
        Decimal("250"),
        Decimal("180"),
    ),
    ("city-news" + DEMO_SUFFIX, "info@city-news" + DEMO_SUFFIX, "цена", Decimal("400"), None),
    ("tech-review" + DEMO_SUFFIX, "ads@tech-review" + DEMO_SUFFIX, "ответ", None, None),
    ("green-blog" + DEMO_SUFFIX, "hello@green-blog" + DEMO_SUFFIX, "автоответ", None, None),
    ("travel-mag" + DEMO_SUFFIX, "editor@travel-mag" + DEMO_SUFFIX, "ждём", None, None),
    ("home-guide" + DEMO_SUFFIX, "contact@home-guide" + DEMO_SUFFIX, "ждём", None, None),
    ("food-diary" + DEMO_SUFFIX, "team@food-diary" + DEMO_SUFFIX, "отказ доставки", None, None),
    ("auto-parts" + DEMO_SUFFIX, "sales@auto-parts" + DEMO_SUFFIX, "отписка", None, None),
]

LETTER_BODY = (
    "Здравствуйте!\n\nПишу по поводу размещения статьи на вашем сайте. "
    "Подскажите, пожалуйста, стоимость размещения и есть ли условия "
    "по тематике.\n\nС уважением,\nотдел контента"
)

REPLIES = {
    "цена": (
        ReplyKind.HUMAN,
        "Здравствуйте!\n\nРазмещение статьи — {white} EUR, с пометкой «партнёрский "
        "материал» — {grey} EUR. Оплата по счёту или картой. Размещаем в течение "
        "трёх рабочих дней.\n\nС уважением,\nредакция",
    ),
    "ответ": (
        ReplyKind.HUMAN,
        "Добрый день! Прайс уточняю у главного редактора, вернусь с ответом на следующей неделе.",
    ),
    "автоответ": (
        ReplyKind.AUTO_REPLY,
        "Я в отпуске до понедельника. По срочным вопросам пишите коллеге.",
    ),
    "отказ доставки": (
        ReplyKind.BOUNCE,
        "Delivery has failed to these recipients: mailbox unavailable (550 5.1.1).",
    ),
    "отписка": (
        ReplyKind.UNSUBSCRIBE,
        "Просьба больше не писать на этот адрес.",
    ),
}


#: Письма, ждущие отправки: донор и то, как модель переписала его зоны.
#: Три случая нарочно — отличие в коридоре, ниже и выше: очередь,
#: где все письма одинаковы, не показывает ничего.
#:
#: **Здесь лежит текст, а не процент.** Процент считается по тексту тем же
#: правилом, что и в бою. Проставленный руками, он разъезжается с письмом
#: рядом — и экран показывает «19%» над текстом, отличающимся на три.
#: Ровно это и нашёл живой прогон.
QUEUE: list[tuple[str, dict[str, str]]] = [
    (
        "repair-guide",
        {
            "greeting": "Good afternoon,",
            "opening": (
                "I have spent a few evenings with {{host}} lately, and the way you handle "
                "your subject sits well with what my clients are after."
            ),
            "ask": (
                "Could you share what a placement costs on your side? If the price changes "
                "when the piece carries a sponsored label, both figures would help."
            ),
        },
    ),
    # Приветствие совпадает с шаблонным: так выглядит письмо, у которого
    # модель отказала и зоны остались шаблонными. Отличие честные ноль.
    ("garden-notes", {"greeting": "Hi there,"}),
    (
        "kitchen-daily",
        {
            "greeting": "Hello and good day to you,",
            "opening": (
                "I came across {{host}} while looking for places my clients could reasonably "
                "appear in, and what you publish lines up with the sort of material they put "
                "their name to."
            ),
            "ask": (
                "What would a placement run to? And if the number moves depending on whether "
                "the article is labelled as sponsored, I would rather know both up front."
            ),
        },
    ),
]
