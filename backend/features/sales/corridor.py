"""Письмо продаж в коридоре отличия: подбор переписанных зон, запасной путь — повтор с подсказкой.

Пилот 11.10.2026: шаблон первого письма целиком `rewrite`. Три сборки подряд — ни одного
письма в очереди: «вне коридора отличия — ждёт следующей сборки», и каждая следующая
промахивалась так же, тратя модель впустую; измеренного числа нигде не было. Слово
владельца: письма выходят чёткими.

**Главный путь — подбор зон после одного вызова** (`letters/selection.select`, ядро общее с
донорами). Живые прогоны 11.10, по 20 писем на шаблон: просьбу «поменяй N% слов» при большой
переписываемой доле модель не исполняет — переписывает зоны почти целиком; у шаблона целиком
`rewrite` первый вызов не лёг в коридор ни разу (42–66%), у «вступление и оффер» — тоже
(39–49%). Повтор с подсказкой раскачивал письмо: после «слишком далеко» — под нижний край,
после «больше» — снова вверх (66 → 15 → 45). А подбор того, какие переписанные зоны принять,
а какие оставить текстом шаблона, клал письмо в коридор одним вызовом — 20 из 20 у обоих
шаблонов. Из наборов в коридоре берётся наибольший по числу зон, среди равных — выбор с зерном
от номера лида: письма разных лидов расходятся, а сборка того же лида повторима.

**Запасной путь — новый вызов с подсказкой**, когда ни один набор не в коридоре (`select` —
`None`): всё переписанное ниже нижнего края (модель почти не правила — «меняй больше слов»)
или выше верхнего даже одной зоной (одна огромная зона — «держись ближе к исходным словам»;
у одной зоны `rewrite` выбор только «ничего» или «всё»). Направление и число — письма со
всеми переписанными зонами: это то, что сделала модель. Попытка — заново из шаблона (модель
без памяти), на каждой — снова подбор; вызовов — до `SALES_REWRITE_ATTEMPTS`. Подсказка —
по-английски, как весь запрос (`letters/rewrite.build_payload`); доля слов в просьбе —
прежняя (`rewrite.change_share`).

**Проверки — те же и в том же порядке**, по итоговому тексту выбранного набора: зоны в письмо
(`compose.assemble`) → метрики (`guards.assert_no_metrics`) → коридор → подпись и адрес
(`queue._store`). Отличие — `difference()` собранного письма, а не сумма по зонам.

**Расход — одной строкой на письмо** (`LETTER_REWRITE`): токены всех вызовов, повторов тоже.
Строку заводит первая трата и дописывают следующие, поэтому потолок модели до каждого
следующего вызова (`queue._within_cap`) видит и трату этого письма: выбран посреди попыток —
сборка встаёт словами потолка, лид ждёт с числами сделанных попыток.

**Шаблон, а не модель, — сборка встаёт** (решение владельца 11.10): `STREAK` лидов подряд вне
коридора после всех попыток — следующие лиды потратили бы модель так же впустую. Серию
прерывает лид, чьё письмо встало в очередь; лиды, ждущие по другим причинам (нет цепочки,
подстановки, стоп-лист), её не трогают — они не про шаблон (`QueueReport.tally`).

Журнал — строка на попытку: номер лида, шаг, попытка, отличие письма, принятые зоны, вердикт
и заметки — почему зона осталась шаблонной. Адреса лида в нём нет. В итог письма идут заметки
одной попытки — той, что встала в очередь, а не легло — последней: заметка первой попытки
«зона X осталась шаблонной» врала бы о письме, где X переписана второй.

**Добивки (шаги 2–3) — тем же циклом** (`followup_texts.py`): свой коридор против своего
шаблона, зерно — номер лида и шаг (`seed`), расход — в ту же строку письма лида; первый
вызов у них общий на все добивки лида, и его ответ приходит готовым (`first`). Не легла —
добивка уходит текстом шаблона, а первое письмо встаёт в очередь (`FollowupLine.reason`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.core import usage
from backend.features.core.models.ops import UsageRecordModel
from backend.features.letters import compose, guards, selection
from backend.features.letters.rewrite import Personalization, RewriteClient, RewriteResult
from backend.features.letters.uniqueness import (
    below_corridor,
    corridor_verdict,
    difference,
    percent_text,
)
from backend.features.sales.chain_text import FIRST_STEP
from backend.features.sales.usage_cap import LETTER_REWRITE

logger = logging.getLogger(__name__)

#: Вердикт попытки в коридоре — словами журнала.
IN_CORRIDOR = "в коридоре"
#: Лидов подряд вне коридора после всех попыток, после которых сборка встаёт: шаблон первого
#: письма не даёт писем в коридоре, и следующие лиды потратили бы модель так же впустую.
STREAK = 3


@dataclass(frozen=True, slots=True)
class Tried:
    """Попытки модели над одним письмом.

    `letter` — письмо первой попытки, чей набор зон лёг в коридор; `None` — не вышло или
    потолок модели встал посреди попыток. `differences` — отличие письма каждой попытки по
    порядку (подобранного набора, а не лёг — всего переписанного), хотя бы одной: первую
    сборка делает всегда. `zones` — принятые зоны последней попытки; `notes` — почему зоны
    остались шаблонными, словами: той попытки, что встала в очередь, а не легло — последней."""

    letter: compose.Letter | None
    differences: tuple[float, ...]
    tokens: int
    zones: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def difference(self) -> float:
        """Отличие последней попытки — той, что встала в очередь, если встала."""
        return self.differences[-1]


@dataclass(frozen=True, slots=True)
class FollowupLine:
    """Добивка письма строкой отчёта: шаг шаблона (2 или 3), вызовов, отличие — или шаблоном.

    `difference` — отличие принятого текста; `None` — добивка уйдёт текстом шаблона: ни одна
    попытка не легла в коридор (`differences` — отличие каждой по порядку)."""

    step: int
    attempts: int
    difference: float | None
    #: Принятые переписанные зоны, по порядку шаблона; остальные — текст шаблона.
    zones: tuple[str, ...] = ()
    differences: tuple[float, ...] = ()

    @property
    def reason(self) -> str | None:
        """Почему добивка уйдёт шаблоном — с отличием каждой попытки; `None` — переписана."""
        return None if self.difference is not None else template_reason(self.differences)

    def as_dict(self) -> dict[str, object]:
        """Добивка строкой экрана: отличие долей и словами — или почему шаблоном."""
        shown = None if self.difference is None else percent_text(self.difference)
        return {
            "step": self.step,
            "attempts": self.attempts,
            "difference": self.difference,
            "difference_text": shown,
            "zones": list(self.zones),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LetterLine:
    """Письмо сборки строкой отчёта (`QueueReport.letters`): чьё, с каким отличием, какие
    зоны переписаны, за сколько вызовов модели и встало ли в очередь. Не встало — лид ждёт, и
    `reason` называет почему по этому письму: вне коридора — с отличием каждой попытки; в
    сводке причин отчёта (`waiting`) ключ — без чисел, одной строкой на все такие письма.
    `followups` — добивки с зонами `rewrite`, собранные с письмом: отличие или «шаблоном»."""

    lead_id: int
    #: Имя лида; нет — экран и консоль называют номер.
    name: str | None
    #: Вызовов модели: первый — всегда, остальные — запасной путь.
    attempts: int
    #: Отличие последней попытки — той, что встала в очередь, если встала: доля 0–1.
    difference: float
    #: Принятые переписанные зоны, по порядку шаблона; остальные — текст шаблона.
    zones: tuple[str, ...]
    queued: bool
    #: Почему письмо не встало в очередь — словами, по этому письму; `None` — встало.
    reason: str | None = None
    #: Добивки письма по порядку шагов; шаг без зон `rewrite` модели не стоит — его здесь нет.
    followups: tuple[FollowupLine, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Строка экрана: отличие и долей, и словами — тем же правилом у края коридора, что
        в причине и журнале (`percent_text`): экрану не нужен коридор, чтобы не соврать."""
        return {
            "lead_id": self.lead_id,
            "name": self.name,
            "attempts": self.attempts,
            "difference": self.difference,
            "difference_text": percent_text(self.difference),
            "zones": list(self.zones),
            "queued": self.queued,
            "reason": self.reason,
            "followups": [later.as_dict() for later in self.followups],
        }


def line(
    lead_id: int,
    name: str | None,
    tried: Tried,
    *,
    reason: str | None,
    followups: tuple[FollowupLine, ...] = (),
) -> LetterLine:
    """Строка отчёта о письме лида: вызовов столько, сколько сделано; отличие и зоны —
    последней попытки; `reason` — почему не встало в очередь, `None` — встало."""
    attempts = len(tried.differences)
    queued = reason is None
    return LetterLine(
        lead_id, name, attempts, tried.difference, tried.zones, queued, reason, followups
    )


def seed(lead_id: int, step: int) -> int:
    """Зерно подбора зон письма шага `step` (шаг шаблона, 1–3) у лида. Первое письмо — номер
    лида, как было; добивка — номер лида и шаг: у разных лидов наборы добивки расходятся, и у
    одного лида две добивки не повторяют друг друга набором."""
    return lead_id if step == FIRST_STEP else lead_id * 10 + step


def _target() -> str:
    """Коридор числами: «15–25%»."""
    low = round(outreach_cfg.UNIQUENESS_TARGET_MIN * 100)
    high = round(outreach_cfg.UNIQUENESS_TARGET_MAX * 100)
    return f"{low}–{high}%"


def hint(value: float) -> str:
    """Подсказка следующей попытке: куда промахнулась прошлая и на сколько.

    По-английски, как весь запрос переписки. Число — тем же правилом у края коридора, что
    в причине и журнале (`percent_text`): «25,4%» не встаёт на край «25%», — с точкой."""
    shown = percent_text(value).replace(",", ".")
    if below_corridor(value):
        return (
            "A previous rewrite of these zones stayed too close to the original: the email "
            f"differed from it by only {shown}, and the target is {_target()}. "
            "Change more words."
        )
    return (
        "A previous rewrite of these zones went too far: the email differed from the original "
        f"by {shown}, and the target is {_target()}. Stay closer to the original wording and "
        "change fewer words."
    )


def _attempts(count: int) -> str:
    """Число попыток после «после»: «1 попытки», «3 попыток», «21 попытки»."""
    word = "попытки" if count % 10 == 1 and count % 100 != 11 else "попыток"
    return f"{count} {word}"


def template_reason(differences: Sequence[float]) -> str:
    """Почему добивка уйдёт текстом шаблона — с отличием каждой попытки по порядку."""
    numbers = ", ".join(percent_text(value) for value in differences)
    tried = _attempts(len(differences))
    return f"не легла в коридор отличия после {tried}: {numbers} — уйдёт текстом шаблона"


def waiting_reason(differences: Sequence[float]) -> str:
    """Почему лид ждёт — по его письму, с отличием каждой попытки по порядку. В сводке причин
    отчёта ключ — без чисел (`queue.OFF_CORRIDOR`): сто таких лидов — одна строка."""
    numbers = ", ".join(percent_text(value) for value in differences)
    tried = _attempts(len(differences))
    return f"вне коридора отличия после {tried}: {numbers} — ждёт следующей сборки"


def _leads(count: int) -> str:
    """Число лидов словами: «1 лид», «3 лида», «5 лидов», «21 лид»."""
    if count % 100 in range(11, 15) or count % 10 in (0, *range(5, 10)):
        return f"{count} лидов"
    return f"{count} лид" if count % 10 == 1 else f"{count} лида"


def streak_stop() -> str:
    """Почему сборка встала после `STREAK` лидов подряд вне коридора — словами отчёта."""
    return (
        f"шаблон первого письма не даёт писем в коридоре отличия: {_leads(STREAK)} подряд — "
        "вне коридора после всех попыток; поправьте цепочку писем — совет в предупреждении у "
        "первого письма"
    )


def _dropped(chosen: selection.Selection | None) -> list[str]:
    """Переписанные зоны, которые подбор оставил текстом шаблона, — словами."""
    names = chosen.dropped if chosen is not None else ()
    return [f"зона «{name}» оставлена шаблонной подбором под коридор отличия" for name in names]


class Spend:
    """Расход письма одной строкой журнала расхода: первая трата заводит строку, следующие
    дописывают в неё. Сессия сбрасывает строку в базу до запроса потолка (autoflush), и
    потолок перед следующим вызовом видит и трату этого письма. Письмо — лида целиком: первое
    и его добивки (`followup_texts.py`) пишут в одну строку."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._entry: UsageRecordModel | None = None
        self.tokens = 0

    def add(self, tokens: int) -> None:
        if not tokens:
            return
        self.tokens += tokens
        if self._entry is None:
            self._entry = usage.record(self._session, operation=LETTER_REWRITE, units=tokens)
        else:
            self._entry.units = self.tokens


def _journal(
    lead_id: int,
    step: int,
    attempt: int,
    attempts: int,
    value: float,
    zones: tuple[str, ...],
    verdict: str | None,
    notes: list[str],
) -> None:
    """Строка журнала на попытку: лид — номером, без адреса; шаг, отличие письма, принятые
    зоны, вердикт и заметки — почему зона осталась шаблонной."""
    fields: dict[str, object] = {
        "lead_id": lead_id,
        "step": step,
        "attempt": attempt,
        "attempts": attempts,
        "difference": round(value, 4),
        "zones": list(zones),
        "verdict": verdict or IN_CORRIDOR,
    }
    if notes:
        fields["notes"] = notes
    logger.info("продажи: попытка письма", extra=fields)


@dataclass(frozen=True, slots=True)
class Writer:
    """Модель над письмами одного лида: клиент, о ком пишем, номер лида, сколько вызовов на
    письмо, потолок модели до каждого следующего вызова и расход одной строкой (`Spend`).
    Первое письмо и его добивки (`followup_texts.py`) пишутся одним писателем: строка расхода
    у них общая, и потолок перед каждым вызовом видит всё, что лид уже стоил."""

    rewriter: RewriteClient
    about: Personalization
    lead_id: int
    attempts: int
    within_cap: Callable[[], Awaitable[bool]]
    spend: Spend

    async def write(
        self,
        rendered: compose.Rendered,
        *,
        step: int = FIRST_STEP,
        first: RewriteResult | None = None,
    ) -> Tried:
        """Письмо шага `step` (шаг шаблона) в коридоре отличия: вызов модели и подбор зон
        (`selection.select`, зерно — номер лида и шаг, `seed`); ни один набор не лёг — новый
        вызов с подсказкой, до `attempts` вызовов, каждый из шаблона. Не лёг — письмо со всеми
        переписанными зонами: его отличие — в причину и в подсказку.

        `within_cap` — потолок модели до каждого следующего вызова: первый проверила сборка.
        `first` — ответ модели, уже оплаченный в `spend`, на месте первого вызова: общий запрос
        добивок лида (`followup_texts.py`)."""
        differences: list[float] = []
        said: list[str] = []
        before = self.spend.tokens
        told: str | None = None
        zones: tuple[str, ...] = ()
        for attempt in range(1, self.attempts + 1):
            if attempt > 1 and not await self.within_cap():
                break
            rewritten = await self._rewritten(rendered, told, first if attempt == 1 else None)
            chosen = selection.select(rendered, rewritten.zones, seed=seed(self.lead_id, step))
            taken = chosen.zones if chosen is not None else rewritten.zones
            letter = compose.assemble(rendered, taken)
            guards.assert_no_metrics(f"{letter.subject}\n{letter.body}")
            value = difference(letter.plain_body, letter.body)
            differences.append(value)
            zones = tuple(zone.name for zone in rendered.rewritable() if zone.name in taken)
            verdict = corridor_verdict(value)
            said = [*rewritten.notes, *_dropped(chosen)]
            _journal(self.lead_id, step, attempt, self.attempts, value, zones, verdict, said)
            if verdict is None:
                spent = self.spend.tokens - before
                return Tried(letter, tuple(differences), spent, zones, tuple(said))
            told = hint(value)
        return Tried(None, tuple(differences), self.spend.tokens - before, zones, tuple(said))

    async def _rewritten(
        self, rendered: compose.Rendered, told: str | None, first: RewriteResult | None
    ) -> RewriteResult:
        """Ответ модели на попытку: готовый `first` — или новый вызов, его трата — в строку."""
        if first is not None:
            return first
        rewritten = await self.rewriter.rewrite(rendered, self.about, hint=told)
        self.spend.add(rewritten.tokens_spent)
        return rewritten
