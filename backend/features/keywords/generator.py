"""Сборка пула ключей: углы, добор, дедупликация.

Порядок работы и причины — в `docs/KEYWORD_MODES.md`. Здесь исполнение.

**Потолок считается после дедупликации, а не до.** Иначе набор выглядит
полным, а после отсева почти-дублей оказывается меньше заказанного —
и это выясняется, когда прогон уже посчитал смету.

**Добор прекращается, когда угол перестал давать новое.** Модель
исчерпала уникальные фразы; крутить её дальше — платить за повторы,
которые всё равно выбросит дедуп.

**Отчёт возвращается вместе с пулом.** Сколько просили, сколько пришло,
что отсеяла гигиена и по какой причине, сколько съел дедуп. Без этого
нельзя ни настроить промпт, ни объяснить, почему пул меньше заказа.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backend.config import llm as cfg
from backend.features.keywords.angles import Angle, load_prompt, preset
from backend.features.keywords.client import Ask, KeygenClient
from backend.features.keywords.dedup import (
    drop_near_duplicates,
    split_over_angles,
)
from backend.features.keywords.hygiene import clean

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PoolReport:
    """Чем кончилась сборка. Идёт в отчёт прогона и в аудит."""

    preset_name: str
    country: str
    language: str
    cap: int
    asked: int = 0  # сколько фраз попросили у модели суммарно
    received: int = 0  # сколько пришло до отсева
    rejected: dict[str, str] = field(default_factory=dict)  # фраза → причина
    near_duplicates: int = 0
    rounds: int = 0
    tokens: int = 0
    calls: int = 0
    per_angle: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "preset": self.preset_name,
            "country": self.country,
            "language": self.language,
            "cap": self.cap,
            "asked": self.asked,
            "received": self.received,
            "rejected": len(self.rejected),
            "near_duplicates": self.near_duplicates,
            "rounds": self.rounds,
            "tokens": self.tokens,
            "calls": self.calls,
            "per_angle": self.per_angle,
        }


@dataclass(frozen=True, slots=True)
class Pool:
    """Пул ключей и то, как он собрался."""

    keywords: list[str]
    report: PoolReport


def build_user_prompt(*, country: str, language: str, angle: Angle) -> str:
    """Просьба к модели: рынок, язык и угол.

    Требование писать на языке рынка стоит отдельной строкой и с нажимом:
    без него модель переводит английские примеры буквально, а такими
    фразами никто не ищет.
    """
    return (
        f"market/country: {country}\n"
        f"language: {language}\n"
        f"CRITICAL: write EVERY query ONLY in {language}, using its native script. "
        f"Do NOT mix in any other language. Examples in the instructions show STYLE only — "
        f"express that intent natively.\n"
        f"focus ONLY on this content angle: {angle.focus}"
    )


class PoolBuilder:
    """Сборка пула для одной пары «рынок и язык»."""

    def __init__(self, client: KeygenClient) -> None:
        self._client = client

    async def build(
        self,
        *,
        cap: int,
        country: str,
        language: str = "English",
        preset_name: str | None = None,
    ) -> Pool:
        angles = preset(preset_name)
        report = PoolReport(
            preset_name=(preset_name or "wide"), country=country, language=language, cap=cap
        )
        if cap <= 0:
            return Pool(keywords=[], report=report)

        collected: list[str] = []
        seen: set[str] = set()

        wanted = split_over_angles(cap, len(angles))
        for angle, want in zip(angles, wanted, strict=True):
            fresh = await self._ask_angle(angle, want, country, language, seen, report)
            collected.extend(fresh)
            report.per_angle[angle.title] = len(fresh)

        collected = await self._backfill(collected, angles, cap, country, language, seen, report)

        deduped = drop_near_duplicates(collected)
        report.near_duplicates = len(collected) - len(deduped)
        report.tokens = self._client.tokens_spent
        report.calls = self._client.calls

        logger.info("ключи: собрано %d из %d — %s", len(deduped[:cap]), cap, report.as_dict())
        return Pool(keywords=deduped[:cap], report=report)

    async def _ask_angle(
        self,
        angle: Angle,
        want: int,
        country: str,
        language: str,
        seen: set[str],
        report: PoolReport,
    ) -> list[str]:
        """Спросить один угол. Просим с запасом: часть отсеют гигиена и дедуп."""
        if want <= 0:
            return []

        request = min(want + cfg.ANGLE_BUFFER, cfg.MAX_PHRASES_PER_CALL)
        report.asked += request

        system = load_prompt(angle.prompt).replace("{max_phrases}", str(request))
        raw = await self._client.ask(
            Ask(
                system=system,
                user=build_user_prompt(country=country, language=language, angle=angle),
                max_phrases=request,
            )
        )
        report.received += len(raw)

        good, rejected = clean(raw)
        report.rejected.update(rejected)

        fresh = [phrase for phrase in good if phrase not in seen]
        seen.update(fresh)
        return fresh[:want]

    async def _backfill(
        self,
        collected: list[str],
        angles: tuple[Angle, ...],
        cap: int,
        country: str,
        language: str,
        seen: set[str],
        report: PoolReport,
    ) -> list[str]:
        """Добрать недостающее — считая от числа ПОСЛЕ дедупликации."""
        for _ in range(cfg.BACKFILL_ROUNDS):
            deficit = cap - len(drop_near_duplicates(collected))
            if deficit <= 0:
                return collected

            report.rounds += 1
            before = len(collected)
            per_angle = max(1, -(-deficit // len(angles)))

            for angle in angles:
                fresh = await self._ask_angle(angle, per_angle, country, language, seen, report)
                collected.extend(fresh)
                report.per_angle[angle.title] = report.per_angle.get(angle.title, 0) + len(fresh)
                if len(drop_near_duplicates(collected)) >= cap:
                    return collected

            if len(collected) == before:
                # Ни один угол не дал новой фразы: уникальные кончились.
                # Крутить модель дальше — платить за повторы.
                logger.info(
                    "ключи: добор остановлен, модель исчерпала уникальные фразы (набрано %d из %d)",
                    len(drop_near_duplicates(collected)),
                    cap,
                )
                return collected

        return collected
