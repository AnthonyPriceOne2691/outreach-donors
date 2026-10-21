"""Чьим именем подписана страница отписки и на каком языке она говорит — по письму метки.

**Этап — по метке письма, а не по тексту.** Метка письма продаж несёт номер письма
(`letters/unsubscribe.letter_url`): номер → письмо → его рассылка → этап. Метка донора
(без номера), письмо другого этапа и письмо не того сайта (метку подписал не этот
сервис: стенд, база из копии) — подпись доноров (`OUTREACH_*`) по-английски, как было.

**Письмо продаж — подпись «Отправителя» продаж:** имя и физический адрес из базы
(`sales/sender.py`), язык — язык диалога, которым написаны его письма
(`sales_threads.language`, `sales/mail.linked`); имя и адрес — набора этого языка, пустое
поле набора — общее (`Sender.in_language`), как в подписи самого письма. Адрес доноров сюда
не подставляется: у продаж свой отправитель, и чужой адрес выглядел бы заданным
(`sales/sender.py`). И слова — о человеке, а не о сайте: отписка с письма продаж закрывает
адрес того, кому оно ушло (решение владельца 12.10.2026, `letters/optout.py`), и «все адреса
сайта» страница не обещает.

**Поломка модуля продаж страницу не роняет.** Модуль спрашивается в своей точке
сохранения; упал — подпись без имени и адреса, по-английски, и строка журнала. Отписка —
требование закона о рассылках: кнопка обязана работать и тогда.

**Чужого письма метка не раскрывает.** Подпись не подделать, а на странице только сайт
(как было), имя и адрес отправителя — то, что стоит в самом письме; адреса получателя на
ней нет.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import assert_never

from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.unsubscribe import page
from backend.features.core.domain import Stage
from backend.features.letters.optout import label_letter
from backend.features.letters.settle import stage_of
from backend.features.letters.unsubscribe import Label
from backend.features.sales import mail, sender

logger = logging.getLogger(__name__)


async def voice_of(session: AsyncSession, label: Label) -> page.Voice:
    """Подпись и язык страницы для письма метки — тем же письмом и этапом, что у нажатия
    (`letters/optout.unsubscribe_label`). Письмо продаж ушло человеку: страница говорит о его
    адресе, а не о сайте (`page.Voice.person`)."""
    letter = await label_letter(session, label)
    if letter is None:
        return page.donors()
    stage = await stage_of(session, letter)
    match stage:
        case None | Stage.DONORS | Stage.ADVERTISERS:
            return page.donors()
        case Stage.SALES:
            return replace(await _sales(session, letter.thread_id), person=True)
        case _:
            assert_never(stage)


async def _sales(session: AsyncSession, thread_id: int | None) -> page.Voice:
    """«Отправитель» продаж и язык диалога — ответом модуля продаж в своей точке сохранения."""
    try:
        async with session.begin_nested():
            found = await sender.read(session)
            dialog = await mail.linked(session, thread_id)
    except Exception:
        logger.warning(
            "страница отписки: модуль продаж не ответил — подпись без имени и адреса",
            exc_info=True,
        )
        return page.Voice(None, None)
    language = dialog.link.language if dialog is not None else "en"
    signer = found.in_language(language)  # тот же набор, что подписал письмо
    return page.Voice(
        signer.values.get("sender_name"), signer.values.get("physical_address"), language
    )
