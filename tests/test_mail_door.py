"""Одна дверь к почте и известные писатели писем — скан кода сервиса.

Правила отправки — стоп-лист, «одна роль на сайт», решение человека, окно получателя, лимиты
ящиков, «отправляется» в базе до вызова платформы — проверяются в одном месте: `Sending.send`
(`letters/sending.py`). Кроме неё почте пишет только пробное письмо себе (`outreach mail-test`,
`letters/probe.py`). Держалось это дисциплиной: ничто не мешало новому модулю собрать письмо почте
и отдать транспорту мимо проверок. Теперь мешает скан — `backend/` целиком, без `migrations` и
`__pycache__`.

**Дверь — три замка.** Письмо почте (`Outgoing` — единственное, что принимает транспорт) вне
аннотаций типов называют только `sending.py` и `probe.py`; пробное письмо (`compose_probe`) берёт
только `cli/mail_test.py`; `send` транспорта зовут только `sending.py` и `cli/mail_test.py`.
Имена — эти и ниже (`Sending`, `MessageModel`) — узнаются и под другим: псевдонимом импорта
(`import … as`), присваиванием (`Letter = MessageModel`) и атрибутом модуля (`transport.Outgoing`).

**`send` транспорта узнаётся не по имени переменной.** Имя (`transport`, `courier`, `self._mail`)
ничего не держит — обход под другим именем прошёл бы. Вызов `.send` — вызов транспорта, если
(1) он ложится на подпись `Transport.send`: это решает `inspect.signature(…).bind` по числу и
именам доводов, и Telegram `bot.send(чат, текст)`, обход `progress.send(ссылки, отметка)` и
`Sending.send(…, author_id=…)` отпадают сами; и (2) получатель — не `Sending`: тот собран вызовом
`Sending(…)` в этой функции или объемлющей, или назван этим типом в аннотации параметра или
переменной (и в объединении `Sending | None`) — аннотацию держит mypy (`strict`). Так
`Sending(…).send(id)` и `postman.send(id)` — дверь, а переменная `sending` без типа — нет. Вызов
через класс (`SendGridTransport.send(транспорт, письмо)`; класс — имя с заглавной, не капсом) —
тоже вызов транспорта; ссылка без вызова (`deliver = transport.send`) считается вызовом: куда её
отдадут, скан не видит. `Sending`, названный иначе (атрибутом `self._sending`), даст красное —
ложное, но громкое: назвать его в функции, как выше.

**Писатели писем.** Строки `messages` создают (`MessageModel(…)`, `insert`, `bulk_insert_mappings`),
правят (`update`, `bulk_update_mappings`) и удаляют (`delete`) — моделью или её таблицей
(`MessageModel.__table__`), под любым её именем — только модули списков ниже. Списки — нынешнее
состояние, как исключения `.importlinter`: новый модуль — красное с подсказкой, строка списка,
которой в коде больше нет, — тоже красное: список только сжимается. Удаление — своим списком: с
письмом уходит его ключ (`building.idempotency_key`), и для сборки домену как будто не писали.

**Чего скан не видит.** Присваивания полей загруженной строки (`message.status = …`,
`letter.thread_id = None`) и `session.delete(строка)`: тип строки по коду не прочесть. Сырого SQL
(`text("UPDATE messages …")`, `table("messages")`). Записи через функции другого модуля: в списке —
модуль, где стоит запрос, а не те, кто его зовёт (`settle.leave` зовут многие). Нового места в
модуле, который уже в списке: судится модуль, как у `.importlinter`, а не строка. Каскада удаления
родителя (`campaigns`, `threads` — `ON DELETE CASCADE`). Динамики (`getattr(t, "send")`,
`type(x)(…)`, `dataclasses.replace` готового письма) и своего HTTP к почтовой платформе мимо
транспорта.

Обратный прогон — файлом (правило из `tests/test_sales_boundary.py`): нарушение подкладывается в
копию `backend/` во временной папке, и та же проверка краснеет понятным словом; копия без
подложенного — зелёная.
"""

from __future__ import annotations

import ast
import inspect
import shutil
from collections.abc import Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest
from backend.features.letters.transport import Transport

ROOT = Path(__file__).resolve().parent.parent

#: Где собирают письмо почте (`Outgoing`).
ENVELOPE = {
    "backend/features/letters/sending.py": "письмо очереди — после всех проверок отправки и "
    "«отправляется» в базе (`Sending._hand_over`)",
    "backend/features/letters/probe.py": "пробное письмо себе: номер письма 0, строки в базе нет",
}
#: Кто берёт пробное письмо (`compose_probe`).
PROBE = {
    "backend/cli/mail_test.py": "`outreach mail-test` — письмо себе до первого донора, база "
    "только на чтение",
}
#: Кто зовёт `send` транспорта.
TRANSPORT = {
    "backend/features/letters/sending.py": "`Sending._hand_over` — после стоп-листа, роли сайта, "
    "решения человека, окна, ящика и захвата",
    "backend/cli/mail_test.py": "пробное письмо себе; предохранитель учётки этапа — в самом "
    "транспорте",
}
#: Кто создаёт строки `messages`.
CREATE = {
    "backend/features/letters/building.py": "первые письма доноров и рекламодателей — сборка очереди",
    "backend/features/letters/followups.py": "добивка — строкой в минуту, когда подошёл её срок",
    "backend/features/letters/answers.py": "наш ответ в переписке",
    "backend/features/letters/price_recheck.py": "перезапрос цены донору — шагом переписки или "
    "первым письмом",
    "backend/features/sales/queue.py": "первые письма продаж — сборка очереди продаж",
    "backend/cli/demo_data.py": "`demo-seed` — выдуманная переписка для экранов",
}
#: Кто правит строки `messages` (`update`).
UPDATE = {
    "backend/features/letters/sending.py": "захват «отправляется» (`Sending._claim`)",
    "backend/features/letters/settle.py": "выход письма из состояния (`settle.leave`): ушло, "
    "исход, остановка",
    "backend/features/letters/followups.py": "срок добивки: захват, возврат, конец цепочки",
    "backend/features/letters/unknown_outcome.py": "ушедшая добивка гасит срок повтора предыдущего "
    "письма",
    "backend/features/crawl/niche.py": "письма бизнесу ниши — из очереди в «остановлено»",
    "backend/features/sales/queue.py": "письмо продаж, собранное заново на том же месте",
}
#: Кто удаляет строки `messages` (`delete`): с письмом уходит его ключ.
DELETE = {
    "backend/features/donors/probe.py": "`prune --probes` — письма липовых доноров вместе с доменом",
    "backend/features/outreach/own_inboxes.py": "`prune --test-traces` — письма своему ящику на "
    "настоящем домене: ключ домена освобождается намеренно",
    "backend/features/sales/trials.py": "`prune --test-traces` — проба продаж на свой ящик",
    "backend/cli/demo_data.py": "`demo-seed --clear` — выдуманные рассылки",
}

#: Подсказка новому писателю писем.
WRITER = "писать через функции модуля-владельца или вписать модуль в список с причиной"
#: Правило → что делается словами, его список и что делать новому месту.
RULES: dict[str, tuple[str, Mapping[str, str], str]] = {
    "outgoing": (
        "собирает или называет письмо почте (`Outgoing`)",
        ENVELOPE,
        "письмо почте собирает только дверь — `Sending(session, mail).send(id)`, пробное себе — "
        "`probe.compose_probe`",
    ),
    "probe": (
        "берёт пробное письмо (`compose_probe`)",
        PROBE,
        "пробное письмо себе — только `outreach mail-test`",
    ),
    "send": (
        "зовёт `send` транспорта",
        TRANSPORT,
        "письмо уходит только через `Sending(session, mail).send(id)`: там стоп-лист, роль сайта, "
        "решение человека, окно и лимиты ящиков; если получатель — `Sending`, собрать его в этой "
        "функции или назвать тип аннотацией `Sending`",
    ),
    "create": ("создаёт строки `messages`", CREATE, WRITER),
    "update": ("правит строки `messages`", UPDATE, WRITER),
    "delete": (
        "удаляет строки `messages`",
        DELETE,
        f"{WRITER}; с письмом уходит его ключ — для сборки домену как будто не писали",
    ),
}

#: Имена, которые скан узнаёт и под другим именем.
NAMED = frozenset({"Outgoing", "compose_probe", "MessageModel", "Sending"})
#: Имена, которые вне аннотаций уже сами — место правила.
REFERENCED = {"Outgoing": "outgoing", "compose_probe": "probe"}
#: Конструкция SQLAlchemy или метод сессии → правило записи строк.
STATEMENTS = {
    "insert": "create",
    "bulk_insert_mappings": "create",
    "update": "update",
    "bulk_update_mappings": "update",
    "delete": "delete",
}
#: Без этих слов в тексте модуля искать нечего: обход под любым именем называет исходное в импорте.
WORDS = ("Outgoing", "compose_probe", "MessageModel", "send")
#: Подпись `send` транспорта — на неё примеряется каждый вызов `.send`.
TRANSPORT_SEND = inspect.signature(Transport.send)


#: Свои области имён: в них `_own` не спускается.
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own(scope: ast.AST) -> Iterator[ast.AST]:
    """Узлы области без тел вложенных функций и классов: у тех свои имена."""
    for child in ast.iter_child_nodes(scope):
        yield child
        if not isinstance(child, SCOPES):
            yield from _own(child)


def _name(node: ast.AST, names: Mapping[str, str]) -> str:
    """Что называет узел: имя (исходное — за другим именем `names`) или атрибут; иначе пусто."""
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    return node.attr if isinstance(node, ast.Attribute) else ""


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Другое имя → исходное: псевдоним импорта (`from … import Outgoing as Envelope`) и
    присваивание (`Letter = MessageModel`). Конструкции записи — только из SQLAlchemy
    (`from sqlalchemy.dialects.postgresql import insert as upsert`)."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            sql = (node.module or "").startswith("sqlalchemy")
            names.update(
                (alias.asname or alias.name, alias.name)
                for alias in node.names
                if alias.name in NAMED or (sql and alias.name in STATEMENTS)
            )
    for node in ast.walk(tree):  # после импортов: присваивают и псевдоним импорта
        if isinstance(node, ast.Assign) and (source := _name(node.value, names)) in NAMED:
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            names.update(dict.fromkeys(targets, source))
    return names


def _fits(call: ast.Call, *, through_class: bool) -> bool:
    """Вызов ложится на подпись `send` транспорта — по числу и именам доводов, как решил бы сам
    Python. Распаковку (`*доводы`, `**слова`) не прочесть — она считается подходящей. Отвечает
    `bind` только исключением: `TypeError` здесь — ответ «не ложится», а не сбой."""
    if any(isinstance(arg, ast.Starred) for arg in call.args):
        return True
    if any(word.arg is None for word in call.keywords):
        return True
    owner = () if through_class else (None,)  # через класс экземпляр — первый довод
    words = {str(word.arg): word.value for word in call.keywords}
    with suppress(TypeError):
        TRANSPORT_SEND.bind(*owner, *call.args, **words)
        return True
    return False


def _a_class(node: ast.expr) -> bool:
    """Получатель — класс (`SendGridTransport.send(…)`): имя с заглавной и не капсом."""
    name = _name(node, {})
    return name[:1].isupper() and not name.isupper()


class _Module(ast.NodeVisitor):
    """Места правил в одном модуле: `found` — (правило, строка)."""

    def __init__(self, tree: ast.Module) -> None:
        self.names = _aliases(tree)
        self.found: list[tuple[str, int]] = []
        #: Имена с типом `Sending` — по области: от модуля до нынешней функции.
        self._scopes = [self._typed(tree)]
        #: `.send`, которые вызваны: ссылка без вызова судится отдельно.
        self._called: set[int] = set()
        self.visit(tree)

    # --- что как называется ---

    def _is(self, node: ast.AST, name: str) -> bool:
        """`node` называет `name` — своим именем, другим именем или атрибутом модуля."""
        return _name(node, self.names) == name

    def _sending_call(self, node: ast.AST | None) -> bool:
        return isinstance(node, ast.Call) and self._is(node.func, "Sending")

    def _sending_type(self, node: ast.expr | None) -> bool:
        """Аннотация — `Sending`: именем, атрибутом модуля, строкой или в объединении
        (`Sending | None`: до `.send` mypy потребует сузить тип)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self._sending_type(ast.parse(node.value, mode="eval").body)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._sending_type(node.left) or self._sending_type(node.right)
        return node is not None and self._is(node, "Sending")

    def _typed(self, scope: ast.AST) -> set[str]:
        """Имена области с типом `Sending`: параметры с аннотацией и связанные в теле."""
        typed = {name for node in _own(scope) for name in self._binds(node)}
        if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
            given = scope.args
            params = (*given.posonlyargs, *given.args, *given.kwonlyargs, given.vararg, given.kwarg)
            typed.update(
                arg.arg for arg in params if arg is not None and self._sending_type(arg.annotation)
            )
        return typed

    def _binds(self, node: ast.AST) -> list[str]:
        """Имена, которые узел связывает с `Sending`: `= Sending(…)`, `:= Sending(…)`, `: Sending`."""
        if isinstance(node, ast.Assign) and self._sending_call(node.value):
            return [target.id for target in node.targets if isinstance(target, ast.Name)]
        if isinstance(node, ast.NamedExpr) and self._sending_call(node.value):
            return [node.target.id]
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return [node.target.id] if self._sending_type(node.annotation) else []
        return []

    def _door(self, receiver: ast.expr) -> bool:
        """Получатель `.send` — отправка `Sending`: собрана тут же, названа типом или сам класс."""
        if isinstance(receiver, ast.NamedExpr):  # `(postman := Sending(…)).send(id)`
            receiver = receiver.value
        if isinstance(receiver, ast.Name) and any(receiver.id in names for names in self._scopes):
            return True
        return self._sending_call(receiver) or self._is(receiver, "Sending")

    def _model(self, node: ast.AST) -> bool:
        """`MessageModel` или его таблица (`MessageModel.__table__`)."""
        return self._is(node, "MessageModel") or self._table(node)

    def _table(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "__table__"
            and self._is(node.value, "MessageModel")
        )

    def _writes(self, call: ast.Call) -> Iterator[str]:
        """Запись строк `messages` этим вызовом — правилом."""
        func = call.func
        if self._is(func, "MessageModel"):
            yield "create"
            return
        rule = STATEMENTS.get(_name(func, self.names))
        if rule is None:
            return
        # Цель — первым доводом (`insert(MessageModel)`, `update(MessageModel.__table__)`)
        # или сама таблица методом (`MessageModel.__table__.delete()`).
        given = bool(call.args) and self._model(call.args[0])
        if given or (isinstance(func, ast.Attribute) and self._table(func.value)):
            yield rule

    # --- обход ---

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Тело — своей областью имён; аннотации параметров и возврата письма не собирают."""
        self._scopes.append(self._typed(node))
        defaults = [*node.args.defaults, *(item for item in node.args.kw_defaults if item)]
        for item in (*node.decorator_list, *defaults, *node.body):
            self.visit(item)
        self._scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Аннотация — тип, а не письмо: судятся цель и значение."""
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        """Псевдоним типа (`type X = …`) — тоже тип, а не письмо."""

    def visit_Call(self, node: ast.Call) -> None:
        self.found.extend((rule, node.lineno) for rule in self._writes(node))
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "send":
            self._called.add(id(func))
            through_class = _a_class(func.value)
            if not self._door(func.value) and _fits(node, through_class=through_class):
                self.found.append(("send", node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and node.attr in REFERENCED:
            self.found.append((REFERENCED[node.attr], node.lineno))
        if self._handed(node):
            self.found.append(("send", node.lineno))
        self.generic_visit(node)

    def _handed(self, node: ast.Attribute) -> bool:
        """`.send` без вызова — ссылку отдают дальше, и куда, скан не видит; у `Sending` — дверь."""
        return (
            isinstance(node.ctx, ast.Load)
            and node.attr == "send"
            and id(node) not in self._called
            and not self._door(node.value)
        )

    def visit_Name(self, node: ast.Name) -> None:
        name = _name(node, self.names)
        if isinstance(node.ctx, ast.Load) and name in REFERENCED:
            self.found.append((REFERENCED[name], node.lineno))


@cache
def _places(source: str) -> tuple[tuple[str, int], ...]:
    """Места правил в тексте модуля. Чистая функция текста: копии дерева в обратных прогонах
    читают те же тексты, и разбирать каждый второй раз незачем."""
    if not any(word in source for word in WORDS):
        return ()
    return tuple(_Module(ast.parse(source)).found)


@dataclass(slots=True)
class Seen:
    """Что нашёл скан: правило → модуль → строки; и сколько файлов прочитано."""

    places: dict[str, dict[str, list[int]]] = field(default_factory=dict)
    files: int = 0


def scan(backend: Path) -> Seen:
    """Места правил во всём `backend/`, кроме `migrations` и `__pycache__`. Путь — от каталога над
    `backend/`, как в списках."""
    seen = Seen()
    for path in sorted(backend.rglob("*.py")):
        if {"migrations", "__pycache__"} & set(path.relative_to(backend).parts):
            continue
        seen.files += 1
        where = path.relative_to(backend.parent).as_posix()
        for rule, line in _places(path.read_text(encoding="utf-8")):
            seen.places.setdefault(rule, {}).setdefault(where, []).append(line)
    return seen


def judge(seen: Seen) -> list[str]:
    """Расхождения со списками — строкой на каждое: новое место и строка списка без места."""
    told: list[str] = []
    for rule, (what, allowed, hint) in RULES.items():
        places = seen.places.get(rule, {})
        for path in sorted(places.keys() - allowed.keys()):
            lines = ", ".join(map(str, places[path]))
            told.append(f"{path}:{lines} {what} — новое место: {hint}")
        for path in sorted(allowed.keys() - places.keys()):
            told.append(
                f"{path} в списке «{what}», а в коде этого нет — вычеркнуть строку: "
                "список только сжимается"
            )
    return told


# --- дерево репозитория -----------------------------------------------------------------------


def test_the_door_and_the_writers_of_letters_are_the_listed_ones() -> None:
    """Места правил в коде — ровно списки: нового места нет, и строки списков все на месте."""
    seen = scan(ROOT / "backend")
    told = judge(seen)

    assert seen.files > 500, f"скан прочёл {seen.files} файлов — путь к backend/ сменился?"
    assert not told, "\n".join(told)


def test_every_listed_module_says_why_in_a_few_words() -> None:
    reasons = [reason for _, allowed, _ in RULES.values() for reason in allowed.values()]

    assert [reason for reason in reasons if not 0 < len(reason.strip()) <= 120] == []


# --- обратный прогон файлом -------------------------------------------------------------------

#: Куда в копии подкладывается модуль: в почту, рядом с чисткой.
PLANTED = "features/outreach/planted.py"

#: Обходы двери и новые писатели писем — каждый своим модулем; правило, которое его ловит.
BYPASSES = {
    "письмо под псевдонимом импорта": (
        "outgoing",
        "from backend.features.letters.transport import Outgoing as Envelope\n\n\n"
        "def letter(**fields):\n"
        "    return Envelope(**fields)\n",
    ),
    "письмо атрибутом модуля": (
        "outgoing",
        "from backend.features.letters import transport\n\n\n"
        "def letter(**fields):\n"
        "    return transport.Outgoing(**fields)\n",
    ),
    "заготовка письма без вызова": (
        "outgoing",
        "from functools import partial\n\n"
        "from backend.features.letters.transport import Outgoing\n\n"
        'TO_SELF = partial(Outgoing, to="me@ours.example.test")\n',
    ),
    "пробное письмо из другого модуля": (
        "probe",
        "from backend.features.letters.probe import compose_probe as probe_letter\n\n\n"
        "def letter(stage):\n"
        '    return probe_letter(to="me@ours.example.test", sender_email="box@mail.example.test",'
        " stage=stage, real=False)\n",
    ),
    "транспорт под другим именем": (
        "send",
        "from backend.features.letters.transport import of_stage\n\n\n"
        "async def deliver(mail, letter):\n"
        '    courier = of_stage(mail, "donors")\n'
        "    return await courier.send(letter)\n",
    ),
    "переменная sending без типа": (
        "send",
        "async def deliver(sending, letter):\n    return await sending.send(letter)\n",
    ),
    "транспорт словом outgoing": (
        "send",
        "async def deliver(mail, letter):\n"
        '    return await mail.for_stage("donors").send(outgoing=letter)\n',
    ),
    "транспорт через класс": (
        "send",
        "from backend.features.letters.sendgrid import SendGridTransport\n\n\n"
        "async def deliver(transport, letter):\n"
        "    return await SendGridTransport.send(transport, letter)\n",
    ),
    "транспорт распаковкой": (
        "send",
        "async def deliver(transport, packed):\n    return await transport.send(**packed)\n",
    ),
    "send транспорта ссылкой без вызова": (
        "send",
        "def courier(transport):\n    return transport.send\n",
    ),
    "строка письма под псевдонимом": (
        "create",
        "from backend.features.core.models import MessageModel as Letter\n\n\n"
        "def add(session):\n"
        "    session.add(Letter(step=0))\n",
    ),
    "вставка в таблицу писем": (
        "create",
        "from sqlalchemy.dialects.postgresql import insert as upsert\n\n"
        "from backend.features.core.models.outreach import MessageModel\n\n"
        "STATEMENT = upsert(MessageModel.__table__).on_conflict_do_nothing()\n",
    ),
    "строка письма присвоенным именем": (
        "create",
        "from backend.features.core.models import outreach\n\n"
        "Letter = outreach.MessageModel\n\n\n"
        "def add(session):\n"
        "    session.add(Letter(step=0))\n",
    ),
    "массовая вставка": (
        "create",
        "from backend.features.core.models.outreach import MessageModel\n\n\n"
        "def add(session, rows):\n"
        "    session.bulk_insert_mappings(MessageModel, rows)\n",
    ),
    "правка атрибутом модуля sqlalchemy": (
        "update",
        "import sqlalchemy as sa\n\n"
        "from backend.features.core.models.outreach import MessageModel\n\n"
        'STATEMENT = sa.update(MessageModel).values(status="stopped")\n',
    ),
    "удаление под псевдонимом": (
        "delete",
        "from sqlalchemy import delete as remove\n\n"
        "from backend.features.core.models.outreach import MessageModel\n\n"
        "STATEMENT = remove(MessageModel).where(MessageModel.step == 0)\n",
    ),
    "удаление методом таблицы": (
        "delete",
        "from backend.features.core.models.outreach import MessageModel\n\n"
        "STATEMENT = MessageModel.__table__.delete()\n",
    ),
}

#: Дверь и чужие `send` — красного не дают: отправка `Sending` в любом своём виде, Telegram,
#: проход обхода, письмо почте в аннотациях.
DOORS = """\
from backend.features.letters import sending as door
from backend.features.letters.sending import Sending
from backend.features.letters.transport import Outgoing


async def button(session, mail, letter_id: int) -> None:
    await Sending(session, mail).send(letter_id)


async def chain(session, mail, letter_id: int) -> None:
    postman = door.Sending(session, mail)
    await postman.send(letter_id)


async def again(session, mail, letter_id: int) -> None:
    await (postman := Sending(session, mail)).send(letter_id)
    await postman.send(letter_id)


async def one(sending: Sending, letter_id: int) -> None:
    await sending.send(letter_id)


async def quoted(sending: "door.Sending", letter_id: int) -> None:
    await sending.send(letter_id)


async def maybe(sending: Sending | None, letter_id: int) -> None:
    if sending is not None:
        await sending.send(letter_id)


async def batch(session, mail, ids: list[int]) -> None:
    postman: Sending = Sending(session, mail)

    async def each(letter_id: int) -> None:
        await postman.send(letter_id)

    for letter_id in ids:
        await each(letter_id)


async def answer(anyone, letter_id: int) -> None:
    await anyone.send(letter_id, from_sender_id=1, in_reply_to=None)


async def telegram(bot, chat: str, text: str) -> None:
    await bot.send(chat, text)


async def walk(progress, links, checkpoint) -> None:
    await progress.send(links, checkpoint)


def handed(outgoing: Outgoing) -> Outgoing:
    kept: Outgoing = outgoing
    return kept


type Letters = list[Outgoing]
"""


def _copy(tmp_path: Path, planted: str | None = None) -> Path:
    """Копия `backend/` во временной папке; `planted` — текст подложенного модуля `PLANTED`."""
    backend = tmp_path / "backend"
    shutil.copytree(ROOT / "backend", backend, ignore=shutil.ignore_patterns("__pycache__"))
    if planted is not None:
        (backend / PLANTED).write_text(planted, encoding="utf-8")
    return backend


@pytest.mark.parametrize("case", BYPASSES)
def test_a_bypass_planted_in_a_copy_turns_the_scan_red_in_words(tmp_path: Path, case: str) -> None:
    rule, source = BYPASSES[case]
    what, _, hint = RULES[rule]

    told = judge(scan(_copy(tmp_path, source)))

    assert len(told) == 1, told
    assert told[0].startswith(f"backend/{PLANTED}:"), told
    assert told[0].endswith(f" {what} — новое место: {hint}"), told


def test_the_door_and_foreign_sends_planted_in_a_copy_stay_green(tmp_path: Path) -> None:
    """Не по имени переменной: дверь в любом своём виде и чужие `send` скан пропускает. Модуль
    в копии прочитан — файлов на один больше, — а обходы в том же месте тот же скан ловит."""
    seen = scan(_copy(tmp_path, DOORS))

    assert judge(seen) == []
    assert seen.files == scan(ROOT / "backend").files + 1


def test_a_listed_module_that_stopped_writing_turns_the_scan_red(tmp_path: Path) -> None:
    """Список только сжимается: в копии модуль из списков больше не пишет — его строки без места
    в коде краснеют, по одной на каждый список."""
    backend = _copy(tmp_path)
    (backend / "cli/demo_data.py").write_text('"""Выдуманное убрано."""\n', encoding="utf-8")

    told = judge(scan(backend))

    stale = "а в коде этого нет — вычеркнуть строку: список только сжимается"
    assert told == [
        f"backend/cli/demo_data.py в списке «{RULES[rule][0]}», {stale}"
        for rule in ("create", "delete")
    ]


def test_the_same_copy_without_plants_is_green(tmp_path: Path) -> None:
    """Положительный контроль: красное выше дал подложенный модуль, а не копия."""
    seen = scan(_copy(tmp_path))

    assert judge(seen) == []
    assert seen.files == scan(ROOT / "backend").files
