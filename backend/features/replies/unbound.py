"""Ответы без письма: не привязались ни к одному нашему письму.

Приём их не выбрасывает (`binding`): молча отброшенный ответ выглядит как
«донор не ответил». Но сохранённый и невидимый — то же самое, только без
шанса заметить: до 28.09.2026 такие ответы лежали в базе, а видеть их было
негде. Здесь — страница для экрана «Не привязаны» и причина словами.

**Не привязан — значит ни диалога, ни письма.** Одного пустого поля мало:
у ответа, чьё письмо удалили, письма нет, а диалог есть — и он по-прежнему
ответ своего донора, его видно в переписке.

**Причина — словами сервера, одним местом.** Экран, вебхук приёма и всё, что
появится потом, говорят о ней одинаково: второй экземпляр слов разошёлся бы
с первым на первой же правке. Код причины лежит у ответа (`unbound_reason`),
номер письма из метки — в адресах, на которые он пришёл.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.outreach import ReplyModel
from backend.features.letters import reply_to
from backend.features.letters.probe import PROBE_MESSAGE_ID
from backend.features.replies.binding import Unbound
from backend.features.replies.inbound import MAX_TEXT_CHARS
from backend.features.replies.quoting import written_by_hand

#: Ответов на странице — как у очереди форм. Размер называет сервер, экран
#: узнаёт его из ответа: второй экземпляр числа на фронте разошёлся бы с этим.
PAGE_SIZE = 20

#: Больше за раз не отдаём: у каждого ответа текст целиком, а это до двухсот
#: тысяч знаков (`inbound.MAX_BODY_CHARS`).
MAX_PAGE_SIZE = 100

#: Начало текста в строке списка. Полный текст — в раскрытой строке.
PREVIEW_CHARS = 160

#: Донора такого ответа ищут глазами — по отправителю и тексту.
_BY_HAND = "Донора ищут по отправителю и тексту."


def is_unbound() -> ColumnElement[bool]:
    """Ответ не привязан ни к диалогу, ни к письму."""
    return and_(ReplyModel.thread_id.is_(None), ReplyModel.message_id.is_(None))


async def page(session: AsyncSession, *, page: int = 1, size: int = PAGE_SIZE) -> list[ReplyModel]:
    """Страница непривязанных, новые первыми. Номер страницы — с единицы.

    Порядок — по номеру ответа, и он полный: номер уникален, и ответ со стыка
    страниц не покажется на обеих. Страница за концом — пустая, а не отказ:
    число страниц экран считает по `total`.
    """
    rows = await session.execute(
        select(ReplyModel)
        .where(is_unbound())
        .order_by(ReplyModel.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    return list(rows.scalars().all())


async def total(session: AsyncSession) -> int:
    """Сколько непривязанных всего — то же условие, что у страницы."""
    return int(await session.scalar(select(func.count(ReplyModel.id)).where(is_unbound())) or 0)


def preview(text: str) -> str:
    """Начало того, что написал человек, — одной строкой.

    Без цитаты: в ней лежит наше собственное письмо, и начало ответа
    «Hi Anna, thank you for…» ничего не говорило бы, если бы строка
    начиналась с нашей же цитаты (`quoting`). Цитата ищется в начале письма,
    а не во всём: строке нужны первые полторы сотни знаков, а письмо бывает
    в двести тысяч — и таких на странице двадцать.
    """
    line = " ".join(written_by_hand(text[:MAX_TEXT_CHARS]).split())
    if len(line) <= PREVIEW_CHARS:
        return line
    return line[: PREVIEW_CHARS - 1].rstrip() + "…"


def labelled(addresses: Sequence[str] | None) -> int | None:
    """Номер письма из метки в адресах ответа. `None` — метки нет или адреса
    не записаны. Подпись не проверяется: это объяснение, а не привязка."""
    for address in addresses or ():
        number = reply_to.labelled_number(address)
        if number is not None:
            return number
    return None


#: Причина → слова. `{number}` — « №417», если номер из метки известен.
#: Каждая причина говорит, что с ответом делать, а не только что случилось:
#: «подпись не сошлась» у одного ответа — случайность, у всех подряд —
#: сменённый секрет, и разбираться надо с ним, а не с ответами.
_WORDS: dict[str, str] = {
    Unbound.NO_SUCH_LETTER: (
        "Метка указывает на письмо{number}, а такого письма в базе нет: его удалили вместе "
        "с рассылкой — или оно ушло с другой установки с тем же секретом приёма ответов. "
        + _BY_HAND
    ),
    Unbound.BAD_SIGNATURE: (
        "В адресе метка письма{number}, но подпись не сошлась: письмо ушло с другим секретом "
        "приёма ответов (его сменили или письмо отправила другая установка) — или метку "
        "подделали. Если так встают все ответы подряд — дело в секрете."
    ),
    Unbound.FOREIGN_THREAD: (
        "Метки в адресе нет, а заголовки цепочки не совпали ни с одним нашим письмом: "
        "ответили на пересланное или чужое письмо. " + _BY_HAND
    ),
    Unbound.NO_LABEL: (
        "Метки в адресе нет, и заголовков цепочки тоже: письмо написано заново, "
        "а не ответом на наше. " + _BY_HAND
    ),
}

#: Пробное письмо — тот же «письма нет», но новость обратная: всё работает.
_PROBE = (
    f"Ответ на пробное письмо: его метка — письмо №{PROBE_MESSAGE_ID}, а писем с таким номером "
    "в базе не бывает. Так и проверяется, что ответы доходят: раз ответ здесь, поддомен ответов "
    "и приём работают."
)


def explain(reason: str | None, addresses: Sequence[str] | None) -> str:
    """Почему ответ не привязан — словами для человека."""
    number = labelled(addresses)
    if reason == Unbound.NO_SUCH_LETTER and number == PROBE_MESSAGE_ID:
        return _PROBE
    if reason is None:
        return "Причина не записана: ответ принят до того, как её стали сохранять."
    words = _WORDS.get(reason)
    if words is None:
        return f"Причина записана кодом, которого этот сервер не знает ({reason})."
    return words.format(number=f" №{number}" if number is not None else "")
