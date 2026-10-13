"""Добивки продаж тоже разные: тексты шагов 2–3 готовит сборка первого письма, каждый в своём
коридоре отличия.

ТЗ: «Цепочка из 3 писем в одной переписке… Формулировки ротируются, двух одинаковых писем не
уходит». Пока добивки продаж были только зонами `fixed`, их текст у всех лидов был один, кроме
подстановок.

**Текст — при сборке первого письма, а не при отправке.** В проходе добивок модели нет:
отправка по расписанию не ждёт модель и не падает от неё. Сборка лида (`queue._prepare`) —
когда первое письмо легло в коридор и сходится с подписью — готовит и тексты шагов с зонами
`rewrite`; шаг без них уходит текстом шаблона, как раньше: модели в нём нечего делать.

**Каждый шаг — свой коридор против своего шаблона** — тем же циклом, что первое письмо
(`corridor.Writer`): подбор принятых зон, зерно — номер лида и шаг (`corridor.seed`), проверки
по собранному тексту шага — метрики, коридор.

**Вызов модели — один на все добивки лида.** Зоны шагов уходят одним запросом с приставкой
шага в имени (`step2_intro`), ответ делится по приставке. Системный запрос переписывания —
сотни токенов на каждый вызов, а зоны добивки — десятки: один запрос на два шага стоит заметно
меньше двух. Доля слов в просьбе (`rewrite.change_share`) считается по добивкам вместе — у
коротких писем с близкой долей переписываемого она та же, а в коридор письмо кладёт подбор
зон. Шаг, ни один набор которого не лёг, зовёт модель заново сам — со своей подсказкой
(`corridor.hint`), до `SALES_REWRITE_ATTEMPTS` вызовов, как первое письмо. Первое письмо в
общий запрос не идёт: его запрос — тот, что замерен живьём, а не легло оно — лид ждёт, и
добивки модели не стоят.

**Не легла — шаблоном**: добивка уходит текстом шаблона, первое письмо из-за неё не держим;
счёт — в отчёте сборки («добивок шаблоном»), по письму — в его строке. **Потолок модели встал
посреди** — сборка встаёт словами потолка, как у первого письма, а лид ждёт следующей сборки
целиком (`CAPPED`): письмо в очереди с добивками без переписки сборка больше не тронула бы.

**Хранение — у переписки лида** (`sales_threads.followups`): текст шага, отличие, принятые зоны
и отличие каждой попытки — с версией цепочки, которой собраны (`Built.stored`). При отправке
добивки (`mail.followup`) берётся сохранённый текст, если версия цепочки та же; иначе — шаблон
шага нынешней цепочки, как раньше (`text_of`). Пересборка письма в очереди собирает и добивки
заново; после ухода первого письма сборка лида не трогает — тексты добивок не меняются.

**Нынешней цепочкой не собрать — сохранённым прежним текстом** (`choose`, решение владельца
13.10): цепочку поправили после сборки, в шаге голый `{{name}}`, а у лида имени нет. Добивка
уходит текстом, сохранённым при сборке письма прежней версией цепочки; нет и его — проверка
отправки её не пускает (`mail.check`), и проход добивок кончает цепочку с причиной словами.
До решения такая добивка откладывалась на час — каждый час, без конца и без тревоги.

**Видно там, где смотрят очередь перед отправкой** — на экране «Очередь писем», под строкой
письма сборки (`ahead`): добивки, которые ещё уйдут, — тем же правилом, что у отправки, и
словами, почему такой текст (`planned`).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import compose, guards
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.compose import Rendered
from backend.features.letters.rewrite import RewriteResult
from backend.features.letters.template import Zone, ZoneKind
from backend.features.letters.uniqueness import percent_text
from backend.features.sales import chain, corridor, letter, sender
from backend.features.sales.chain_text import FIRST_STEP, STEP_TITLES, STEPS, StepTemplate
from backend.features.sales.models import SalesLeadModel, SalesThreadModel

#: Почему лид ждёт, когда потолок модели встал на его добивках, — ключ сводки причин.
CAPPED = "потолок модели встал на добивках — письмо ждёт следующей сборки"

#: Заметка клиента модели о зоне шага в общем запросе: «зона «step2_intro» …».
_OF_STEP = re.compile(r"«step(\d+)_")


def _prefix(step: int) -> str:
    """Приставка шага в имени зоны общего запроса. Имена зон — латиница строчными и «_»
    (`template.HEADER_RE`): приставка с номером шага делит ответ однозначно."""
    return f"step{step}_"


@dataclass(frozen=True, slots=True)
class Written:
    """Текст добивки, собранный с первым письмом: строка отчёта и сам текст — зоны без подписи
    и адреса (их допишет отправка, `letter.signed`)."""

    line: corridor.FollowupLine
    body: str


@dataclass(frozen=True, slots=True)
class Built:
    """Добивки письма лида: тексты шагов с зонами `rewrite`, заметки — почему зоны остались
    шаблонными, и встал ли потолок модели посреди (`capped` — лид ждёт целиком)."""

    written: tuple[Written, ...] = ()
    notes: tuple[str, ...] = ()
    capped: bool = False

    @property
    def lines(self) -> tuple[corridor.FollowupLine, ...]:
        """Строки отчёта добивок — по порядку шагов."""
        return tuple(item.line for item in self.written)

    def stored(self, version: str) -> dict[str, Any] | None:
        """Тексты для переписки лида с версией цепочки; хранить нечего — `None`."""
        if not self.written:
            return None
        steps = {
            str(item.line.step): {
                "body": item.body,
                "difference": item.line.difference,
                "zones": list(item.line.zones),
                "differences": list(item.line.differences),
            }
            for item in self.written
        }
        return {"version": version, "steps": steps}


@dataclass(slots=True)
class _Cap:
    """Потолок модели до каждого следующего вызова — и встал ли он: тогда лид ждёт."""

    check: Callable[[], Awaitable[bool]]
    hit: bool = False

    async def __call__(self) -> bool:
        if await self.check():
            return True
        self.hit = True
        return False


async def build(writer: corridor.Writer, found: chain.Chain, values: Mapping[str, str]) -> Built:
    """Тексты добивок лида с зонами `rewrite`: один общий вызов модели, затем свой коридор у
    каждого шага. Шагов с такими зонами нет — модели не стоят (`Built()`)."""
    steps = [
        (number, letter.rendered(template, values))
        for number, template in sorted(found.steps.items())
        if number != FIRST_STEP and template.letter.of_kind(ZoneKind.REWRITE)
    ]
    if not steps:
        return Built()
    cap = _Cap(writer.within_cap)
    if not await cap():
        return Built(capped=True)
    together = await writer.rewriter.rewrite(_joined(steps), writer.about)
    writer.spend.add(together.tokens_spent)
    capped = replace(writer, within_cap=cap)
    written: list[Written] = []
    notes: list[str] = []
    for number, rendered in steps:
        tried = await capped.write(rendered, step=number, first=_part(together, number))
        if cap.hit:
            return Built(capped=True)
        written.append(_written(number, rendered, tried))
        notes += [f"{STEP_TITLES[number]}: {note}" for note in tried.notes]
    return Built(tuple(written), tuple(notes))


def _joined(steps: Sequence[tuple[int, Rendered]]) -> Rendered:
    """Добивки одним запросом: зоны всех шагов с приставкой шага. Модели уходят переписываемые,
    неизменные считаются в долю слов просьбы (`rewrite.change_share`)."""
    zones = tuple(
        Zone(f"{_prefix(number)}{zone.name}", zone.kind, zone.text)
        for number, rendered in steps
        for zone in rendered.zones
    )
    return Rendered(subject="", zones=zones)


def _part(together: RewriteResult, number: int) -> RewriteResult:
    """Доля общего ответа у шага: его зоны без приставки и его заметки. Токены уже в строке
    расхода письма — у доли их нет."""
    prefix = _prefix(number)
    zones = {
        name.removeprefix(prefix): text
        for name, text in together.zones.items()
        if name.startswith(prefix)
    }
    return RewriteResult(zones=zones, notes=list(_notes_of(together.notes, number)))


def _notes_of(notes: Sequence[str], number: int) -> Iterator[str]:
    """Заметки общего ответа для шага: о его зоне — без приставки, общие (ключа модели нет,
    отказ) — каждому шагу, о зоне другого шага — мимо."""
    prefix = _prefix(number)
    for note in notes:
        found = _OF_STEP.search(note)
        if found is None:
            yield note
        elif int(found.group(1)) == number:
            yield note.replace(f"«{prefix}", "«")


def _written(number: int, rendered: Rendered, tried: corridor.Tried) -> Written:
    """Добивка в коридоре — переписанный текст; не легла — текст шаблона, проверенный тем же
    правилом: метрики — по собранному тексту шага."""
    attempts = len(tried.differences)
    if tried.letter is not None:
        line = corridor.FollowupLine(
            number, attempts, tried.difference, tried.zones, tried.differences
        )
        return Written(line, tried.letter.body)
    body = compose.assemble(rendered, {}).body
    guards.assert_no_metrics(body)
    return Written(corridor.FollowupLine(number, attempts, None, (), tried.differences), body)


@dataclass(frozen=True, slots=True)
class Kept:
    """Добивка, сохранённая у переписки: текст, отличие (`None` — шаблоном), принятые зоны и
    отличие каждой попытки."""

    body: str
    difference: float | None
    zones: tuple[str, ...]
    differences: tuple[float, ...]


def kept(stored: Mapping[str, Any] | None, version: str) -> dict[int, Kept]:
    """Тексты добивок переписки, собранные этой версией цепочки: шаг шаблона → текст. Другая
    версия (цепочку правили после сборки) или переписка, собранная до переписки добивок, —
    пусто: добивки уйдут шаблоном шага."""
    if not stored or stored.get("version") != version:
        return {}
    return {
        int(step): Kept(
            str(item["body"]),
            item.get("difference"),
            tuple(item.get("zones", ())),
            tuple(item.get("differences", ())),
        )
        for step, item in dict(stored.get("steps", {})).items()
    }


def _template_text(template: StepTemplate, values: Mapping[str, str]) -> str:
    """Текст шага шаблоном: подстановки лида, зоны `rewrite` — текстом шаблона."""
    return compose.assemble(letter.rendered(template, values), {}).body


def text_of(
    stored: Mapping[str, Any] | None, found: chain.Chain, step: int, values: Mapping[str, str]
) -> str:
    """Текст добивки шага `step` (шаг шаблона) на отправку, без подписи и адреса: собранный при
    сборке этой же версией цепочки — или шаблон шага нынешней цепочки, как раньше."""
    item = kept(stored, found.version).get(step)
    return item.body if item is not None else _template_text(found.steps[step], values)


#: Почему текст добивки с подписью и адресом «Отправителя» не уйдёт — словами; `None` — уйдёт.
Check = Callable[[str], str | None]
#: Добивка, которую не собрать: так её называют проверка отправки и экран очереди.
NOT_BUILT = "не собрать ни нынешней цепочкой, ни сохранённым прежним текстом"


def unbuilt(found: sender.Sender) -> Check:
    """Проверка текста добивки тем же правилом, что у отправки (`letter.problem` над текстом с
    подписью и адресом). «Отправитель» не заполнен — не вопрос текста: добивка ждёт подключения
    продаж (`connection.check`), и текст тогда не судится."""
    if found.missing:
        return lambda _text: None
    return lambda text: letter.problem(letter.signed(text, found), found)


@dataclass(frozen=True, slots=True)
class Chosen:
    """Текст добивки на отправку — без подписи и адреса — и откуда он. `problem` — почему
    нынешней цепочкой не собрать (`None` — собрать); тогда текст — сохранённый прежний
    (`earlier`), а нет и его — нынешний как есть: проверка отправки его не пустит."""

    body: str
    problem: str | None = None
    earlier: bool = False


def choose(
    stored: Mapping[str, Any] | None,
    found: chain.Chain,
    step: int,
    values: Mapping[str, str],
    check: Check,
) -> Chosen:
    """Текст добивки шага `step` (шаг шаблона): `text_of`, а его не собрать — сохранённый при
    сборке письма прежней версией цепочки, если он уходит (решение владельца 13.10)."""
    body = text_of(stored, found, step, values)
    problem = check(body)
    if problem is None:
        return Chosen(body)
    earlier = _earlier(stored, step)
    if earlier is not None and earlier != body and check(earlier) is None:
        return Chosen(earlier, problem, earlier=True)
    return Chosen(body, problem)


def _earlier(stored: Mapping[str, Any] | None, step: int) -> str | None:
    """Сохранённый текст шага, какой версией цепочки ни собран; `None` — не хранится: шаг без
    зон `rewrite` или письмо собрано до переписки добивок."""
    item = dict((stored or {}).get("steps", {})).get(str(step))
    return None if not item else str(item["body"])


@dataclass(frozen=True, slots=True)
class Planned:
    """Добивка, какой она уйдёт: шаг шаблона, текст без подписи и адреса (`None` — шага в
    цепочке нет) и как он получен — словами; отличие и принятые зоны — у переписанной."""

    step: int
    body: str | None
    words: str
    difference: float | None = None
    zones: tuple[str, ...] = ()


def planned(
    stored: Mapping[str, Any] | None,
    found: chain.Chain,
    step: int,
    values: Mapping[str, str],
    check: Check,
) -> Planned:
    """Добивка шага `step` — тем же правилом, что у отправки (`choose`), и словами — почему
    такой текст: переписана при сборке, текст шаблона и почему, прежний текст — или не уйдёт."""
    template = found.steps.get(step)
    if template is None:
        return Planned(step, None, "шага нет в цепочке — добивка ждёт, пока цепочку дополнят")
    chosen = choose(stored, found, step, values, check)
    if chosen.earlier:
        words = f"сохранённым прежним текстом — нынешней цепочкой не собрать: {chosen.problem}"
        return Planned(step, chosen.body, words)
    if chosen.problem is not None:
        words = f"не уйдёт — {NOT_BUILT}: {chosen.problem}; проход добивок остановит цепочку"
        return Planned(step, None, words)
    item = kept(stored, found.version).get(step)
    if item is not None and item.difference is not None:
        words = f"переписана при сборке — отличие {percent_text(item.difference)}"
        return Planned(step, item.body, words, item.difference, item.zones)
    if item is not None:
        return Planned(step, item.body, corridor.template_reason(item.differences))
    rewrites = bool(template.letter.of_kind(ZoneKind.REWRITE))
    words = _template_words(stored, found.version, rewrites=rewrites)
    return Planned(step, _template_text(template, values), words)


def _template_words(stored: Mapping[str, Any] | None, version: str, *, rewrites: bool) -> str:
    """Почему добивка уйдёт текстом шаблона нынешней цепочки — словами."""
    if not rewrites:
        return "текст шаблона — зон rewrite в шаге нет"
    if stored and stored.get("version") != version:
        return "текст шаблона нынешней цепочки — цепочку правили после сборки письма"
    return "текст шаблона — письмо собрано, когда добивки ещё не переписывались"


async def ahead(
    session: AsyncSession, link: SalesThreadModel, lead: SalesLeadModel, host: str
) -> list[Planned]:
    """Добивки переписки лида, которые ещё уйдут, — какими они уйдут (`planned`). Добивка,
    письмо которой уже есть, — в переписке, а не впереди; у кончившейся цепочки (ответ,
    отписка, стоп-лист гасят срок) впереди ничего."""
    last = await session.scalar(
        select(MessageModel)
        .where(MessageModel.thread_id == link.thread_id, MessageModel.step < ANSWER_STEP)
        .order_by(MessageModel.step.desc())
        .limit(1)
    )
    if last is None or (last.status is not MessageStatus.QUEUED and last.next_action_at is None):
        return []
    current = await chain.of_set(session, link.chain_hypothesis_id, link.language)
    values, check = letter.values_of(lead, host), unbuilt(await sender.read(session))
    after = letter.template_step(last.step)
    return [planned(link.followups, current, step, values, check) for step in STEPS if step > after]
