"""Текст письма для контактной формы донора — тот же, что ушёл бы ему письмом.

У донора с «только формой» адреса нет, и письмо ему сборка не готовит. Пишет человек —
через форму на сайте, — и до 20.10.2026 текст он сочинял сам. Теперь текст тот же, что
у писем донорам: тот же шаблон, те же подстановки, то же переписывание моделью под сайт
(`building.py`), та же проверка на метрики и тот же подбор зон под коридор отличия.

**Шаблон — рассылки прогона, в котором донора приняли.** Текст первого письма утверждают
на экране при создании рассылки, и донор формы получил бы его, попади он в рассылку:
берётся текст последней рассылки Этапа 1 этого прогона, нет такой — последней рассылки
Этапа 1 вообще (кроме перезапроса цены — у него свой текст), рассылок не было —
умолчание из кода. Прогон — первым: у рассылок разных прогонов разный язык, и текст
последней чужой рассылки уходил бы в форму сайта другой страны (ревью 20.10.2026). Чей
текст взят, сказано рядом с ним (`based_on`).

**Язык и тема — как у письма.** Страна — прогона, в котором донора приняли: от неё язык
письма (ТЗ), а не от языка сайта; ниша — ключи, по которым он в том прогоне нашёлся;
о чём сайт — цитата его страницы, сохранённая судьёй. Как и у писем, язык текста —
язык шаблона: модель переписывает зоны на том же языке (`prompts/rewrite.md`).

**Вызов модели стоит денег — текст хранится** (`form_letters`). Второй раз его открывают
бесплатно; собрать заново — по просьбе человека. Расход — в журнал расхода, как у сборки
писем, и под тем же потолком (`usage.ensure_llm_within_cap`).

**Форму сервис не отправляет.** У форм капча и защита от спама, и обходить их значит
вести себя как спам-робот; отправляет человек (`api/contacts/form_letter.py`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.contacts import forms
from backend.features.core import usage
from backend.features.core.domain import Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.form_letter import FormLetterModel
from backend.features.core.models.outreach import CampaignModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.crawl.niche import LINKS
from backend.features.letters import compose, guards, selection
from backend.features.letters.chain import RECHECK_CAMPAIGN
from backend.features.letters.rewrite import Personalization, RewriteClient
from backend.features.letters.template import Template, for_stage, of_campaign
from backend.features.letters.uniqueness import difference

logger = logging.getLogger(__name__)

#: Операция журнала расхода — та же, что у сборки писем: это то же переписывание
#: первого письма донору моделью, и на экране «Расход» оно и есть «переписывание писем».
OPERATION = "letter_rewrite"

#: Чей текст взят, если рассылок донорам ещё не было или последняя шла умолчанием.
DEFAULT_TEXT = "текст первого письма по умолчанию"

#: Страна письма, если донора не принимали ни в одном прогоне (заведён руками):
#: та же, что у сборки без прогонов (`building.BuildRequest.country`).
FALLBACK_COUNTRY = "us"


@dataclass(frozen=True, slots=True)
class Basis:
    """Из чего собирается письмо донору формы — то же, что знает сборка писем."""

    template: Template
    based_on: str
    country: str
    niche: tuple[str, ...]
    about: str | None


async def _campaign(session: AsyncSession, run_id: int | None) -> CampaignModel | None:
    """Последняя рассылка донорам — прогона `run_id` или любая (`None`)."""
    query = select(CampaignModel).where(
        CampaignModel.stage == Stage.DONORS,
        CampaignModel.audience == LINKS,
        CampaignModel.name != RECHECK_CAMPAIGN,
    )
    if run_id is not None:
        query = query.where(CampaignModel.run_id == run_id)
    campaign: CampaignModel | None = await session.scalar(
        query.order_by(CampaignModel.id.desc()).limit(1)
    )
    return campaign


async def _template(session: AsyncSession, run: RunModel | None) -> tuple[Template, str]:
    """Текст рассылки прогона донора, иначе последней рассылки донорам — или умолчание
    этапа, словами «чей»."""
    campaign = await _campaign(session, run.id) if run is not None else None
    campaign = campaign or await _campaign(session, None)
    if campaign is None or not campaign.letter_template:
        return for_stage(Stage.DONORS), DEFAULT_TEXT
    template = of_campaign(Stage.DONORS, campaign.letter_template, campaign.audience)
    return template, f"текст рассылки «{campaign.name}»"


async def _run_of(session: AsyncSession, domain_id: int) -> RunModel | None:
    """Прогон, в котором донора приняли последним: от него страна и ниша письма."""
    run: RunModel | None = await session.scalar(
        select(RunModel)
        .join(RunCandidateModel, RunCandidateModel.run_id == RunModel.id)
        .where(RunCandidateModel.domain_id == domain_id, RunCandidateModel.status == "accepted")
        .order_by(RunModel.id.desc())
        .limit(1)
    )
    return run


def _found_by(run: RunModel | None, host: str) -> tuple[str, ...]:
    """Ключи прогона, по которым нашёлся домен, — как у сборки (`scope._found_by`)."""
    if run is None:
        return ()
    found = (run.candidates or {}).get("found_by") or {}
    return tuple(found.get(host) or ())


async def basis(session: AsyncSession, domain_id: int, host: str) -> Basis:
    """Шаблон, страна, ниша и цитата сайта — всё, что сборка писем знала бы о доноре."""
    run = await _run_of(session, domain_id)
    template, based_on = await _template(session, run)
    about = await session.scalar(select(DomainModel.judge_quote).where(DomainModel.id == domain_id))
    return Basis(
        template=template,
        based_on=based_on,
        country=(run.country if run is not None else FALLBACK_COUNTRY).lower(),
        niche=_found_by(run, host),
        about=about,
    )


async def stored(session: AsyncSession, domain_id: int) -> FormLetterModel | None:
    """Собранный раньше текст для формы этого сайта; `None` — не собирали.

    Строка перечитывается из базы и тогда, когда сессия её уже видела: текст
    переписывают заново той же сессией (`build`), и прежний объект врал бы.
    """
    letter: FormLetterModel | None = await session.scalar(
        select(FormLetterModel)
        .where(FormLetterModel.domain_id == domain_id, FormLetterModel.stage == Stage.DONORS.value)
        .execution_options(populate_existing=True)
    )
    return letter


async def build(
    session: AsyncSession, donor_id: int, *, rewriter: RewriteClient, by: str | None = None
) -> FormLetterModel:
    """Собрать текст письма для формы донора и сохранить его. Тратит вызов модели.

    Донор — из очереди «Формы» (`forms.form_row`): только форма, адреса нет, ему пишем.
    Потолок расхода на модель проверяется до вызова, как у сборки писем.
    """
    row = await forms.form_row(session, donor_id)
    known = await basis(session, row.domain_id, row.host)
    await usage.ensure_llm_within_cap(session)
    rendered = compose.render(
        known.template, compose.values_for(host=row.host, domain_id=row.domain_id)
    )
    rewritten = await rewriter.rewrite(
        rendered,
        Personalization(
            host=row.host,
            country=known.country,
            niche=known.niche,
            about=known.about,
            names=(outreach_cfg.SENDER_NAME,),
        ),
    )
    chosen = selection.select(rendered, rewritten.zones, seed=row.domain_id)
    letter = compose.assemble(rendered, chosen.zones if chosen is not None else rewritten.zones)
    guards.assert_letter_clean(letter.subject, letter.body)
    if rewritten.tokens_spent:
        usage.record(session, operation=OPERATION, units=rewritten.tokens_spent, stage=Stage.DONORS)
    values: dict[str, Any] = {
        "subject": letter.subject,
        "body": letter.body,
        "uniqueness_pct": difference(letter.plain_body, letter.body),
        "tokens": rewritten.tokens_spent,
        "based_on": known.based_on,
        "notes": "; ".join(rewritten.notes) or None,
        "built_by": by,
    }
    statement = insert(FormLetterModel).values(
        domain_id=row.domain_id, stage=Stage.DONORS.value, **values
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_form_letters_domain_stage",
            set_={**values, "updated_at": func.now()},
        )
    )
    logger.info(
        "формы: текст письма для %s собран (%s, токенов %s)",
        row.host,
        known.based_on,
        rewritten.tokens_spent,
    )
    found = await stored(session, row.domain_id)
    assert found is not None  # только что записан
    return found
