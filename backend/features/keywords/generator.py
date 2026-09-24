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
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.config import llm as cfg
from backend.features.keywords.angles import Angle, load_prompt, preset
from backend.features.keywords.client import Ask, KeygenClient, LlmError
from backend.features.keywords.dedup import (
    drop_near_duplicates,
    split_over_angles,
)
from backend.features.keywords.footprints import (
    expand,
    templates_for,
    topics_needed,
    with_niche,
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
    #: Сколько дала каждая тема и каждый язык. Без этих чисел пул,
    #: перекошенный в одну тему, внешне неотличим от ровного.
    per_topic: dict[str, int] = field(default_factory=dict)
    per_language: dict[str, int] = field(default_factory=dict)
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
            "per_topic": self.per_topic,
            "per_language": self.per_language,
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


#: Меньше этого на одно сочетание «тема × язык» просить незачем: буфер
#: и дедуп съедают почти всё, а каждый вызов стоит своего минимума.
#: Замер: потолок 30 обошёлся в двенадцать вызовов, потолок 100 — в девять.
MIN_PER_COMBINATION = 5


def _combinations(topics: Sequence[str], languages: Sequence[str]) -> list[tuple[str, str]]:
    """Все пары «тема × язык». Пустой язык — «English»: у пула всегда есть
    язык, даже когда рынок его не назвал."""
    langs = _clean_list(languages) or ("English",)
    return [(topic, language) for topic in topics for language in langs]


def _new_report(
    combinations: Sequence[tuple[str, str]], *, cap: int, country: str, preset_name: str | None
) -> PoolReport:
    """Шапка отчёта: что просили. Темы и языки перечисляются строкой —
    по ней потом видно, из чего пул собирался."""
    return PoolReport(
        preset_name=(preset_name or "wide"),
        country=country,
        language=", ".join(dict.fromkeys(language for _, language in combinations)),
        cap=cap,
        topic=", ".join(dict.fromkeys(topic for topic, _ in combinations if topic)),
    )


def _count(report: PoolReport, *, topic: str, language: str, got: int) -> None:
    """Записать, сколько дало сочетание. Отдельными столбцами: пул,
    перекошенный в одну тему или язык, по общему числу неотличим
    от ровного."""
    name = topic or "без темы"
    report.per_topic[name] = report.per_topic.get(name, 0) + got
    report.per_language[language] = report.per_language.get(language, 0) + got


def _refuse_if_too_thin(cap: int, combinations: Sequence[tuple[str, str]]) -> None:
    """Отказать, если на сочетание приходится слишком мало.

    Тонкая доля — это не маленький пул, а испорченный: буфер просит
    с запасом, дедуп режет, и на выходе получается два ключа вместо пяти.
    Отказ называет, что делать, потому что выходов ровно два.
    """
    # Одно сочетание делить не на что: маленький пул там — это маленький
    # пул, а не рваный. Правило про ДОЛЮ, и включается оно с деления.
    if len(combinations) < 2 or cap >= MIN_PER_COMBINATION * len(combinations):
        return
    topics = len({topic for topic, _ in combinations})
    langs = len({language for _, language in combinations})
    raise ValueError(
        f"На {len(combinations)} сочетаний ({topics} тем × {langs} яз.) просят {cap} ключей — "
        f"это меньше {MIN_PER_COMBINATION} на каждое, и пул выйдет рваным. "
        f"Поднимите потолок до {MIN_PER_COMBINATION * len(combinations)} или уберите тему"
    )


def _clean_list(values: Sequence[str]) -> tuple[str, ...]:
    """Список без пустых, без повторов и в прежнем порядке."""
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


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

    def __init__(self, client: KeygenClient, *, topics: Sequence[str] = ()) -> None:
        self._client = client
        self._topics = _clean_list(topics) or ("",)

    async def build(
        self,
        *,
        cap: int,
        country: str,
        languages: Sequence[str] = ("English",),
        preset_name: str | None = None,
    ) -> Pool:
        """Собрать пул по всем сочетаниям «тема × язык».

        Набор виденного общий на все сочетания: фраза, придуманная дважды,
        схлопывается сразу, а не после того, как за неё дважды заплатили.
        """
        angles = preset(preset_name)
        combinations = _combinations(self._topics, languages)
        report = _new_report(combinations, cap=cap, country=country, preset_name=preset_name)
        if cap <= 0:
            return Pool(keywords=[], report=report)
        _refuse_if_too_thin(cap, combinations)
        if angles[0].footprints:
            return await self._footprints(
                angles[0], cap=cap, country=country, combinations=combinations, report=report
            )

        collected: list[str] = []
        seen: set[str] = set()

        for (topic, language), share in zip(
            combinations, split_over_angles(cap, len(combinations)), strict=True
        ):
            got = await self._one(share, angles, country, language, topic, seen, report)
            collected.extend(got)
            _count(report, topic=topic, language=language, got=len(got))

        deduped = drop_near_duplicates(collected)
        report.near_duplicates = len(collected) - len(deduped)
        report.tokens = self._client.tokens_spent
        report.calls = self._client.calls

        report.refusals = list(self._client.refusals)
        return _settle(deduped[:cap], cap=cap, report=report)

    async def _footprints(
        self,
        angle: Angle,
        *,
        cap: int,
        country: str,
        combinations: Sequence[tuple[str, str]],
        report: PoolReport,
    ) -> Pool:
        """Набор «guest»: темы у модели, слова футпринта — по таблице языка.

        Шаблоны каждого языка проверяются до первого вызова: рынок без
        футпринтов отказывает, не потратив ни токена. Почти-дубли убираются
        среди тем — запросы одной темы различаются шаблоном, и дедуп по
        словам их бы схлопнул (`keywords/footprints.py`).
        """
        templates = {language: templates_for(language) for _, language in combinations}
        seen: set[str] = set()
        collected: list[str] = []
        shares = split_over_angles(cap, len(combinations))
        for (topic, language), share in zip(combinations, shares, strict=True):
            want = topics_needed(share, templates[language])
            if want == 0:
                continue
            asked = with_niche(
                topic,
                await self._ask_angle(
                    angle, want + cfg.ANGLE_BUFFER, country, language, topic, seen, report
                ),
                templates[language],
            )
            topics = drop_near_duplicates(asked)
            report.near_duplicates += len(asked) - len(topics)
            report.per_angle[angle.title] = report.per_angle.get(angle.title, 0) + len(topics)
            keys, rejected = clean(expand(topics[:want], templates[language]))
            report.rejected.update(rejected)
            collected.extend(keys[:share])
            _count(report, topic=topic, language=language, got=len(keys[:share]))

        report.tokens = self._client.tokens_spent
        report.calls = self._client.calls
        report.refusals = list(self._client.refusals)
        return _settle(list(dict.fromkeys(collected))[:cap], cap=cap, report=report)

    async def _one(
        self,
        cap: int,
        angles: tuple[Angle, ...],
        country: str,
        language: str,
        topic: str,
        seen: set[str],
        report: PoolReport,
    ) -> list[str]:
        """Один проход по сочетанию «тема × язык»: углы и добор."""
        collected: list[str] = []
        for angle, want in zip(angles, split_over_angles(cap, len(angles)), strict=True):
            fresh = await self._ask_angle(angle, want, country, language, topic, seen, report)
            collected.extend(fresh)
            report.per_angle[angle.title] = report.per_angle.get(angle.title, 0) + len(fresh)
        return await self._backfill(collected, angles, cap, country, language, topic, seen, report)

    async def _ask_angle(
        self,
        angle: Angle,
        want: int,
        country: str,
        language: str,
        topic: str,
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
                    country=country, language=language, angle=angle, topic=topic
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
        topic: str,
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
                fresh = await self._ask_angle(
                    angle, per_angle, country, language, topic, seen, report
                )
                collected.extend(fresh)
                report.per_angle[angle.title] = report.per_angle.get(angle.title, 0) + len(fresh)
                if len(drop_near_duplicates(collected)) >= cap:
                    return collected

            if len(collected) == before:
                _log_dry(report, collected=collected, cap=cap)
                return collected

        return collected
