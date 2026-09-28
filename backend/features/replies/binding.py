"""К какому нашему письму относится ответ.

Решение целиком описано в `docs/OUTREACH_THREADS.md`, здесь исполнение.
Коротко, почему путей два и почему они в таком порядке.

**Метка надёжнее.** Поле «куда отвечать» указывает на служебный адрес
с подписанной меткой, и метка едет в поле «кому», которое почтовые
клиенты сохраняют всегда. Ответ с любого адреса, даже написанный заново,
привязывается верно.

**Заголовки цепочки — запасной путь.** Большинство клиентов возвращает
идентификатор исходного письма, но большинство — не все: пересылка,
веб-интерфейсы и корпоративные шлюзы теряют их регулярно. Идентификатор
этот — наш собственный `Message-ID` (`letters/identity.py`): номер письма
у платформы получатель не видит, и вернуть его в ответе ему нечем.

**Привязка по отправителю не делается вовсе.** Это самый очевидный
способ, и он ломается на первом же пересланном письме: адреса, с которого
пришёл ответ, в нашей базе нет, и письмо повисает без диалога. Донор
определяется по получателю — мы знаем, кому писали.

**Непривязанное не выбрасывается.** Ответ, который не удалось соотнести,
это не мусор, а потерянный донор: он сохраняется и виден отдельно
(`replies/unbound.py`, вкладка «Не привязаны» на экране диалогов).
Молча отброшенный ответ выглядит как «донор не ответил».

**Почему не привязали — решается здесь и хранится у ответа** (`Unbound`).
Пересчитать это потом по сохранённому нельзя и не нужно: заголовков
цепочки у ответа нет, а к минуте, когда смотрит человек, секрет могли
сменить, а письмо — удалить. Объяснение должно говорить о том, что
случилось при приёме, а не о том, что вышло бы сейчас.
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


class Unbound(StrEnum):
    """Почему ответ не привязан. Хранится у ответа (`replies.unbound_reason`).

    Четыре случая — четыре разных действия человека. Метки нет — донора
    ищут по отправителю и тексту. Подпись не сошлась — чинят секрет, пока
    так не встали все ответы подряд. Письма нет — это пробное письмо или
    удалённая рассылка. Цепочка чужая — ответили на пересланное.
    """

    #: Ни метки в адресе, ни заголовков цепочки: письмо написано заново.
    NO_LABEL = "no_label"
    #: Метки нет, а заголовки цепочки не совпали ни с одним нашим письмом.
    FOREIGN_THREAD = "foreign_thread"
    #: Метка в адресе есть, но подпись не сошлась.
    BAD_SIGNATURE = "bad_signature"
    #: Метка верная, а письма с таким номером нет (пробное письмо — №0).
    NO_SUCH_LETTER = "no_such_letter"


@dataclass(frozen=True, slots=True)
class Binding:
    """К какому письму относится ответ, как это выяснили — или почему нет."""

    message_id: int | None
    way: BindingWay
    #: Почему не привязали. Пусто у привязанного.
    unbound: Unbound | None = None

    @property
    def bound(self) -> bool:
        return self.message_id is not None


def no_such_letter() -> Binding:
    """Метка верная, но письма с её номером нет — ответ не привязан.

    Так бывает у пробного письма (номер 0 зарезервирован, `letters/probe.py`)
    и после чистки базы. Заголовки цепочки тут не спасают: они ссылаются на то
    же письмо, которого нет.
    """
    return Binding(message_id=None, way=BindingWay.NONE, unbound=Unbound.NO_SUCH_LETTER)


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
    by_message_id: Sequence[tuple[str, int]] = (),
    secret: str | None = None,
) -> Binding:
    """Привязать ответ к нашему письму.

    `by_message_id` — то, что нашлось в базе по идентификаторам из
    заголовков: пары «наш `Message-ID` → номер нашего письма». Совпадение
    точное, токен в токен. Ищет их вызывающий, потому что это запрос
    к базе, а правило — здесь.
    """
    labelled = by_label(incoming, secret=secret)
    if labelled is not None:
        return Binding(message_id=labelled, way=BindingWay.LABEL)

    known = dict(by_message_id)
    references = thread_ids(incoming)
    for reference in references:
        if reference in known:
            return Binding(message_id=known[reference], way=BindingWay.HEADERS)

    return Binding(message_id=None, way=BindingWay.NONE, unbound=_why_not(incoming, references))


def _why_not(incoming: Incoming, references: Sequence[str]) -> Unbound:
    """Почему не привязали ни меткой, ни заголовками.

    Метка, которая не прошла подпись, — первой: это самый громкий случай.
    Сменили секрет — и так встанет каждый следующий ответ, а выглядеть это
    будет как «метки нет», если не назвать отдельно.
    """
    if any(reply_to.labelled_number(address) is not None for address in incoming.to):
        return Unbound.BAD_SIGNATURE
    if references:
        return Unbound.FOREIGN_THREAD
    return Unbound.NO_LABEL
