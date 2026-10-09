"""Файлы нашего ответа в базе: приложить к переписке, убрать, привязать к письму, прочитать.

Правила самого файла — `outgoing_files.py`; здесь — где он лежит и с каким письмом уходит.

**Файл живёт раньше письма.** Человек прикладывает его, пока пишет ответ, — строка
заводится с перепиской и без письма. Письмо ответа получает файлы, когда заводится
само (`answers.answer_reply`), и с этой минуты файл из него не убирается: письмо
ушло или уйдёт вместе с ним.

**Привязка — захватом, а не записью**, как перевод письма в «отправляется»
(`Sending._claim`): условный `UPDATE … WHERE message_id IS NULL`. Два ответа,
взявшие один файл в одну секунду, иначе оба увидели бы его свободным, и второй
молча увёл бы файл из первого письма.

**Повтор ответа файлы не теряет и не удваивает.** Почта не приняла ответ — письмо
ждёт в очереди с файлами, и повтор того же ответа берёт то же письмо
(`answers._materialize`) — вместе с ними: приложенные прежде остаются при нём,
даже если повтор их не назвал. Строки не копируются: файл один, письмо одно.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

from sqlalchemy import delete, false, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import ThreadModel
from backend.features.letters.outgoing_files import PENDING_DAYS, check, check_letter
from backend.features.letters.transport import OutgoingFile
from backend.features.outreach.repository import UnknownThreadError

#: Файлы письма в том виде, в каком их берёт транспорт.
type Files = tuple[OutgoingFile, ...]

_File = OutgoingAttachmentModel


class UnknownOutgoingFileError(LookupError):
    """Такого файла нет — в этой переписке или у этого письма."""


@dataclass(frozen=True, slots=True)
class ThreadFiles:
    """Файлы переписки сведениями: у писем — по номеру письма, и ждущие ответа."""

    letters: Mapping[int, Sequence[_File]]
    #: Приложены к переписке и ещё ни с одним письмом не ушли: экран показывает их
    #: у формы ответа и после перезагрузки страницы.
    pending: Sequence[_File]


#: Переписка без файлов.
NO_FILES = ThreadFiles(letters=MappingProxyType({}), pending=())


class OutgoingFileTakenError(RuntimeError):
    """Файл уже приложен к письму: убрать его или приложить к другому нельзя."""


class OutgoingFiles:
    """Файлы наших писем: запись, привязка к письму ответа и чтение."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def keep(
        self, thread_id: int, raw_name: str | None, data: bytes, *, by: int | None
    ) -> _File:
        """Приложить файл к переписке — без письма. Без фиксации: её делает вызывающий.

        Проверка — в пуле потоков: опись пакета Office и текст на десять
        мегабайт — не работа для цикла событий.
        """
        found = await self._session.scalar(
            select(ThreadModel.id).where(ThreadModel.id == thread_id)
        )
        if found is None:
            raise UnknownThreadError(f"Диалога №{thread_id} нет — прикладывать файл не к чему")
        checked = await asyncio.to_thread(check, raw_name, data)
        row = _File(
            thread_id=thread_id,
            name=checked.name,
            content_type=checked.content_type,
            size=checked.size,
            data=data,
            uploaded_by=by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def remove(self, thread_id: int, file_id: int) -> None:
        """Убрать файл, который ещё ни с одним письмом не ушёл. Без фиксации.

        Удаление — условное, как и привязка: файл, который секундой раньше взял
        ответ, не удаляется из-под письма.
        """
        gone = await self._session.scalar(
            delete(_File)
            .where(_File.id == file_id, _File.thread_id == thread_id, _File.message_id.is_(None))
            .returning(_File.id)
        )
        if gone is not None:
            return
        row = await self._in_thread(thread_id, file_id)
        raise OutgoingFileTakenError(
            f"Файл «{row.name}» уже приложен к письму №{row.message_id} — убрать его нельзя: "
            "письмо ушло или уйдёт вместе с ним"
        )

    async def drop_abandoned(self, *, now: datetime) -> list[tuple[int, int]]:
        """Убрать брошенные файлы: ни с одним письмом не ушли за `PENDING_DAYS`. Без фиксации.

        Условие — в самом удалении, как у `remove`: файл, который секундой раньше взял
        ответ, из-под письма не удаляется. Возвращает убранные (файл, переписка) — для журнала.
        """
        gone = await self._session.execute(
            delete(_File)
            .where(_File.message_id.is_(None))
            .where(_File.created_at < now - timedelta(days=PENDING_DAYS))
            .returning(_File.id, _File.thread_id)
        )
        return sorted((file_id, thread_id) for file_id, thread_id in gone.tuples())

    async def chosen(
        self, thread_id: int, file_ids: Sequence[int], *, letter_id: int | None
    ) -> list[_File]:
        """Файлы будущего ответа — до заведения письма, отказ словами.

        Названные человеком — из этой переписки и ни к какому письму не приложены;
        приложенные прежде к письму этого же ответа (`letter_id` — ответ, который
        почта не приняла и он ждёт в очереди) остаются при нём. Вместе — не больше
        пределов письма. Число названных сверяется до запроса: тысяча номеров
        в базу не уходит.
        """
        wanted = list(dict.fromkeys(file_ids))
        check_letter(len(wanted), 0)
        own = _File.message_id == letter_id if letter_id is not None else false()
        rows = await self._session.scalars(
            select(_File)
            .where(_File.thread_id == thread_id, or_(_File.id.in_(wanted), own))
            .order_by(_File.id)
        )
        found = list(rows.all())
        _all_named(found, wanted, thread_id)
        _free_for(found, letter_id)
        check_letter(len(found), sum(row.size for row in found))
        return found

    async def attach(self, files: Sequence[_File], message_id: int) -> None:
        """Приложить файлы к письму — захватом. Без фиксации.

        Файл, который между проверкой и привязкой взял другой ответ, не уводится:
        отказ, и письмо этого ответа не заводится — вызывающий не фиксирует.
        """
        wanted = [row.id for row in files]
        if not wanted:
            return
        taken = await self._session.scalars(
            update(_File)
            .where(_File.id.in_(wanted))
            .where(or_(_File.message_id.is_(None), _File.message_id == message_id))
            .values(message_id=message_id)
            .returning(_File.id)
            .execution_options(synchronize_session="fetch")
        )
        if len(taken.all()) != len(wanted):
            raise OutgoingFileTakenError(
                f"Файл ответа секундой раньше приложили к другому письму — письмо №{message_id} "
                "не ушло. Приложите файл заново и отправьте ответ ещё раз"
            )

    async def of_thread(self, thread_id: int) -> ThreadFiles:
        """Файлы переписки сведениями, без тел, одним запросом: приложенные к письмам —
        по номерам писем, ещё ни с чем не ушедшие — отдельно.

        Файл всегда лежит в переписке своего письма (`chosen`), поэтому выборка по
        переписке находит и те, и другие.
        """
        rows = await self._session.scalars(
            select(_File).where(_File.thread_id == thread_id).order_by(_File.id)
        )
        letters: dict[int, list[_File]] = {}
        pending: list[_File] = []
        for row in rows.all():
            if row.message_id is None:
                pending.append(row)
            else:
                letters.setdefault(row.message_id, []).append(row)
        return ThreadFiles(letters=letters, pending=pending)

    async def outgoing(self, message_id: int) -> Files:
        """Файлы письма для транспорта — с телами, по порядку приложения.

        Колонками, а не строками: строка, прочитанная раньше без тела (`deferred`),
        тело из этого запроса могла бы и не получить.
        """
        rows = await self._session.execute(
            select(_File.name, _File.content_type, _File.data)
            .where(_File.message_id == message_id)
            .order_by(_File.id)
        )
        return tuple(OutgoingFile(name, kind, data) for name, kind, data in rows.all())

    async def names(self, message_id: int) -> list[str]:
        """Имена файлов письма — для журнала."""
        rows = await self._session.scalars(
            select(_File.name).where(_File.message_id == message_id).order_by(_File.id)
        )
        return list(rows.all())

    async def file(self, message_id: int, file_id: int) -> tuple[str, bytes]:
        """Имя и тело файла письма. Номер письма сверяется: файл чужого письма
        по подобранному номеру — тоже «нет такого»."""
        found = (
            await self._session.execute(
                select(_File.name, _File.data).where(
                    _File.id == file_id, _File.message_id == message_id
                )
            )
        ).first()
        if found is None:
            raise UnknownOutgoingFileError(f"У письма №{message_id} нет вложения №{file_id}")
        name, data = found
        return name, data

    async def _in_thread(self, thread_id: int, file_id: int) -> _File:
        row = await self._session.scalar(
            select(_File).where(_File.id == file_id, _File.thread_id == thread_id)
        )
        if row is None:
            raise UnknownOutgoingFileError(f"В переписке №{thread_id} нет файла №{file_id}")
        return row


def _all_named(found: Sequence[_File], wanted: Sequence[int], thread_id: int) -> None:
    """Каждый названный файл нашёлся в этой переписке. Чужой переписки файл —
    тоже «нет такого»: номер подобран, а не взят с экрана этой переписки."""
    known = {row.id for row in found}
    missing = [file_id for file_id in wanted if file_id not in known]
    if missing:
        raise UnknownOutgoingFileError(
            f"В переписке №{thread_id} нет файла №{missing[0]} — приложить к ответу нечего"
        )


def _free_for(found: Sequence[_File], letter_id: int | None) -> None:
    """Ни один файл не приложен к другому письму: файл уходит с одним письмом."""
    taken = [row for row in found if row.message_id not in (None, letter_id)]
    if taken:
        raise OutgoingFileTakenError(
            f"Файл «{taken[0].name}» уже приложен к письму №{taken[0].message_id} — "
            "к этому ответу приложите его заново"
        )
