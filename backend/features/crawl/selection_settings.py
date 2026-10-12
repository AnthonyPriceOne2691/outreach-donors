"""Пороги, веса и списки Этапа 2, которые действуют, — для экрана «Пороги».

Сами значения — настройками окружения (`config/advertisers.py`, сверка ТЗ 11.10.2026);
здесь они собраны словами экрана. Читаются у модулей, которые ими судят
(`scoring`, `big_sites`, `denylist`), а не у настроек напрямую: экран показывает то,
чем обход судит на самом деле.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import advertisers as cfg
from backend.config import crawl as crawl_cfg
from backend.features.crawl import big_sites, denylist, scoring


@dataclass(frozen=True, slots=True)
class Number:
    """Вес или порог: что значит, сколько и где меняется."""

    label: str
    value: int
    setting: str


@dataclass(frozen=True, slots=True)
class Listed:
    """Список «кому не пишем»: что в нём и где меняется."""

    label: str
    values: tuple[str, ...]
    setting: str


@dataclass(frozen=True, slots=True)
class InForce:
    """Всё, чем Этап 2 судит ссылки сейчас."""

    points: tuple[Number, ...]
    thresholds: tuple[Number, ...]
    lists: tuple[Listed, ...]


def _number(label: str, value: int, field: str) -> Number:
    return Number(label=label, value=value, setting=cfg.setting_name(field))


def _listed(label: str, values: frozenset[str] | tuple[str, ...], field: str) -> Listed:
    return Listed(label=label, values=tuple(sorted(values)), setting=cfg.setting_name(field))


def in_force() -> InForce:
    """Веса, пороги и списки, которыми обход судит ссылки сейчас."""
    points = (
        _number("rel=sponsored", scoring.POINTS_REL_SPONSORED, "points_rel_sponsored"),
        _number("пометка рекламы в статье или адресе", scoring.POINTS_MARKER, "points_marker"),
        _number(
            "dofollow на коммерческий из тела статьи",
            scoring.POINTS_DOFOLLOW_IN_BODY,
            "points_dofollow_in_body",
        ),
        _number("коммерческий анкор", scoring.POINTS_COMMERCIAL_ANCHOR, "points_commercial_anchor"),
        _number("тематика не совпадает", scoring.POINTS_OFF_TOPIC, "points_off_topic"),
        _number("партнёрская метка в адресе", scoring.POINTS_AFFILIATE, "points_affiliate"),
        _number(
            "ссылка-источник в платной статье", scoring.POINTS_PAID_SOURCE, "points_paid_source"
        ),
        _number(
            "коммерческая ссылка под nofollow", scoring.POINTS_PAID_LOOKING, "points_paid_looking"
        ),
        _number(
            "ссылки со многих страниц или у нескольких доноров (с сильным признаком)",
            scoring.POINTS_REPEATED,
            "points_repeated",
        ),
    )
    thresholds = (
        _number("«куплено» — баллов от", scoring.BOUGHT_AT, "bought_at"),
        _number("«на ручную проверку» — баллов от", scoring.REVIEW_AT, "review_at"),
        _number("«много страниц» — страниц от", scoring.REPEAT_PAGES, "repeat_pages"),
        _number("«у нескольких наших доноров» — доноров от", scoring.BOOST_DONORS, "boost_donors"),
        _number("кому не пишем — DR выше", big_sites.MAX_DR, "max_dr"),
        Number(
            label="давнее размещение — статьи старше, лет",
            value=crawl_cfg.STALE_YEARS,
            setting="CRAWL_STALE_YEARS",
        ),
        _number(
            "поставщик из списка держится, месяцев",
            cfg.SUPPLIER_TERM_MONTHS,
            "supplier_term_months",
        ),
    )
    lists = (
        _listed(
            "гос. и учебные зоны сверх общего правила", denylist.BLOCKED_SUFFIXES, "deny_zones"
        ),
        _listed("справочники и наука", denylist.REFERENCE_DOMAINS, "deny_reference"),
        _listed("соцсети и видео", denylist.SOCIAL_DOMAINS, "deny_social"),
        _listed("инфраструктура сайта", denylist.INFRASTRUCTURE_DOMAINS, "deny_infrastructure"),
        _listed("сокращатели ссылок", denylist.SHORTENER_DOMAINS, "deny_shorteners"),
        _listed("крупные СМИ", denylist.MAJOR_MEDIA, "deny_media"),
        _listed("документы", denylist.DOCUMENT_SUFFIXES, "deny_documents"),
        _listed("«куплено» только человеком — зоны", scoring.NONPROFIT_SUFFIXES, "nonprofit_zones"),
    )
    return InForce(points=points, thresholds=thresholds, lists=lists)


def snapshot() -> dict[str, object]:
    """Чем судили — имя настройки → значение, списки по алфавиту. Ложится на обход
    при пересчёте (`gate.judge_run`): веса меняются окружением, и через месяц
    по причинам кандидата и этому снимку видно, каким порогом его судили."""
    found = in_force()
    numbers: dict[str, object] = {
        item.setting: item.value for item in (*found.points, *found.thresholds)
    }
    return {**numbers, **{item.setting: list(item.values) for item in found.lists}}
