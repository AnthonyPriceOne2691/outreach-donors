"""Потолки модели на экране «Расход»: общий потолок сервиса, лимиты частей и свободный остаток,
потрачено за сутки и сброс, кто и когда менял (слово Anthony 29.10.2026, `core/llm_caps.py`).

Своим файлом, как кап Ahrefs (`cap_schemas.py`): схемы расхода — у предела длины, а потолки
модели — своя тема. Экран называет, а не считает: лимит, «потрачено» и «осталось» — тем же
правилом и тем же счётом, что у сторожа (`usage.spent_by_part`, `llm_caps.available`), и число на
экране не расходится с отказом. Без права «Продажи» части продаж на экране нет, и расход
продаж не входит в «потрачено сегодня» (П2б).
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Literal

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.agent.guarding import drafts_cap
from backend.features.core import llm_caps, usage
from backend.features.core.llm_caps import Block, LlmCapChange
from backend.features.sales.usage_cap import sales_cap


class LlmCapsBody(BaseModel):
    """Новый блок: общий (`general_tokens` — число из списка, пусто — без потолка) и лимиты
    частей процентами общего. Список, шаг и сумму проверяет сама смена (`llm_caps.refusal`) —
    тем же правилом, что экран получает в `LlmCapsCard`."""

    general_tokens: int | None
    sales_share: int
    agent_share: int
    outreach_share: int

    @field_validator(
        "general_tokens", "sales_share", "agent_share", "outreach_share", mode="before"
    )
    @classmethod
    def _whole(cls, value: object) -> object:
        """Числа — целые: дробь, строка и «да/нет» — отказ словами, а не именем поля."""
        if value is None or (isinstance(value, int) and not isinstance(value, bool)):
            return value
        raise PydanticCustomError(
            "llm_cap_not_whole",
            "Потолок и лимиты — целые числа: токены из списка, проценты шагом 5",
        )

    def block(self) -> Block:
        return Block(self.general_tokens, self.sales_share, self.agent_share, self.outreach_share)


class BlockCard(BaseModel):
    """Блок числами: общий (`None` — без потолка) и лимиты частей, проценты общего."""

    general_tokens: int | None
    sales_share: int
    agent_share: int
    outreach_share: int

    @classmethod
    def of(cls, saved: Block) -> BlockCard:
        return cls(
            general_tokens=saved.general,
            sales_share=saved.sales,
            agent_share=saved.agent,
            outreach_share=saved.outreach,
        )


class LlmCapChangeCard(BaseModel):
    """Смена блока: что поставили, кто и когда; `set_by` пусто — учётку удалили."""

    id: int
    block: BlockCard
    set_by: str | None
    set_at: datetime

    @classmethod
    def of(cls, change: LlmCapChange) -> LlmCapChangeCard:
        return cls(
            id=change.id,
            block=BlockCard.of(change.block),
            set_by=change.set_by,
            set_at=change.set_at,
        )


class PartCard(BaseModel):
    """Часть: лимит (процент общего и токены), потрачено и осталось за сутки."""

    part: str
    title: str
    #: Лимит процентом общего; `None` — блок не сохраняли, потолок части — из `.env`.
    share: int | None
    #: Лимит, токены за сутки; `None` — без потолка.
    limit: int | None
    #: Потрачено частью за сутки UTC — тем же счётом, что у сторожа.
    spent: int
    #: Осталось до лимита; `None` — без потолка.
    left: int | None
    #: Выбрано от лимита целыми процентами вниз; `None` — без потолка.
    used_percent: int | None


class LlmCapsCard(BaseModel):
    """Потолки модели за сутки: схема, общий, части, свободный остаток, сброс и смены."""

    #: «env» — блок не сохраняли, потолки из `.env`; «parts» — общий и лимиты частей с экрана.
    scheme: Literal["env", "parts"]
    #: Общий потолок сервиса, токены за сутки; `None` — без потолка.
    general: int | None
    #: Свободный остаток — проценты и токены общего, не розданные частям; `None` — не сохраняли
    #: (у токенов — ещё и общий без потолка).
    free_share: int | None
    free: int | None
    parts: list[PartCard]
    #: Потрачено моделью за сутки — всё, что видит учётка: без права «Продажи» — без продаж.
    spent: int
    #: Что сохранено — с него открывается окно; `None` — не сохраняли, окно открывается с
    #: предложенного (`suggested`).
    saved: BlockCard | None
    suggested: BlockCard
    #: Общий — только эти числа токенов в сутки или «без потолка»; лимиты — шагом `share_step`.
    general_presets: list[int]
    share_step: int
    #: Сутки счёта — с полуночи UTC; сброс — следующая полночь UTC (03:00 по Москве).
    day_start: datetime
    resets_at: datetime
    #: «через 5 ч 12 мин (в 03:00 по Москве)» — те же слова, что у отказа.
    reset_in: str
    #: Последние смены блока, новые первыми; пусто — не меняли.
    changes: list[LlmCapChangeCard]


async def llm_caps_card(session: AsyncSession, *, sales: bool, now: datetime) -> LlmCapsCard:
    """Потолки модели на эту минуту — по свежему чтению блока (`llm_caps.refresh`). `sales` —
    видит ли учётка этап продаж: без права «Продажи» части продаж на экране нет."""
    await llm_caps.refresh(session)
    day = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
    spent = await usage.spent_by_part(session, since=day)
    saved = llm_caps.block()
    shown = [part for part in llm_caps.PARTS if sales or part != llm_caps.PART_SALES]
    return LlmCapsCard(
        scheme="env" if saved is None else "parts",
        general=llm_caps.daily_cap() or None,
        free_share=None if saved is None else saved.free_share,
        free=None if saved is None or saved.general is None else saved.free,
        parts=[_part(part, saved, spent) for part in shown],
        spent=sum(spent[part] for part in shown),
        saved=None if saved is None else BlockCard.of(saved),
        suggested=BlockCard.of(llm_caps.SUGGESTED),
        general_presets=list(llm_caps.GENERAL_PRESETS),
        share_step=llm_caps.SHARE_STEP,
        day_start=day,
        resets_at=llm_caps.reset_at(now),
        reset_in=llm_caps.reset_words(now),
        changes=[LlmCapChangeCard.of(change) for change in await llm_caps.cap_changes(session)],
    )


def _part(part: str, saved: Block | None, spent: llm_caps.Spent) -> PartCard:
    """Часть числами: лимит блока, а не сохраняли — потолок части из `.env` (у аутрича своего
    нет — держит общий)."""
    if saved is not None:
        limit = saved.limit(part) or None
        left = None if limit is None else llm_caps.available(saved, part, spent)
        share: int | None = saved.share(part)
    else:
        env = {llm_caps.PART_SALES: sales_cap, llm_caps.PART_AGENT: drafts_cap}.get(part)
        limit = None if env is None else env().tokens or None
        left = None if limit is None else max(0, limit - spent[part])
        share = None
    return PartCard(
        part=part,
        title=llm_caps.TITLES[part],
        share=share,
        limit=limit,
        spent=spent[part],
        left=left,
        used_percent=None if limit is None else min(100, spent[part] * 100 // limit),
    )
