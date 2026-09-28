"""Вложения ответа: какие файлы хранить, какие нет, и почему.

Файл, присланный донором, — недоверенный (`docs/SECURITY.md`), но и
единственный: платформа приёма — единственный получатель письма, и не
сохранённый здесь прайс потерян навсегда. Отсюда порядок решений.

**Письмо принимается всегда.** Потолки ограничивают, что из файлов
хранится, а не что принимается: ответ донора с двадцать первым файлом —
всё ещё ответ донора, и отказ вебхуку превратился бы в повторы платформы
и потерю письма целиком.

**О каждом файле остаются сведения**, а у не сохранённого — причина
словами. «Прайс не присылали» и «прайс прислали, но мы его не взяли» —
разные ответы донора, и различать их человек должен по карточке.

**Опасные файлы не хранятся вовсе** — исполняемым файлам и скриптам
в прайсе делать нечего. Проверки антивирусом здесь нет, и это открытый
вопрос: отдаётся файл только на скачивание, никогда — на показ.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.replies.inbound import MAX_ATTACHMENTS, Attachment

logger = logging.getLogger(__name__)

_MB = 1024 * 1024

#: Файлов на ответ. Больше — не прайс, а выгрузка.
MAX_FILES = MAX_ATTACHMENTS

#: Один файл. Прайс и медиакит укладываются с запасом; больше — видео
#: или архив, и держать их в базе ради ответа на вопрос о цене незачем.
MAX_FILE_BYTES = 10 * _MB

#: Все файлы одного ответа вместе. Письмо у платформы приёма — до 30 МБ
#: вместе с текстом и кодированием; двадцать пять — почти всё, что в нём
#: может быть файлами.
MAX_REPLY_BYTES = 25 * _MB

#: Сколько вложений вообще записываем сведениями. Письмо на тысячи пустых
#: файлов — не ответ донора, а способ занять вебхук; остаток считается
#: в логе.
MAX_LISTED = 100


class UnknownAttachmentError(ValueError):
    """Такого вложения у этого ответа нет."""


class AttachmentNotKeptError(ValueError):
    """Вложение есть, но файла нет: его не сохранили. Сообщение говорит почему."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Что делать с одним вложением."""

    attachment: Attachment
    keep: bool
    reason: str | None = None


def megabytes(size: int) -> str:
    """Размер словами, как его назвали бы человеку: «10 МБ», «12,5 МБ»."""
    value = f"{size / _MB:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{value} МБ"


def _refusal(attachment: Attachment, kept: int, kept_bytes: int) -> str | None:
    """Почему файл не хранить, словами. `None` — хранить.

    Текст идёт человеку как есть — после «не сохранён», на карточке и
    в отказе на скачивание, — поэтому он называет причину, а не код.
    """
    if attachment.dangerous:
        return f"«{attachment.extension}» — исполняемый файл или скрипт, такие не принимаются"
    if attachment.data is None:
        return "платформа назвала файл, но самого файла в письме не было"
    size = len(attachment.data)
    if kept >= MAX_FILES:
        return f"в ответе больше {MAX_FILES} файлов — хранятся первые {MAX_FILES}"
    if size > MAX_FILE_BYTES:
        return f"{megabytes(size)} — больше предела {megabytes(MAX_FILE_BYTES)} на файл"
    if kept_bytes + size > MAX_REPLY_BYTES:
        return f"вместе с предыдущими файлами больше {megabytes(MAX_REPLY_BYTES)} на ответ"
    return None


def sort_out(attachments: Sequence[Attachment]) -> list[Decision]:
    """Решить по каждому вложению, по порядку их в письме.

    Первые — важнее: прайс прикладывают первым, а подписи с логотипами
    и картинки из цитаты идут следом.
    """
    decisions: list[Decision] = []
    kept = kept_bytes = 0
    for attachment in attachments[:MAX_LISTED]:
        reason = _refusal(attachment, kept, kept_bytes)
        if reason is None and attachment.data is not None:
            kept += 1
            kept_bytes += len(attachment.data)
        decisions.append(Decision(attachment=attachment, keep=reason is None, reason=reason))
    return decisions


class ReplyFiles:
    """Запись и чтение вложений ответа."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def keep(self, reply_id: int, attachments: Sequence[Attachment]) -> list[ReplyAttachmentModel]:
        """Записать вложения ответа: файлы — хранимые, сведения — все."""
        decisions = sort_out(attachments)
        rows = [
            ReplyAttachmentModel(
                reply_id=reply_id,
                name=decision.attachment.name,
                content_type=decision.attachment.content_type,
                size=decision.attachment.size,
                data=decision.attachment.data if decision.keep else None,
                accepted=decision.keep,
                reason=decision.reason,
            )
            for decision in decisions
        ]
        self._session.add_all(rows)
        refused = [d for d in decisions if not d.keep]
        unlisted = len(attachments) - len(decisions)
        if refused or unlisted:
            logger.warning(
                "приём: ответ №%s — вложений %s, сохранено %s, не сохранено %s%s",
                reply_id,
                len(attachments),
                len(decisions) - len(refused),
                len(refused),
                f", не записано даже сведениями {unlisted}" if unlisted else "",
            )
        return rows

    async def listed(self, reply_ids: Iterable[int]) -> dict[int, list[ReplyAttachmentModel]]:
        """Сведения о вложениях по ответам — без самих файлов."""
        wanted = list(reply_ids)
        if not wanted:
            return {}
        rows = await self._session.execute(
            select(ReplyAttachmentModel)
            .where(ReplyAttachmentModel.reply_id.in_(wanted))
            .order_by(ReplyAttachmentModel.id)
        )
        found: dict[int, list[ReplyAttachmentModel]] = {}
        for row in rows.scalars().all():
            found.setdefault(row.reply_id, []).append(row)
        return found

    async def file(self, reply_id: int, attachment_id: int) -> tuple[ReplyAttachmentModel, bytes]:
        """Вложение вместе с файлом.

        Номер ответа сверяется: вложение чужого ответа по подобранному
        номеру — тоже «нет такого».
        """
        row = await self._session.scalar(
            select(ReplyAttachmentModel)
            .options(undefer(ReplyAttachmentModel.data))
            .where(ReplyAttachmentModel.id == attachment_id)
            .where(ReplyAttachmentModel.reply_id == reply_id)
        )
        if row is None:
            raise UnknownAttachmentError(f"У ответа №{reply_id} нет вложения №{attachment_id}")
        if row.data is None:
            raise AttachmentNotKeptError(
                f"Файл «{row.name}» не сохранён: {row.reason or 'причина не записана'}"
            )
        return row, row.data
