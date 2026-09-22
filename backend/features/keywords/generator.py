"""Сборка пула ключей: углы, добор, дедупликация.

Порядок работы и причины — в `docs/KEYWORD_MODES.md`. Здесь исполнение.

**Потолок считается после дедупликации, а не до.** Иначе набор выглядит
полным, а после отсева почти-дублей оказывается меньше заказанного —
и это выясняется, когда прогон уже посчитал смету.

**Добор прекращается, когда угол перестал давать новое.** Модель
исчерпала уникальные фразы; крутить её дальше — платить за повторы,
которые всё равно выбросит дедуп.

**Отчёт возвращается вместе с пулом.** Сколько просили, сколько пришло,
что отсеяла гигиена и по какой причине, сколько съел дедуп, сколько раз
модель отказала. Без этого нельзя ни настроить промпт, ни объяснить,
почему пул меньше заказа.

**Пустой пул из-за отказов модели — это ошибка, а не результат.** Пул
собирается до траты на выдачу, и молчаливый ноль здесь означает прогон,
который пойдёт дальше ни за чем. Пустой пул без отказов — законный
исход: модель отвечала, фразы не прошли гигиену.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backend.config import llm as cfg
from backend.features.keywords.angles import Angle, load_prompt, preset
from backend.features.keywords.client import Ask, KeygenClient, LlmError
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
    topic: str = ""  # про что просили; пусто — широкий пул
    asked: int = 0  # сколько фраз попросили у модели суммарно
    received: int = 0  # сколько пришло до отсева
    rejected: dict[str, str] = field(default_factory=dict)  # фраза → причина
    near_duplicates: int = 0
    rounds: int = 0
    tokens: int = 0
    calls: int = 0
    per_angle: dict[str, int] = field(default_factory=dict)
    #: Отказы модели. Пустой пул при пустом списке — модель правда ничего
    #: не придумала; пустой пул при непустом — она не отвечала.
    refusals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "preset": self.preset_name,
            "country": self.country,
            "language": self.language,
            "topic": self.topic,
            "cap": self.cap,
            "asked": self.asked,
            "received": self.received,
            "rejected": len(self.rejected),
            "near_duplicates": self.near_duplicates,
            "rounds": self.rounds,
            "tokens": self.tokens,
            "calls": self.calls,
            "per_angle": self.per_angle,
            "refusals": len(self.refusals),
        }


@dataclass(frozen=True, slots=True)
class Pool:
    """Пул ключей и то, как он собрался."""

    keywords: list[str]
    report: PoolReport


def build_user_prompt(*, country: str, language: str, angle: Angle, topic: str = "") -> str:
    """Просьба к модели: рынок, язык, тема и угол.

    Требование писать на языке рынка стоит отдельной строкой и с нажимом:
    без него модель переводит английские примеры буквально, а такими
    фразами никто не ищет.

    **Тема — то, чего у генерации не было вовсе.** В промпт уходили только
    страна, язык и угол, поэтому пул получался «обзоры в стране X»,
    а не «обзоры про Y в стране X»: прогон по ЮАР с пресетом обзоров
    выдал интернет-провайдеров там, где нужны были ставки. Пустая тема —
    законный исход: широкий пул иногда и нужен.
    """
    about = (
        f"topic: EVERY query must be about {topic}. Queries about anything else "
        f"are useless to us.\n"
        if topic.strip()
        else ""
    )
    return (
        f"market/country: {country}\n"
        f"language: {language}\n"
        f"{about}"
        f"CRITICAL: write EVERY query ONLY in {language}, using its native script. "
        f"Do NOT mix in any other language. Examples in the instructions show STYLE only — "
        f"express that intent natively.\n"
        f"focus ONLY on this content angle: {angle.focus}"
    )


def _settle(keywords: list[str], *, cap: int, report: PoolReport) -> Pool:
    """Отдать пул — или отказаться отдавать пустой, если он пуст из-за
    отказов модели.

    Пул собирается ДО траты на выдачу, и молчаливый ноль здесь означает
    прогон, который пойдёт дальше ни за чем. Пустой пул без отказов —
    законный исход: модель отвечала, фразы не прошли отбор.
    """
    if not keywords and report.refusals:
        raise LlmError(
            "пул ключей пуст: модель отказала "
            f"{len(report.refusals)} раз(а) — {'; '.join(report.refusals[:3])}"
        )

    logger.info("ключи: собрано %d из %d — %s", len(keywords), cap, report.as_dict())
    if report.refusals:
        logger.warning(
            "ключи: пул собран частично — %d отказ(ов) модели: %s",
            len(report.refusals),
            "; ".join(report.refusals[:3]),
        )
    return Pool(keywords=keywords, report=report)


def _log_dry(report: PoolReport, *, collected: list[str], cap: int) -> None:
    """Объяснить, почему добор встал. Две причины, и они разные.

    Раньше обе печатались одной строкой «модель исчерпала уникальные
    фразы» — и протухший ключ читался как исчерпанная фантазия модели.
    Вынесено из `_backfill` отдельной функцией: там речь про счёт фраз,
    здесь про то, что сказать человеку.
    """
    if report.received == 0:
        logger.error(
            "ключи: модель не дала ни одной фразы за %d вызов(ов) — %s",
            report.calls or len(report.refusals),
            "; ".join(report.refusals) or "ответы пустые, отказов не было",
        )
        return
    logger.info(
        "ключи: добор остановлен, модель исчерпала уникальные фразы (набрано %d из %d)",
        len(drop_near_duplicates(collected)),
        cap,
    )


class PoolBuilder:
    """Сборка пула для одной пары «рынок и язык»."""

    def __init__(self, client: KeygenClient, *, topic: str = "") -> None:
        self._client = client
        self._topic = topic.strip()

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
            preset_name=(preset_name or "wide"),
            country=country,
            language=language,
            cap=cap,
            topic=self._topic,
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

        report.refusals = list(self._client.refusals)
        return _settle(deduped[:cap], cap=cap, report=report)

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
                user=build_user_prompt(
                    country=country, language=language, angle=angle, topic=self._topic
                ),
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
                _log_dry(report, collected=collected, cap=cap)
                return collected

        return collected
