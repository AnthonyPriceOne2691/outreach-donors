"""Кого не берём в отбор вовсе — и чем это отличается от свежести.

Свежесть отвечает на вопрос «за этот домен уже заплачено», исключение —
на вопрос «этот домен нам не нужен». Числа складывать нельзя, и в отчёте
они стоят разными строками: первое радует, второе прячет домен от
оператора, и он обязан знать, почему тот пропал.

**Гейт стоит до сметы.** До этого среза стоп-лист проверялся только при
сборке очереди писем: домен, который отписался, пожаловался или лежит
в поставщиках, проходил весь платный путь — просев, метрики, страны —
и отсеивался в самом конце. На базе 22.09.2026 таких 47%: список
маленький только потому, что боевой рассылки ещё не было.

**Три причины, два источника.** Стоп-лист и поставщики лежат в разных
таблицах — первая про адресатов, вторая про доноров, — и сводить их
в одну здесь не время. Молчание своей записи не имеет вовсе: оно
считается по письмам, и теневая строка на каждого промолчавшего
завела бы второй источник правды там, где хватает первого.

**«Кому не пишем» — на каждом пути, где пишем** (ТЗ: список Этапа 2
распространяется на Этап 1; решение владельца 11.10.2026). По имени домена —
здесь же, до Ahrefs (`by_name`); DR > 80 — на первой ступени замера
(`verdict.check_dr`). А на выходах — одно условие `not_writing()`: очередь
рассмотрения, сборка писем, очередь «Форм», платный поиск адреса и проверка
при отправке письма и добивки не берут крупный сайт и домен из списков,
даже принятые до правила. Почему — словами (`not_writing_reason`). Бизнесу ниши
из выдачи — то же правило на самом сайте, без строки донора (`site_not_writing`,
сверка ТЗ 12.10.2026): до неё его сверяли только с примером площадки в письме.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, assert_never

from sqlalchemy import ColumnElement, Select, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.config import judge as judge_cfg
from backend.config import outreach as cfg
from backend.features.core.domain import (
    MessageStatus,
    ReplyKind,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.core.models.run import RunSettingsModel
from backend.features.crawl.denylist import MAJOR_MEDIA, REFERENCE_DOMAINS, SOCIAL_DOMAINS
from backend.features.donors.publisher_judge import (
    PUBLIC_ZONE_LABELS,
    PUBLIC_ZONE_TLDS,
    is_platform,
    is_public_zone,
)
from backend.features.donors.verdict import BIG_SITE_DR, big_site_reason

#: Письмо ушло от нас и не было отвергнуто. Отказ доставки сюда
#: не входит: до адресата мы не добрались, и считать это «мы ему уже
#: писали» значило бы похоронить донора из-за мёртвого ящика —
#: письмо уйдёт на следующий его адрес (`letters/attempts.py`).
LEFT_OUR_HANDS = (MessageStatus.SENT, MessageStatus.DELIVERED)


class ExclusionReason(StrEnum):
    """Почему домен не пошёл в прогон. Оператор читает именно это."""

    STOPLIST = "stoplist"
    SUPPLIER = "supplier"
    SILENT = "silent"
    DECLINES = "declines"
    #: Человек отклонил в прошлом прогоне. Смотреть второй раз незачем,
    #: платить за метрики — тем более.
    REJECTED = "rejected"
    #: Гос., учебная или военная зона: размещений не продаёт по уставу.
    PUBLIC_ZONE = "public_zone"
    #: Соцсеть, UGC-площадка, маркетплейс: разместиться там нельзя.
    PLATFORM = "platform"
    #: Крупное СМИ: «кому не пишем» ТЗ (11.10.2026 — и в Этапе 1).
    MAJOR_MEDIA = "major_media"
    #: Справочник, энциклопедия, наука: «кому не пишем» ТЗ — «исследования».
    REFERENCE = "reference"

    @property
    def caption(self) -> str:
        """Как причина называется в отчёте. Не `title`: у `str` он свой."""
        return _CAPTIONS[self]


_CAPTIONS = {
    ExclusionReason.STOPLIST: "в стоп-листе",
    ExclusionReason.SUPPLIER: "поставщик агентства",
    ExclusionReason.SILENT: "писали, не ответил",
    ExclusionReason.DECLINES: "ответил: размещений не продаёт",
    ExclusionReason.REJECTED: "отклонён человеком",
    ExclusionReason.PUBLIC_ZONE: "гос. или учебная зона",
    ExclusionReason.PLATFORM: "платформа или соцсеть",
    ExclusionReason.MAJOR_MEDIA: "крупное СМИ",
    ExclusionReason.REFERENCE: "справочник или исследования",
}


def counts(excluded: dict[str, ExclusionReason]) -> dict[str, int]:
    """Сколько по каждой причине — в том виде, в каком это читают."""
    tally: dict[str, int] = {}
    for reason in excluded.values():
        tally[reason.caption] = tally.get(reason.caption, 0) + 1
    return tally


def by_name(hosts: Sequence[str]) -> dict[str, ExclusionReason]:
    """Бесспорное по одному имени домена — без базы и без модели.

    Строка «кому не пишем» требований (wikipedia / gov / edu / крупные СМИ /
    соцсети / исследования) стояла только в Этапе 2, и прогон 23.09.2026
    заплатил метриками за reddit.com, linkedin.com, youtube.com, nih.gov
    и cornell.edu — все признаны «годными». Здесь только классы, где ошибиться
    нельзя: зона домена, платформа и списки обхода (`crawl/denylist.py`) —
    соцсети, крупные СМИ, справочники. С 11.10.2026 список Этапа 2 действует
    на Этап 1 целиком (решение владельца: по ТЗ), и крупное СМИ отсекается
    до Ahrefs, а не уходит в конец очереди. «Похоже на бренд» сюда не идёт —
    это мнение судьи, и оно ошибается (cloudways.com со страницей для
    авторов правило судьи отрезало). DR > 80 — по замеру (`verdict.check_dr`).
    """
    found: dict[str, ExclusionReason] = {}
    for host in hosts:
        if is_public_zone(host):
            found[host] = ExclusionReason.PUBLIC_ZONE
        elif is_platform(host) or host in SOCIAL_DOMAINS:
            found[host] = ExclusionReason.PLATFORM
        elif host in MAJOR_MEDIA:
            found[host] = ExclusionReason.MAJOR_MEDIA
        elif host in REFERENCE_DOMAINS:
            found[host] = ExclusionReason.REFERENCE
    return found


#: Кому не пишем по имени корневого домена — списками обхода.
NAMED_HOSTS: frozenset[str] = MAJOR_MEDIA | SOCIAL_DOMAINS | REFERENCE_DOMAINS

#: Почему не пишем — словами для человека, по классу домена (`by_name`).
NOT_WRITING_WORDS: dict[ExclusionReason, str] = {
    ExclusionReason.PUBLIC_ZONE: "гос. и учебным сайтам не пишем",
    ExclusionReason.PLATFORM: "платформам и соцсетям не пишем",
    ExclusionReason.MAJOR_MEDIA: "крупным СМИ не пишем",
    ExclusionReason.REFERENCE: "справочникам и исследованиям не пишем",
}


def exclusion_words(reason: ExclusionReason) -> str:
    """Причина гейта словами отчёта и резерва: «кому не пишем» — так и сказано."""
    if reason in NOT_WRITING_WORDS:
        return f"Кому не пишем: {reason.caption}"
    return reason.caption[:1].upper() + reason.caption[1:]


def _either(labels: Iterable[str]) -> str:
    return "|".join(sorted(re.escape(label) for label in labels))


#: `by_name` на языке базы — одно правило с прогоном, сверяет их тест на одних доменах.
#: Зона (`is_public_zone`): последняя метка — gov / mil / edu, или три метки и больше,
#: и перед двухбуквенной страной — метка гос. или учебной зоны.
_ZONE_TLD = rf"\.({_either(PUBLIC_ZONE_TLDS)})$"
_ZONE_LABEL = rf"[^.]\.({_either(PUBLIC_ZONE_LABELS)})\.[^.]{{2}}$"
#: Платформа (`is_platform`): любая метка домена — из `PLATFORM_LABELS`.
_PLATFORM = rf"(^|\.)({_either(judge_cfg.PLATFORM_LABELS)})(\.|$)"


def _by_name_sql(host: Any) -> ColumnElement[bool]:
    low = func.lower(host)
    return or_(
        low.in_(sorted(NAMED_HOSTS)),
        low.op("~")(_ZONE_TLD),
        low.op("~")(_ZONE_LABEL),
        low.op("~")(_PLATFORM),
    )


def max_dr_in_force() -> ColumnElement[int]:
    """Порог крупного сайта действующей версии настроек — подзапросом: выходы берут то, что
    действует сейчас, не читая версию каждый сам. Версий ещё нет — умолчание конфига."""
    version = aliased(RunSettingsModel)
    latest = select(version.max_dr).order_by(version.version.desc()).limit(1).scalar_subquery()
    return func.coalesce(latest, BIG_SITE_DR)


def big_site() -> ColumnElement[bool]:
    """DR выше порога действующей версии по последнему замеру. DR не мерили (донор заведён
    руками) — не крупный: неизвестное не повод молча вычеркнуть, а `NULL` вывернул бы
    условие наизнанку."""
    return func.coalesce(DonorModel.dr, 0) > max_dr_in_force()


def named() -> ColumnElement[bool]:
    """Домен донора — из «кому не пишем» по имени: зона, платформа, соцсеть, СМИ, справочник.

    Условие на строке донора: подзапрос по своему псевдониму доменов не цепляется
    за внешний запрос, и условие годно и там, где доменов в запросе нет («Формы»).
    """
    domain = aliased(DomainModel)
    return DonorModel.domain_id.in_(select(domain.id).where(_by_name_sql(domain.host)))


def not_writing() -> ColumnElement[bool]:
    """Донор в «кому не пишем» ТЗ — одно условие на все выходы, где пишем."""
    return or_(big_site(), named())


def writable[Query: Select[Any]](statement: Query) -> Query:
    """Запрос без доноров из «кому не пишем» — даже принятых до правила 11.10.2026: ступень
    «вне «кому не пишем»» воронки сборки и её выборка (`letters/recipients.py`)."""
    return statement.where(~not_writing())


def domain_not_writing(domain_id: Any) -> ColumnElement[bool]:
    """Донор этого домена — в «кому не пишем»: для запросов без строки донора (письма)."""
    return select(DonorModel.id).where(DonorModel.domain_id == domain_id, not_writing()).exists()


def stoplisted(domain_id: Any, stage: Stage) -> ColumnElement[bool]:
    """Домен держит запись стоп-листа для этапа `stage` сейчас — правилом проверки перед
    отправкой (`SuppressionModel.holds`). Для выходов, где адреса ещё нет, — очередь «Форм»
    (`contacts/forms.in_queue`, 28.10.2026). Только запись на домен: запись на адрес держит
    адрес, а не сайт (`Exclusions._stoplisted`)."""
    held = SuppressionModel.holds(stage, datetime.now(UTC))
    return select(SuppressionModel.id).where(SuppressionModel.domain_id == domain_id, held).exists()


async def not_writing_of(session: AsyncSession, domain_ids: Sequence[int]) -> dict[str, str]:
    """Домены из этих, которым не пишем: хост → почему, словами."""
    if not domain_ids:
        return {}
    rows = await session.execute(
        select(DomainModel.host, big_site(), max_dr_in_force())
        .join(DonorModel, DonorModel.domain_id == DomainModel.id)
        .where(DomainModel.id.in_(list(domain_ids)), not_writing())
    )
    found: dict[str, str] = {}
    for host, big, max_dr in rows.tuples():
        reason = by_name([host]).get(host)
        if big or reason is None:
            found[host] = big_site_reason(int(max_dr))
        else:
            found[host] = NOT_WRITING_WORDS[reason]
    return found


async def not_writing_reason(session: AsyncSession, domain_id: int) -> str | None:
    """Почему донору этого домена не пишем — словами; `None` — пишем."""
    return next(iter((await not_writing_of(session, [domain_id])).values()), None)


def site_not_writing(domain_id: Any, host: Any) -> ColumnElement[bool]:
    """Сайт — в «кому не пишем», есть у него строка донора или нет: по имени — сам хост,
    по DR — замер его донора, если прогон его мерил. Для адресата не из доноров — бизнеса
    ниши из выдачи (`letters/niche_recipients.py`): судья мог отрезать его до Ahrefs,
    и строки донора тогда нет. Правило то же, что у доноров, — без своей копии."""
    return or_(_by_name_sql(host), domain_not_writing(domain_id))


def domain_site_not_writing(domain_id: Any) -> ColumnElement[bool]:
    """Сайт этого домена — в «кому не пишем» (`site_not_writing`), для запросов без строки
    домена: число очереди «Форм» рекламодателей считается без `domains`
    (`contacts/forms.advertiser_in_queue`, 28.10.2026). Хост — через свой псевдоним доменов:
    подзапрос не цепляется за внешний запрос и годен и там, где домены в нём есть (`named`)."""
    domain = aliased(DomainModel)
    off = site_not_writing(domain.id, domain.host)
    return select(domain.id).where(domain.id == domain_id, off).exists()


async def site_not_writing_reason(session: AsyncSession, domain_id: int) -> str | None:
    """Почему этому сайту не пишем — словами, как `site_not_writing`; `None` — пишем."""
    off = await not_writing_reason(session, domain_id)
    if off is not None:
        return off
    host = await session.scalar(select(DomainModel.host).where(DomainModel.id == domain_id))
    reason = None if host is None else by_name([host.lower()]).get(host.lower())
    return None if reason is None else NOT_WRITING_WORDS[reason]


class NotWritingAcceptError(ValueError):
    """Принять нельзя: домены — в «кому не пишем». Текст называет их и почему."""

    def __init__(self, refused: dict[str, str]) -> None:
        listed = "; ".join(f"{host} — {why}" for host, why in sorted(refused.items()))
        super().__init__(f"Принять нельзя: {listed}. Снимите их с выбора — остальных можно принять")


class Exclusions:
    """Три запроса к базе, ни одного к провайдеру.

    Поэтому гейт и стоит первым: он ничего не стоит, а отсекает то,
    за что иначе платят полный путь.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        silence_days: int = cfg.SILENCE_DAYS,
        decline_days: int = cfg.DECLINE_DAYS,
    ) -> None:
        self._session = session
        self._silence_days = silence_days
        self._decline_days = decline_days

    async def excluded_hosts(
        self,
        hosts: Sequence[str],
        *,
        stage: Stage = Stage.DONORS,
        now: datetime | None = None,
    ) -> dict[str, ExclusionReason]:
        """Домены, которые в прогон не идут, и почему.

        Причина у домена одна, хотя совпасть могут все. Порядок
        от слабой к сильной: молчание перебивается ответом «не продаём»,
        ответ — поставщиком, поставщик — стоп-листом. Иначе донор, который отписался
        и молчит, объяснялся бы оператору молчанием.
        """
        if not hosts:
            return {}

        moment = now or datetime.now(UTC)
        found: dict[str, ExclusionReason] = by_name(hosts)
        for host in await self._silent(hosts, stage, moment):
            found[host] = ExclusionReason.SILENT
        match stage:
            case Stage.DONORS:
                found.update(await self._donor_answers(hosts, moment))
            case Stage.ADVERTISERS | Stage.SALES:
                pass
            case _:
                assert_never(stage)
        for host in await self._suppliers(hosts, moment):
            found[host] = ExclusionReason.SUPPLIER
        found.update(await self._stoplisted(hosts, stage, moment))
        return found

    async def _donor_answers(
        self, hosts: Sequence[str], moment: datetime
    ) -> dict[str, ExclusionReason]:
        """Только донорам: «не продаём размещения» — ответ про донорство,
        «отклонён» — решение по донору. Рекламодателем или лидом продаж тот же
        сайт быть может — им письмо о другом. Отклонение перебивает ответ."""
        found = dict.fromkeys(await self._declined(hosts, moment), ExclusionReason.DECLINES)
        found.update(dict.fromkeys(await self._rejected(hosts), ExclusionReason.REJECTED))
        return found

    async def _rejected(self, hosts: Sequence[str]) -> list[str]:
        """Отклонены человеком — последним решением по домену."""
        rows = await self._session.execute(
            select(DomainModel.host)
            .join(DonorModel, DonorModel.domain_id == DomainModel.id)
            .where(DomainModel.host.in_(hosts))
            .where(DonorModel.review == "rejected")
        )
        return [host for (host,) in rows.all()]

    async def _declined(self, hosts: Sequence[str], moment: datetime) -> list[str]:
        """Сами ответили «не продаём размещения» — и не так давно."""
        border = moment - timedelta(days=self._decline_days)
        rows = await self._session.execute(
            select(DomainModel.host)
            .where(DomainModel.host.in_(hosts))
            .where(DomainModel.seller_answer == "declines")
            .where(DomainModel.seller_answer_at > border)
        )
        return [host for (host,) in rows.all()]

    async def _stoplisted(
        self, hosts: Sequence[str], stage: Stage, moment: datetime
    ) -> dict[str, ExclusionReason]:
        """Стоп-лист — только записи по домену.

        Запись на один адрес домена не закрывает: у сайта их несколько,
        и отказ секретаря не означает отказа редакции. При сборке письма
        такая запись всё равно сработает — там адресат уже известен.
        """
        rows = await self._session.execute(
            select(DomainModel.host, SuppressionModel.reason)
            .join(SuppressionModel, SuppressionModel.domain_id == DomainModel.id)
            .where(DomainModel.host.in_(hosts))
            .where(or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == stage))
            .where(SuppressionModel.in_force(moment))
        )
        return {
            host: (
                ExclusionReason.SUPPLIER
                if reason is SuppressionReason.SUPPLIER
                else ExclusionReason.STOPLIST
            )
            for host, reason in rows.all()
        }

    async def _suppliers(self, hosts: Sequence[str], moment: datetime) -> set[str]:
        rows = await self._session.execute(
            select(SupplierDonorModel.host)
            .where(SupplierDonorModel.host.in_(hosts))
            .where(SupplierDonorModel.in_force(moment))
        )
        return set(rows.scalars().all())

    async def _silent(self, hosts: Sequence[str], stage: Stage, moment: datetime) -> set[str]:
        """Домены, которым письмо уходило, а ответа не было.

        Ответ смотрится по диалогу, а не по письму: отвечают с другого
        адреса чаще, чем кажется. Ответивший из отбора не выпадает —
        его держат сроки годности, и по ним же требование велит
        перезапросить цену, когда ей исполнится 150 дней.

        Считается по этапу, а не по кампании — тем же правилом, что
        и «кому мы ещё не писали» при сборке очереди. Этапы спрашивают
        разное: у донора цену, у рекламодателя размещение, — и молчание
        на первый вопрос не значит молчания на второй.

        Отдельной проверки «цепочка кончилась» здесь нет намеренно.
        Цепочка укладывается в две недели, а срок тишины — год: домен,
        которому пишут прямо сейчас, попадает под то же условие и точно
        так же не должен уходить в новый прогон.
        """
        border = moment - timedelta(days=self._silence_days)
        # Ответ — и статус диалога, и сам ответ человека на нём. Статус
        # ставит приём (`replies.repository.mark_replied`), но до 28.09.2026
        # его не ставил никто, и одного статуса мало: диалог, отвеченный
        # до этого, остался бы «молчащим» на год.
        human_reply = (
            select(ReplyModel.id)
            .where(ReplyModel.thread_id == ThreadModel.id)
            .where(ReplyModel.kind == ReplyKind.HUMAN)
            .exists()
        )
        replied = (
            select(ThreadModel.domain_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(or_(ThreadModel.status == ThreadStatus.REPLIED, human_reply))
            .where(CampaignModel.stage == stage)
        )
        rows = await self._session.execute(
            select(distinct(DomainModel.host))
            .join(MessageModel, MessageModel.domain_id == DomainModel.id)
            .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
            .where(DomainModel.host.in_(hosts))
            .where(CampaignModel.stage == stage)
            .where(MessageModel.status.in_(LEFT_OUR_HANDS))
            .where(MessageModel.sent_at.is_not(None))
            .where(MessageModel.sent_at > border)
            .where(DomainModel.id.not_in(replied))
        )
        return set(rows.scalars().all())
