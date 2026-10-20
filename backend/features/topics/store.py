"""Тема сайта в базе: что уже определено, запись, фильтр и счётчики.

**Тема лежит у домена** (`domains.site_topic*`), рядом с вердиктом судьи, и отдельного
хранилища не требует: отметка времени и версия и есть кэш. Свежая тема — той же версии
промпта (`classifier.TOPIC_VERSION`) и моложе срока (`TOPIC_TTL_DAYS`, год): такой сайт
прогон и досуд не спрашивают снова. «Не ясна» (модель сказала, что по тексту темы не понять)
— тоже знание, но свежее месяц (`TOPIC_UNCLEAR_TTL_DAYS`): «не понять» чаще говорит о бедном
тексте, чем о сайте, а текст бывает богаче — новая выдача, открывшаяся главная.

**Запись — в одном порядке, по имени домена** (`TopicStore.save`): два прогона с общими
доменами, пишущие их в разном порядке, ждали бы друг друга — та же взаимная блокировка, что
чинили у решений по домену (#415).

**Фильтр и счётчик — одним правилом** (`topic_is`, `topic_counts`): тема — основная или
вторая. «Финансы» находит и новостной сайт со второй темой «Финансы», и пункт фильтра
считает его там же, где найдёт фильтр, — как страна в топ-5 у «Гео».
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import topics as cfg
from backend.features.core.models.domain import DomainModel
from backend.features.topics.taxonomy import NOT_DEFINED


@dataclass(frozen=True, slots=True)
class TopicRecord:
    """Тема в том виде, в каком она ложится на домен."""

    topic: str | None
    secondary: str | None
    reason: str
    model: str | None
    version: str
    source: str


@dataclass(frozen=True, slots=True)
class Known:
    """Что база уже знает о сайтах до прохода."""

    #: Тема свежая: определять не нужно.
    fresh: frozenset[str]
    #: Заголовок главной, сохранённый прошлым судом (`domains.judge_home`): текст для
    #: сайта, чью главную в этом проходе не открывали.
    home_titles: dict[str, str]


#: Тема сайта в строке экрана — колонки домена: основная, вторая и почему.
TOPIC_COLUMNS = (
    DomainModel.site_topic,
    DomainModel.site_topic_secondary,
    DomainModel.topic_reason,
)


def is_fresh(now: datetime, version: str) -> ColumnElement[bool]:
    """Тема определена этой версией и моложе своего срока: тема — год, «не ясна» — месяц."""
    named = and_(
        DomainModel.site_topic.is_not(None),
        DomainModel.topic_at > now - timedelta(days=cfg.TTL_DAYS),
    )
    unclear = and_(
        DomainModel.site_topic.is_(None),
        DomainModel.topic_at > now - timedelta(days=cfg.UNCLEAR_TTL_DAYS),
    )
    return and_(DomainModel.topic_version == version, or_(named, unclear))


def topic_is(code: str) -> ColumnElement[bool]:
    """Условие фильтра: тема — основная или вторая; `none` — тема не определена."""
    if code == NOT_DEFINED:
        return DomainModel.site_topic.is_(None)
    return or_(DomainModel.site_topic == code, DomainModel.site_topic_secondary == code)


class TopicStore:
    """Темы доменов: что свежо, что записать."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def known(
        self, hosts: Sequence[str], *, version: str, now: datetime | None = None
    ) -> Known:
        """Свежие темы и сохранённые заголовки главных — одним запросом на пачку."""
        if not hosts:
            return Known(frozenset(), {})
        moment = now or datetime.now(UTC)
        rows = await self._session.execute(
            select(
                DomainModel.host,
                is_fresh(moment, version).label("fresh"),
                DomainModel.judge_home["title"].astext.label("title"),
            ).where(DomainModel.host.in_(list(dict.fromkeys(hosts))))
        )
        fresh: set[str] = set()
        titles: dict[str, str] = {}
        for host, current, title in rows.all():
            if current:
                fresh.add(host)
            if title:
                titles[host] = str(title)
        return Known(frozenset(fresh), titles)

    async def save(self, records: Mapping[str, TopicRecord], *, now: datetime | None = None) -> int:
        """Темы на домены. Домена ещё нет — заводится: тема не теряется оттого, что судья
        по этому сайту не ответил и домен не записал. Возвращает число записей."""
        if not records:
            return 0
        moment = now or datetime.now(UTC)
        hosts = sorted(records)
        await self._session.execute(
            insert(DomainModel)
            .values([{"host": host} for host in hosts])
            .on_conflict_do_nothing(index_elements=["host"])
        )
        for host in hosts:
            record = records[host]
            await self._session.execute(
                update(DomainModel)
                .where(DomainModel.host == host)
                .values(
                    site_topic=record.topic,
                    site_topic_secondary=record.secondary,
                    topic_reason=record.reason[:256],
                    topic_model=record.model,
                    topic_version=record.version,
                    topic_source=record.source[:16] or None,
                    topic_at=moment,
                )
            )
        return len(records)


async def topic_counts(session: AsyncSession, screen: Callable[..., Select[Any]]) -> dict[str, int]:
    """Тема → у скольких строк экрана она основная или вторая; `none` — сколько без темы.

    `screen` — основа экрана: колонки → выборка с его соединениями и условием (у доноров —
    `browse._screen`). Тем же правилом, что фильтр (`topic_is`): счётчик пункта — то, что
    фильтр найдёт.
    """
    primary = screen(DomainModel.site_topic.label("code"))
    secondary = screen(DomainModel.site_topic_secondary.label("code")).where(
        DomainModel.site_topic_secondary.is_not(None)
    )
    both = union_all(primary, secondary).subquery()
    rows = await session.execute(select(both.c.code, func.count()).group_by(both.c.code))
    return {str(code) if code is not None else NOT_DEFINED: int(n) for code, n in rows.tuples()}
