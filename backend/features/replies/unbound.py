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
номер письма из метки — в адресах, на которые он пришёл. Слов два вида, и оба
здесь: метка в несколько слов для строки списка (`explain_short`) и слова
целиком — что случилось и что с ответом делать (`explain`).

**Ответ на ящик продаж — только с правом «Продажи»** (решение Anthony 10.10.2026,
П2; ревью стыков 12.10.2026). Этап у такого ответа — по нашему адресу, на который
он пришёл (`recipient_stage.py`): страница, число вкладки и число в «Обзоре»
считают ответы этапов, которые видит спрашивающий (`stages`). Нашего адреса нет —
этапа нет, и ответ виден всем, как был.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.letters import reply_to
from backend.features.letters.probe import PROBE_MESSAGE_ID
from backend.features.replies import recipient_stage
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

#: Кого ищут по ответу без письма — словом его направления: на ящик продаж отвечает лид,
#: на ящик Этапа 2 — рекламодатель (11.11.2026; до этого у всех стояло «донора»).
_WHOM: dict[Stage, str] = {
    Stage.DONORS: "Донора",
    Stage.ADVERTISERS: "Рекламодателя",
    Stage.SALES: "Лида",
}

#: Направление неизвестно: нашего адреса среди адресов ответа нет — или причину говорит
#: приём, который направления не спрашивает (`pipeline`). Слово прежнее.
_WHOM_UNKNOWN = "Донора"

#: Отправителя такого ответа ищут глазами — по отправителю и тексту. `{whom}` — кого именно.
_BY_HAND = "{whom} ищут по отправителю и тексту."


def is_unbound() -> ColumnElement[bool]:
    """Ответ не привязан ни к диалогу, ни к письму."""
    return and_(ReplyModel.thread_id.is_(None), ReplyModel.message_id.is_(None))


async def page(
    session: AsyncSession, *, stages: Collection[Stage], page: int = 1, size: int = PAGE_SIZE
) -> list[ReplyModel]:
    """Страница непривязанных, которые видит спрашивающий (`stages`), новые первыми.
    Номер страницы — с единицы.

    Порядок — по номеру ответа, и он полный: номер уникален, и ответ со стыка
    страниц не покажется на обеих. Страница за концом — пустая, а не отказ:
    число страниц экран считает по `total`.
    """
    shown = (await _shown(session, stages))[(page - 1) * size : page * size]
    if not shown:
        return []
    rows = await session.execute(
        select(ReplyModel).where(ReplyModel.id.in_(shown)).order_by(ReplyModel.id.desc())
    )
    return list(rows.scalars().all())


async def total(session: AsyncSession, *, stages: Collection[Stage]) -> int:
    """Сколько непривязанных видит спрашивающий — то же условие, что у страницы."""
    if not frozenset(Stage) - frozenset(stages):
        return int(await session.scalar(select(func.count(ReplyModel.id)).where(is_unbound())) or 0)
    return len(await _shown(session, stages))


async def _shown(session: AsyncSession, stages: Collection[Stage]) -> list[int]:
    """Номера непривязанных ответов этапов `stages` и без этапа — новые первыми. Текстов
    не берёт: текст до двухсот тысяч знаков (`inbound.MAX_BODY_CHARS`). Адреса — только когда
    спрашивающий видит не все этапы: этап по нашему адресу нужен, чтобы скрыть чужой, а число
    меню у того, кто видит всё, адресов не берёт (`test_threads_lean`)."""
    hidden = frozenset(Stage) - frozenset(stages)
    if not hidden:
        found = await session.scalars(
            select(ReplyModel.id).where(is_unbound()).order_by(ReplyModel.id.desc())
        )
        return list(found)
    rows = (
        await session.execute(
            select(ReplyModel.id, ReplyModel.to_addresses)
            .where(is_unbound())
            .order_by(ReplyModel.id.desc())
        )
    ).all()
    domains = await recipient_stage.known_domains(session)
    return [
        number
        for number, addresses in rows
        if recipient_stage.stage_of(addresses, domains) not in hidden
    ]


async def stages_of(
    session: AsyncSession, replies: Sequence[ReplyModel]
) -> dict[int, Stage | None]:
    """Направление каждого ответа страницы — по нашему адресу, на который он пришёл: тем же
    правилом, каким страница прячет чужой этап (`_shown`). Один вопрос о доменах на страницу,
    а не на строку; пустой странице он не нужен. `None` — нашего адреса среди адресов нет."""
    if not replies:
        return {}
    domains = await recipient_stage.known_domains(session)
    return {reply.id: recipient_stage.stage_of(reply.to_addresses, domains) for reply in replies}


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


#: Причина → слова. `{number}` — « №417», если номер из метки известен; `{whom}` —
#: кого ищут по ответу (`_WHOM`).
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


#: Причина → метка в несколько слов для строки списка: продолжает «Почему не привязан: …».
#: Говорит, что случилось; что с таким ответом делать — в словах целиком (`_WORDS`). До
#: 11.11.2026 строка несла слова целиком — два десятка слов, почти у всех ответов одни и те же.
_SHORT: dict[str, str] = {
    Unbound.NO_SUCH_LETTER: "письма из метки нет в базе",
    Unbound.BAD_SIGNATURE: "подпись метки не сошлась",
    Unbound.FOREIGN_THREAD: "ответ на чужое или пересланное письмо",
    Unbound.NO_LABEL: "написано заново, не ответом на наше письмо",
}


def _answers_probe(reason: str | None, number: int | None) -> bool:
    """Ответ на пробное письмо: метка верная, а письма с её номером не бывает."""
    return reason == Unbound.NO_SUCH_LETTER and number == PROBE_MESSAGE_ID


def explain(reason: str | None, addresses: Sequence[str] | None, stage: Stage | None = None) -> str:
    """Почему ответ не привязан — словами для человека.

    `stage` — направление ответа по нашему адресу (`stages_of`): кого по нему ищут. Без
    направления — донора: так говорит и приём, который его не спрашивает."""
    number = labelled(addresses)
    if _answers_probe(reason, number):
        return _PROBE
    if reason is None:
        return "Причина не записана: ответ принят до того, как её стали сохранять."
    words = _WORDS.get(reason)
    if words is None:
        return f"Причина записана кодом, которого этот сервер не знает ({reason})."
    whom = _WHOM_UNKNOWN if stage is None else _WHOM.get(stage, _WHOM_UNKNOWN)
    return words.format(number=f" №{number}" if number is not None else "", whom=whom)


def explain_short(reason: str | None, addresses: Sequence[str] | None) -> str:
    """Почему ответ не привязан — меткой в несколько слов, по тому же коду, что `explain`.

    Причина остаётся на виду в каждой строке списка, а слова целиком экран показывает по
    запросу. Код неизвестной причины метка не называет: он стоит в словах целиком."""
    if _answers_probe(reason, labelled(addresses)):
        return "ответ на пробное письмо"
    if reason is None:
        return "причина не записана"
    return _SHORT.get(reason, "неизвестная причина")
