"""Общее начало отбора адресатов оффера Этапа 2: рекламодатель и его свежий адрес.

Им пользуются оба отбора — по найденной ссылке (`recipients`) и бизнесов
ниши (`niche_recipients`): строка на адрес, с номером попытки.

**Рекламодатель — тот, кому пишем** (`advertiser_rules.in_work`): по ссылке — всякий,
бизнес ниши — после «пишем» человека; тем же условием его считает список «Все
рекламодатели». До 10.11.2026 отбор по ссылке и его воронка брали все строки
`advertisers`: первая ступень воронки «Писем» считала и бизнесы ниши без решения и
расходилась со «всего» списка, а бизнес ниши, чью ссылку нашёл и обход (перевод пишет
её и в его строку), получал оффер по ссылке без «пишем» человека.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import aliased

from backend.features.core.domain import Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.crawl.advertiser_rules import in_work
from backend.features.letters import attempts


def donor_geo_of(donor_host: Any) -> Any:
    """Гео донора, на чьей площадке нашли ссылку, — колонкой к строке рекламодателя.

    Площадка записана именем хоста, а не номером: ищем донора по домену.
    Нет донора или гео — пусто, и письмо обходится без фразы про аудиторию.
    """
    donor_domain = aliased(DomainModel)
    return (
        select(DonorModel.geo)
        .join(donor_domain, donor_domain.id == DonorModel.domain_id)
        .where(donor_domain.host == func.lower(donor_host))
        .limit(1)
        .scalar_subquery()
    )


def advertiser_domains() -> Select[tuple[int]]:
    """Домены рекламодателей, которым пишем, — первая ступень воронки отбора по ссылке."""
    return (
        select(DomainModel.id)
        .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
        .where(in_work())
    )


def advertiser_addresses(*columns: Any) -> Select[Any]:
    """Домен, адрес, номер попытки — и `columns` своего отбора: рекламодатели, которым пишем."""
    return (
        select(
            DomainModel.id.label("domain_id"),
            DomainModel.host.label("host"),
            ContactModel.id.label("contact_id"),
            ContactModel.email.label("email"),
            attempts.attempt_number(DomainModel.id, Stage.ADVERTISERS).label("attempt"),
            *columns,
        )
        .join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
        .join(ContactModel, ContactModel.domain_id == DomainModel.id)
        .where(in_work(), attempts.fresh(ContactModel))
    )
