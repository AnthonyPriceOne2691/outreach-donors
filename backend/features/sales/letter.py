"""Письмо продаж из шаблона цепочки — без базы: подстановки лида, подпись, сверка, ключ.

Общее у сборки очереди (`queue.py`) и добивки (`mail.py`): одно правило у двух путей.

**Подпись и физический адрес дописываются блоком настроек** — после текста шаблона, в
том порядке, в каком их показывает предпросмотр цепочки (`chain_text.preview`): шаблон
их не содержит (правило записи 4.6a), а отправитель правит их на экране без правки
шаблонов. Без адреса письмо не собирается: сборку и отправку не пускает проверка
подключения (`connection.py`).

**Сверка готового письма** (`problem`) — на сборке и ещё раз на отправке: блок настроек
стоит в конце письма и он нынешний; текст до него не повторяет подпись или адрес
(шаблон, записанный до того, как задали подпись, мог её содержать — заметка 4.6a);
подстановок без значения нет.

**Ответ лиду в переписке** (`answered`, `answer_problem`) — тот же блок настроек в конце:
ответ пишет человек или агент, а подпись и физический адрес дописываются при отправке.
Подпись в самом тексте ответа не отказ: строка с именем — обычное прощание, а не шаблон,
повторивший блок.

**Ключ идемпотентности продаж — с контактом**: `{этап}:{домен}:{адрес}:{шаг}`. Два лида
одной компании — два письма; повтор сборки или задачи тому же человеку — тот же ключ.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from backend.features.core.domain import Stage
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales.chain_text import StepTemplate, squashed
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.sender import WHERE, Sender

#: Ширина ключа письма в базе (`messages.idempotency_key`): длиннее — вставка упала бы
#: посреди сборки. Сверку с колонкой держит тест.
KEY_LENGTH = 320

#: Подстановка без значения: сырая `{{имя}}` или громкая метка сборки («… НЕ ЗАДАНО»).
_UNFILLED = re.compile(r"\{\{[^{}]*\}\}|«[^«»]*НЕ ЗАДАН[^«»]*»")


def template_step(step: int) -> int:
    """Шаг шаблона цепочки (1–3, `chain_text.STEPS`) для шага письма (с нуля,
    `messages.step`, как у доноров) — перевод в одном месте."""
    return step - FIRST_STEP + 1


def values_of(lead: SalesLeadModel, host: str) -> dict[str, str]:
    """Значения подстановок шаблона продаж: имя адресата, компания, сайт компании."""
    return {
        "name": (lead.name or "").strip(),
        "company": (lead.company or "").strip(),
        "site": host,
    }


def lacking(steps: Iterable[StepTemplate], values: Mapping[str, str]) -> list[str]:
    """Подстановки шагов цепочки, для которых у лида нет значения, — `{{имя}}` по порядку.

    Смотрятся все шаги, а не только первое письмо: добивка с подстановкой без значения
    застряла бы в переписке, которую уже начали."""
    used: set[str] = set()
    for step in steps:
        used |= step.letter.placeholders()
    return [f"{{{{{name}}}}}" for name in sorted(used) if not values.get(name, "").strip()]


def block(found: Sender) -> str:
    """Подпись и физический адрес из «Отправителя» — в этом порядке, через пустую строку."""
    parts = (found.values.get("signature"), found.values.get("physical_address"))
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def signed(body: str, found: Sender) -> str:
    """Текст шаблона и блок настроек: так письмо видит адресат."""
    return f"{body.rstrip()}\n\n{block(found)}"


def problem(body: str, found: Sender) -> str | None:
    """Что не так с готовым письмом продаж — словами; `None` — письмо цело."""
    if found.missing:
        return f"у отправителя {'; '.join(found.missing)} — {WHERE}"
    tail = block(found)
    if not body.endswith(f"\n\n{tail}"):
        return (
            "подписи и физического адреса из настроек отправителя в конце письма нет — "
            "письмо собрано с прежними: соберите очередь заново"
        )
    head = squashed(body[: -len(tail)])
    for name, words in (("signature", "подпись"), ("physical_address", "физический адрес")):
        if squashed(found.values.get(name) or "") in head:
            return (
                f"{words} из настроек отправителя стоит и в тексте письма — шаблон её "
                "повторяет: уберите из шаблона цепочки"
            )
    if (left := _UNFILLED.search(body)) is not None:
        return f"в письме подстановка без значения: {left.group(0)}"
    return None


def answered(body: str, found: Sender) -> str:
    """Ответ лиду, каким его увидит лид: текст и блок настроек. Блок уже в конце (ответ
    отправляют повторно) — второй раз не дописывается."""
    text = body.rstrip()
    return text if text.endswith(f"\n\n{block(found)}") else signed(text, found)


def answer_problem(body: str, found: Sender) -> str | None:
    """Что не так с ответом лиду — словами; `None` — ответ цел. Блок настроек в конце и
    нынешний, подстановок без значения нет; подпись в тексте — не отказ (см. выше)."""
    if found.missing:
        return f"у отправителя {'; '.join(found.missing)} — {WHERE}"
    if not body.endswith(f"\n\n{block(found)}"):
        return (
            "подписи и физического адреса из настроек отправителя в конце ответа нет — "
            "настройки сменились после того, как ответ записан: отправьте его заново"
        )
    if (left := _UNFILLED.search(body)) is not None:
        return f"в ответе подстановка без значения: {left.group(0)}"
    return None


def key(host: str, email: str, step: int) -> str:
    """Ключ идемпотентности письма продаж: этап, домен, адрес человека, шаг письма."""
    return f"{Stage.SALES.value}:{host}:{email.strip().lower()}:{step}"
