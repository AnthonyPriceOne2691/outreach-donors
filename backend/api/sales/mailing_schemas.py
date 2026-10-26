"""Вкладка «Рассылка» продаж: адреса частями — что уходит на экран (`GET /sales/mailing`).

Числа считает `features/sales/mailing.py`, экран своего счёта не ведёт: итог — сумма частей, а
сумма причин — своя часть. Строки автоотправки и автосборки — той же формой и тем же правилом, что
у «Очереди писем» (`AutoLineView`, `sales/auto_lines.py`). Прогноз отправки по дням — у соседней
ручки (`GET /api/letters/forecast?stage=sales`); «в очереди» здесь — то же число, что её
`queued_first`, а «сборка напишет» — то же, что её `supply_extra`.
"""

from __future__ import annotations

from pydantic import BaseModel

from backend.api.sales.queue import AutoLineView
from backend.features.sales import mailing


class MailingWhy(BaseModel):
    """Причина словами и сколько лидов по ней."""

    words: str
    count: int

    @classmethod
    def of(cls, found: mailing.Why) -> MailingWhy:
        return cls(words=found.words, count=found.count)


class MailingParts(BaseModel):
    """Адреса частями: каждый лид — ровно в одной части, итог — их сумма."""

    #: Всего лидов: ушло + в очереди + сборка напишет + не напишет + не готовы + сняты.
    total: int
    #: Первое письмо ушло (ушло, дошло или вернулось — шаг «отправлено» воронки без периода) или
    #: письмо в полёте: его уже взяла отправка; сколько таких — `in_flight`.
    sent: int
    #: Сколько из «ушло» ушло пока только письмом в полёте: письмо цепочки «отправляется», его исход
    #: ещё неизвестен, а ушедшего, дошедшего или вернувшегося письма у лида нет. Воронка таких ещё
    #: не считает: «ушло» без них — её шаг «отправлено» без периода. Не своя часть — внутри «ушло».
    in_flight: int
    #: Первое письмо ждёт в очереди — тем же счётом, что пачка и `queued_first` прогноза; письма,
    #: которые не уйдут, пока их не поправят, — тоже здесь.
    queued: int
    #: Письма нет (или оно снято до отправки и пишется заново), и сборка его напишет — её решением;
    #: по всем гипотезам это `supply_extra` прогноза.
    will_write: int
    #: Письма нет, а сборка сейчас его не напишет — почему, в `wont_write_why`.
    wont_write: int
    #: Лид не готов: на очистке.
    not_ready: int
    #: Снят: лид отклонён или его первое письмо снято и заново не пишется — почему, в `removed_why`.
    removed: int
    #: Почему сборка не напишет — словами её отчёта, старшие по числу первыми; сумма — `wont_write`.
    wont_write_why: list[MailingWhy]
    #: Почему сняты — словами экрана лидов и снятия письма, старшие первыми; сумма — `removed`.
    removed_why: list[MailingWhy]

    @classmethod
    def of(cls, found: mailing.Parts) -> MailingParts:
        return cls(
            total=found.total,
            sent=found.sent,
            in_flight=found.in_flight,
            queued=found.queued,
            will_write=found.will_write,
            wont_write=found.wont_write,
            not_ready=found.not_ready,
            removed=found.removed,
            wont_write_why=[MailingWhy.of(why) for why in found.wont_write_why],
            removed_why=[MailingWhy.of(why) for why in found.removed_why],
        )


class MailingRow(BaseModel):
    """Адреса одной гипотезы."""

    hypothesis_id: int
    name: str
    parts: MailingParts

    @classmethod
    def of(cls, found: mailing.Row) -> MailingRow:
        return cls(
            hypothesis_id=found.hypothesis_id, name=found.name, parts=MailingParts.of(found.parts)
        )


class SalesMailingView(BaseModel):
    """Вкладка «Рассылка»: режим строками, адреса по гипотезам (и без лидов — с нулями) и итог."""

    #: Автоотправка: включена ли, как часто, сколько в окне получателя, почему стоит.
    autosend: AutoLineView
    #: Автосборка: хватает ли очереди на ближайший день отправки, кого соберёт или почему нет.
    autobuild: AutoLineView
    #: Строка на гипотезу — по номеру, как у воронки.
    rows: list[MailingRow]
    #: Итог — сумма строк: лид принадлежит одной гипотезе.
    total: MailingParts
