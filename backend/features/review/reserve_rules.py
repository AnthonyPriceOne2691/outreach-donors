"""Правило резерва: что в нём, какой группы причина, где страна прогона и можно ли взять.

Отдельно от выборки (`reserve.py`) с 20.10.2026: модуль резерва упёрся в потолок длины, а
правило «можно ли взять» и слова строки — одно на экран и на «Взять на рассмотрение».
Здесь нет запросов к базе: только условия для выборки и разбор её строки.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import ColumnElement, and_, case, or_

from backend.features.core.domain import DonorStatus
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunVerdictModel
from backend.features.donors.geo import stored_shares
from backend.features.donors.wording import reject_reason_text
from backend.features.review.queue import Decision
from backend.features.runs.exclusions import NOT_WRITING_WORDS, ExclusionReason
from backend.features.runs.refusals import BIG_KEY_PREFIX, Threshold
from backend.features.runs.verdicts import EXCLUDED, GAP

#: «Кому не пишем» — показан, но не берётся (ТЗ, решение владельца 11.10.2026).
NOT_TAKEN = "Не берём: кому не пишем"


class Group(StrEnum):
    """Почему не прошёл — группами фильтра: так оператор и спрашивает."""

    #: DR ниже порога, мало трафика, ключей или реф. доменов.
    WEAK = "weak"
    #: Страна прогона не в топ-N и даёт меньше доли.
    REGION = "region"
    #: «Не проверен»: замеру не хватило данных — куплен только DR, страны не спрашивали или
    #: не получены, в разбивке одна верхняя страна (ревью 20.10.2026). Свежий, он 90 дней
    #: не перемеряется, и без группы его не было видно по имени нигде.
    GAP = "gap"
    #: «Кому не пишем»: крупный сайт, платформа, соцсеть, СМИ, справочник, гос. зона.
    NOT_WRITING = "not_writing"


#: Причины гейта, которые резерв показывает: «кому не пишем» по имени домена.
_NAMED: tuple[str, ...] = tuple(reason.value for reason in NOT_WRITING_WORDS)

#: Отношения, появившиеся после прогона, — «взять» их перепроверяет (`Exclusions`, ревью
#: 20.10.2026): стоп-лист, поставщик, «писали, не ответил», «не продаёт». «Кому не пишем» по
#: имени и «отклонён человеком» сказаны своими словами раньше (`refusal`).
RELATIONS: frozenset[ExclusionReason] = frozenset(
    {
        ExclusionReason.STOPLIST,
        ExclusionReason.SUPPLIER,
        ExclusionReason.SILENT,
        ExclusionReason.DECLINES,
    }
)


def in_reserve() -> ColumnElement[bool]:
    """Строка вердикта — в резерве: отказ порогами, «не проверен» из-за нехватки данных или
    «кому не пишем» гейта по имени."""
    return or_(
        RunVerdictModel.status == DonorStatus.UNSUITABLE.value,
        and_(RunVerdictModel.status == DonorStatus.UNCHECKED.value, RunVerdictModel.cause == GAP),
        and_(RunVerdictModel.status == EXCLUDED, RunVerdictModel.cause.in_(_NAMED)),
    )


def group_of() -> ColumnElement[str]:
    """Группа причины строкой — одним правилом у фильтра и у его счётчиков."""
    return case(
        (
            or_(
                RunVerdictModel.status == EXCLUDED,
                RunVerdictModel.cause == Threshold.BIG.value,
            ),
            Group.NOT_WRITING.value,
        ),
        (RunVerdictModel.cause == Threshold.GEO.value, Group.REGION.value),
        (RunVerdictModel.cause == GAP, Group.GAP.value),
        else_=Group.WEAK.value,
    )


def group_named(status: str, cause: str | None) -> Group:
    """Группа строки — тем же правилом, что `group_of`, для строки уже прочитанной."""
    if status == EXCLUDED or cause == Threshold.BIG.value:
        return Group.NOT_WRITING
    if cause == Threshold.GEO.value:
        return Group.REGION
    return Group.GAP if cause == GAP else Group.WEAK


@dataclass(frozen=True, slots=True)
class CountryFit:
    """Страна прогона в топ-5 домена: место и доля, верхняя страна. Места нет — не в топе
    или страны не спрашивали (`asked`)."""

    place: int | None
    share: float | None
    top: str | None
    top_share: float | None
    asked: bool
    partial: bool


def fit_of(donor: DonorModel | None, country: str) -> CountryFit:
    """Где страна прогона у домена — по записанной разбивке (`donors.geo_breakdown`)."""
    if donor is None:
        return CountryFit(None, None, None, None, asked=False, partial=False)
    shares = stored_shares(donor.geo_breakdown)
    wanted = country.lower()
    place = next((at for at, (code, _) in enumerate(shares, start=1) if code == wanted), None)
    return CountryFit(
        place=place,
        share=None if place is None else shares[place - 1][1],
        top=shares[0][0] if shares else None,
        top_share=shares[0][1] if shares else None,
        asked=donor.geo_refreshed_at is not None,
        partial=bool(donor.geo_partial),
    )


def said_of(cause: str | None, reason: str | None) -> str | None:
    """Причина строки словами экрана: страна — названием (`wording.reject_reason_text`),
    крупный сайт — как в отчёте прогона: «Кому не пишем: слишком крупный — DR выше 80»,
    нехватка данных — «Не проверен: …», как в отчёте."""
    if cause == Threshold.BIG.value and reason:
        bound = reason.split(" — ", maxsplit=1)[0].removeprefix(BIG_KEY_PREFIX).strip()
        return f"Кому не пишем: слишком крупный — DR выше {bound}"
    if cause == GAP and reason:
        return f"Не проверен: {reason}"
    return reject_reason_text(reason)


@dataclass(frozen=True, slots=True)
class Standing:
    """Что о домене известно сейчас, кроме его строки резерва: строка в очереди этого
    прогона, где ещё ждёт решения, «кому не пишем» по действующей версии и отношения."""

    queued: str | None = None
    pending_in: int | None = None
    #: «Кому не пишем» по действующей версии — словами; домен перемерен после прогона.
    off: str | None = None
    #: Отношение, появившееся после прогона (`RELATIONS`), — подписью гейта.
    relation: str | None = None


def refusal(verdict: RunVerdictModel, donor: DonorModel | None, now: Standing) -> str | None:
    """Почему этот домен из резерва не взять на рассмотрение — словами; `None` — можно.

    «Кому не пишем» строки — коротко: подробно его называет причина рядом (`said_of`), и
    дважды в строке одно и то же не пишется. По действующей версии (`off`) — подробно:
    причина строки о нём не говорит — домен перемерен после прогона. Стоп-лист, поставщик,
    молчание и «не продаёт» — тоже: прогон их не застал."""
    if verdict.status == EXCLUDED or verdict.cause == Threshold.BIG.value:
        return NOT_TAKEN
    if now.off is not None:
        return f"Не берём: {now.off}"
    if now.relation is not None:
        return f"Не берём: {now.relation}"
    if now.queued is not None:
        return _QUEUED.get(now.queued, "уже в очереди этого прогона")
    return _decided(donor, now.pending_in)


_QUEUED = {
    Decision.PENDING.value: "уже на рассмотрении в этом прогоне",
    Decision.ACCEPTED.value: "уже донор — принят в этом прогоне",
    Decision.REJECTED.value: "отклонён в этом прогоне — решение снимают в его очереди",
}


def _decided(donor: DonorModel | None, pending_in: int | None) -> str | None:
    """Решение по домену одно: принятый, отклонённый и ждущий в другой очереди не берутся."""
    if donor is None:
        return "данных замера нет — брать нечего"
    if donor.review == Decision.ACCEPTED.value:
        return "уже донор — принят человеком"
    if donor.review == Decision.REJECTED.value:
        return "отклонён человеком — решение снимают в очереди прогона, где решали"
    if pending_in is not None:
        return f"ждёт решения в очереди прогона №{pending_in}"
    return None
