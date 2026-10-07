"""Мир продаж для тестов отправки и сборки очереди (срез 4.6b): подключённые продажи.

Гипотеза, цепочка писем общим набором на двух языках, «Отправитель», ящик продаж, своя
учётка почты продаж (переменные окружения), ссылка отписки — и лиды. Тексты заведомо
выдуманные: репозиторий публичный, коммерческих текстов в нём нет. Адреса — на
`*.example.test`: нулевой транспорт пишет только туда, боевой не собирается вовсе.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import SenderModel
from backend.features.letters.compose import Rendered, assemble
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.letters.uniqueness import difference
from backend.features.sales import chain, chain_text, hypotheses, sender
from backend.features.sales.models import LeadSource, LeadStatus, SalesLeadModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: Некруглое выдуманное время — не дата-обязательство.
NOW = datetime(2026, 10, 14, 9, 37, tzinfo=UTC)
SENDER_NAME = "Mira Testova"
SIGNATURE = "Mira Testova\nMade-up Test Agency"
ADDRESS = "Example Street 7, Testville"
OWN_KEY = "SG.made-up-sales-key"
SALES_BOX = "mira@mail-sales.example.test"
DONORS_BOX = "anna@mail-donors.example.test"

SUBJECT = "A made-up question for {{company}}"
FIRST_BODY = (
    "[greeting] rewrite\nHello {{name}},\n\n"
    "[ask] rewrite\nWho looks after search and marketing at {{company}} these days? "
    "This is a made-up test question about {{site}} and nothing else.\n\n"
    "[offer] fixed\nMade-up test offer: nothing real is sold here."
)
FOLLOW_BODY = {
    2: "[reminder] fixed\nA made-up first reminder for {{name}}.",
    3: "[reminder] fixed\nA made-up last reminder about {{company}}.",
}
RU_SUBJECT = "Выдуманный вопрос для {{company}}"
RU_FIRST = (
    "[greeting] rewrite\nЗдравствуйте, {{name}}!\n\n"
    "[ask] rewrite\nКто сейчас отвечает за поиск и маркетинг в {{company}}? "
    "Это выдуманный тестовый вопрос о сайте {{site}}, и только.\n\n"
    "[offer] fixed\nВыдуманное тестовое предложение: здесь ничего не продают."
)
RU_FOLLOW = {
    2: "[reminder] fixed\nВыдуманное первое напоминание для {{name}}.",
    3: "[reminder] fixed\nВыдуманное последнее напоминание о {{company}}.",
}


def connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подключить продажи: выключатель, своя учётка, ссылка отписки и метки ответа."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setenv("OUTREACH_SALES_SENDGRID_API_KEY", OWN_KEY)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")
    monkeypatch.setattr(outreach_cfg, "UNSUBSCRIBE_URL", "https://unsub.example.test/u")
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", "made-up-inbound-secret")
    monkeypatch.setattr(outreach_cfg, "SENDER_NAME", "Donor Desk Name")


async def settings(session: AsyncSession, **changes: str | None) -> None:
    """«Отправитель» продаж: имя, подпись и адрес — или то, что передали."""
    values: dict[str, str | None] = {
        "sender_name": SENDER_NAME,
        "signature": SIGNATURE,
        "physical_address": ADDRESS,
    }
    await sender.save(session, values | changes, author="тест", author_id=None)


async def chain_of(session: AsyncSession, *, hypothesis_id: int | None = None) -> None:
    """Цепочка общим набором (или гипотезы): три шага на английском и русском."""
    made = [
        chain_text.step_template(step=1, language="en", subject=SUBJECT, body=FIRST_BODY),
        chain_text.step_template(step=1, language="ru", subject=RU_SUBJECT, body=RU_FIRST),
    ]
    for step in (2, 3):
        made.append(chain_text.step_template(step=step, language="en", body=FOLLOW_BODY[step]))
        made.append(chain_text.step_template(step=step, language="ru", body=RU_FOLLOW[step]))
    for new in made:
        await chain.save(session, new, hypothesis_id=hypothesis_id, author="тест", author_id=None)


async def mailbox(
    session: AsyncSession, email: str = SALES_BOX, stage: Stage = Stage.SALES
) -> SenderModel:
    """Ящик этапа, которым можно писать сегодня."""
    box = SenderModel(
        domain=email.split("@", 1)[1],
        email=email,
        stage=stage,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=True,
    )
    session.add(box)
    await session.flush()
    return box


async def domain(session: AsyncSession, host: str) -> DomainModel:
    """Строка `domains` компании лида — та, что заводит загрузка базы."""
    found = DomainModel(host=host)
    session.add(found)
    await session.flush()
    return found


async def lead(
    session: AsyncSession,
    hypothesis_id: int,
    email: str,
    *,
    host: str | None = None,
    **fields: Any,
) -> SalesLeadModel:
    """Лид `ready` гипотезы; домен компании — `host` или домен адреса."""
    site = host or email.rpartition("@")[2]
    company = await _domain_of(session, site)
    defaults: dict[str, Any] = {
        "name": "Jane Example",
        "company": "Example Test Co",
        "language": "en",
        "source": LeadSource.IMPORT,
        "status": LeadStatus.READY,
    }
    made = SalesLeadModel(
        hypothesis_id=hypothesis_id, domain_id=company.id, email=email, **(defaults | fields)
    )
    session.add(made)
    await session.flush()
    return made


async def _domain_of(session: AsyncSession, host: str) -> DomainModel:
    found = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    return found or await domain(session, host)


@dataclass(frozen=True, slots=True)
class World:
    """Подключённые продажи: гипотеза, ящики продаж и доноров."""

    hypothesis_id: int
    sales_box: SenderModel
    donors_box: SenderModel


async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> World:
    """Продажи подключены целиком: учётка, «Отправитель», цепочка, ящики обоих этапов."""
    connect(monkeypatch)
    hypothesis = await hypotheses.add(session, "Выдуманная гипотеза", None)
    await settings(session)
    await chain_of(session)
    sales_box = await mailbox(session)
    donors_box = await mailbox(session, DONORS_BOX, Stage.DONORS)
    return World(hypothesis.id, sales_box, donors_box)


class CorridorRewriter:
    """Подделка модели: меняет слова переписываемых зон по одному, пока отличие письма
    от шаблона не войдёт в коридор, — как и просят настоящую модель."""

    def __init__(self, target: float = 0.2) -> None:
        self.target = target
        self.seen: list[Personalization] = []

    async def rewrite(self, rendered: Rendered, about: Personalization) -> RewriteResult:
        self.seen.append(about)
        zones = {zone.name: zone.text.split(" ") for zone in rendered.rewritable()}
        for name, words in zones.items():
            for index in range(len(words)):
                words[index] = f"changed{index}"
                result = {key: " ".join(value) for key, value in zones.items()}
                if difference(rendered.body, assemble(rendered, result).body) >= self.target:
                    return RewriteResult(zones=result, tokens_spent=37)
            zones[name] = words
        return RewriteResult(zones={key: " ".join(value) for key, value in zones.items()})


class FlatRewriter:
    """Подделка модели, которая ничего не переписала: письмо — шаблон, отличие ноль."""

    async def rewrite(self, _rendered: Rendered, _about: Personalization) -> RewriteResult:
        return RewriteResult(notes=["LLM_API_KEY не задан — письма уходят шаблонными"])


class NoRewrite:
    """Модель, которую звать нельзя: отказ — раньше неё."""

    async def rewrite(self, *_: object) -> RewriteResult:
        raise AssertionError("сборка продаж дошла до модели")
