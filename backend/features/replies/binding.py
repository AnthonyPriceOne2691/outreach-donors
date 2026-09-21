"""К какому нашему письму относится ответ.

Решение целиком описано в `docs/OUTREACH_THREADS.md`, здесь исполнение.
Коротко, почему путей два и почему они в таком порядке.

**Метка надёжнее.** Поле «куда отвечать» указывает на служебный адрес
с подписанной меткой, и метка едет в поле «кому», которое почтовые
клиенты сохраняют всегда. Ответ с любого адреса, даже написанный заново,
привязывается верно.

**Заголовки цепочки — запасной путь.** Большинство клиентов возвращает
идентификатор исходного письма, но большинство — не все: пересылка,
веб-интерфейсы и корпоративные шлюзы теряют их регулярно.

**Привязка по отправителю не делается вовсе.** Это самый очевидный
способ, и он ломается на первом же пересланном письме: адреса, с которого
пришёл ответ, в нашей базе нет, и письмо повисает без диалога. Донор
определяется по получателю — мы знаем, кому писали.

**Непривязанное не выбрасывается.** Ответ, который не удалось соотнести,
это не мусор, а потерянный донор: он сохраняется и виден отдельно.
Молча отброшенный ответ выглядит как «донор не ответил».
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from backend.features.letters import reply_to
from backend.features.replies.inbound import Incoming


class BindingWay(StrEnum):
    """Чем привязали. Нужно как число в отчёте, а не для ветвления.

    Доля привязок по заголовкам — это доля писем, где метка не сработала;
    если она растёт, ломается основной путь, и узнать об этом хочется
    раньше, чем по жалобе «донор не ответил».
    """

    LABEL = "label"  # подписанная метка в адресе «кому»
    HEADERS = "headers"  # идентификатор исходного письма в заголовках
    NONE = "none"  # не привязали


@dataclass(frozen=True, slots=True)
class Binding:
    """К какому письму относится ответ и как это выяснили."""

    message_id: int | None
    way: BindingWay

    @property
    def bound(self) -> bool:
        return self.message_id is not None


def by_label(incoming: Incoming, *, secret: str | None = None) -> int | None:
    """Номер нашего письма из подписанной метки в адресе «кому»."""
    for address in incoming.to:
        found = reply_to.message_id_from(address, secret=secret)
        if found is not None:
            return found
    return None


def thread_ids(incoming: Incoming) -> tuple[str, ...]:
    """Идентификаторы писем, на которые ссылается ответ.

    `In-Reply-To` вперёд `References`: первый называет письмо, на которое
    отвечают, второй — всю цепочку, и в ней может оказаться наше письмо
    трёхмесячной давности.
    """
    found: list[str] = []
    if incoming.in_reply_to:
        found.append(incoming.in_reply_to.strip())
    found.extend(ref.strip() for ref in reversed(incoming.references) if ref.strip())
    # Дубликаты убираем, порядок сохраняем: он и есть приоритет.
    seen: set[str] = set()
    unique: list[str] = []
    for reference in found:
        if reference not in seen:
            seen.add(reference)
            unique.append(reference)
    return tuple(unique)


def bind(
    incoming: Incoming,
    *,
    by_provider_id: Sequence[tuple[str, int]] = (),
    secret: str | None = None,
) -> Binding:
    """Привязать ответ к нашему письму.

    `by_provider_id` — то, что нашлось в базе по идентификаторам из
    заголовков: пары «идентификатор у почты → номер нашего письма».
    Ищет их вызывающий, потому что это запрос к базе, а правило — здесь.
    """
    labelled = by_label(incoming, secret=secret)
    if labelled is not None:
        return Binding(message_id=labelled, way=BindingWay.LABEL)

    known = dict(by_provider_id)
    for reference in thread_ids(incoming):
        if reference in known:
            return Binding(message_id=known[reference], way=BindingWay.HEADERS)

    return Binding(message_id=None, way=BindingWay.NONE)
