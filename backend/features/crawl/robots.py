"""robots.txt: спрашиваем разрешение до первого запроса к страницам.

Правило Этапа 2 короткое — «запрещает, откладываем и помечаем», — но
за ним три решения, которые легко принять неправильно.

**Не прочитали ≠ разрешено.** Сайт, ответивший на robots.txt пятисотым
или оборвавший соединение, не сказал «можно». Считать молчание согласием
значит обойти сайт, который нас запретил, и узнать об этом от его
владельца. Поэтому исходов у чтения три: правила прочитаны, файла нет
(404 — это законное «ограничений нет»), файл не прочитан. Третий
останавливает обход и называется вслух.

**Запрет именно нам обязан работать.** Мы представляемся своим именем,
и группа правил под этим именем сильнее группы `*`. Иначе наш
user-agent — украшение: сайт вписал нас в robots.txt, а мы ходим по
общим правилам.

**`Crawl-delay` слушаем, но не бесконечно.** Встречаются значения
в минуты; подчиняться им буквально значит потратить весь бюджет времени
на один сайт. Берём большее из нашей паузы и запрошенной, но не выше
потолка — и потолок этот записан в настройках, а не спрятан здесь.

Разбор свой, а не `urllib.robotparser`: тот сам ходит в сеть, сам решает,
что делать с ошибкой, и не умеет сказать «не прочитал». Ровно три вещи,
которые нам здесь нужны, он и не отдаёт.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

#: Сколько байт robots.txt читаем. Файл в мегабайты — либо ошибка сайта,
#: либо попытка нас утопить; RFC разрешает обрезать на 500 КиБ.
MAX_ROBOTS_BYTES = 512 * 1024


class RobotsStatus(StrEnum):
    """Чем кончилось чтение robots.txt."""

    RULES = "rules"  # файл прочитан и разобран
    ABSENT = "absent"  # файла нет (404/410) — ограничений нет
    UNREADABLE = "unreadable"  # не прочитали: отказ, обрыв, пятисотый


@dataclass(frozen=True, slots=True)
class Rule:
    """Одна строка `Allow`/`Disallow`, приведённая к регулярному выражению.

    `length` — длина исходного образца: при споре двух правил побеждает
    более длинное, а при равной длине — разрешающее (RFC 9309).
    """

    allow: bool
    length: int
    pattern: re.Pattern[str]


@dataclass(slots=True)
class RobotsRules:
    """Правила для нашего имени: что можно, и как часто."""

    status: RobotsStatus
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)

    @property
    def readable(self) -> bool:
        """Прочитали ли мы правила. Отдельное свойство, потому что
        `не прочитали` — это исход обхода, а не деталь разбора."""
        return self.status is not RobotsStatus.UNREADABLE

    def allows(self, url: str) -> bool:
        """Можно ли открывать этот адрес.

        Файла нет — можно всё. Файл не прочитан — нельзя ничего: сюда
        обход доходить не должен, но если дойдёт, пусть откажет, а не
        разрешит.
        """
        if self.status is RobotsStatus.ABSENT:
            return True
        if self.status is RobotsStatus.UNREADABLE:
            return False

        path = _path_of(url)
        best: Rule | None = None
        for rule in self.rules:
            if not rule.pattern.match(path):
                continue
            if best is None or _wins(rule, best):
                best = rule
        return best.allow if best is not None else True


def _wins(candidate: Rule, current: Rule) -> bool:
    """Побеждает длинный образец; при равной длине — разрешающий."""
    if candidate.length != current.length:
        return candidate.length > current.length
    return candidate.allow and not current.allow


def _path_of(url: str) -> str:
    """Путь с запросом, приведённый к виду, в котором сравнивают образцы."""
    parts = urlparse(url)
    path = unquote(parts.path) or "/"
    return f"{path}?{parts.query}" if parts.query else path


def _to_pattern(value: str) -> re.Pattern[str]:
    """Образец robots в регулярное выражение: `*` — любое, `$` — конец.

    Экранируется всё остальное: путь вида `/search?q=` содержит символы,
    которые regex поймёт по-своему, и без экранирования запрет на один
    раздел иногда запрещал бы половину сайта.
    """
    out: list[str] = []
    for index, char in enumerate(value):
        if char == "*":
            out.append(".*")
        elif char == "$" and index == len(value) - 1:
            out.append("$")
        else:
            out.append(re.escape(char))
    return re.compile("".join(out))


def _split_field(line: str) -> tuple[str, str] | None:
    """Строка `поле: значение` без комментария. Не разобрали — `None`."""
    text = line.split("#", 1)[0].strip()
    if ":" not in text:
        return None
    name, _, value = text.partition(":")
    name, value = name.strip().lower(), value.strip()
    return (name, value) if name else None


def _delay_of(value: str) -> float | None:
    """`Crawl-delay` числом. Не число — предупреждение, а не тихий ноль."""
    try:
        delay = float(value.replace(",", "."))
    except ValueError:
        logger.warning("robots.txt: Crawl-delay не число: %r — игнорируем", value)
        return None
    return delay if delay >= 0 else None


class _Group:
    """Группа правил под одним или несколькими именами.

    Собирается отдельным объектом, потому что принадлежность строки
    к группе определяется порядком: `user-agent` подряд открывают одну
    группу, а первая строка правила её закрывает для новых имён.
    """

    def __init__(self) -> None:
        self.agents: list[str] = []
        self.rules: list[Rule] = []
        self.delay: float | None = None
        self.accepting_agents = True

    def add_agent(self, value: str) -> bool:
        """Принять имя в эту группу. `False` — группа уже закрыта правилами,
        и это имя открывает новую."""
        if not self.accepting_agents:
            return False
        self.agents.append(value.lower())
        return True

    def add_rule(self, allow: bool, value: str) -> None:
        self.accepting_agents = False
        # Пустой `Disallow:` — это «запретов нет», а не «запрещено всё».
        if not value:
            if not allow:
                self.rules.append(Rule(allow=True, length=1, pattern=_to_pattern("/")))
            return
        self.rules.append(Rule(allow=allow, length=len(value), pattern=_to_pattern(value)))

    def set_delay(self, value: str) -> None:
        self.accepting_agents = False
        delay = _delay_of(value)
        if delay is not None:
            self.delay = delay


def _collect_groups(text: str) -> tuple[list[_Group], list[str]]:
    """Группы правил и глобальный список sitemap.

    `Sitemap` намеренно собирается мимо групп: по RFC это поле файла,
    а не группы, и сайты пишут его где угодно — и до первой группы тоже.
    """
    groups: list[_Group] = []
    sitemaps: list[str] = []
    current = _Group()

    for line in text.splitlines():
        parsed = _split_field(line)
        if parsed is None:
            continue
        name, value = parsed

        if name == "sitemap":
            if value:
                sitemaps.append(value)
            continue

        if name == "user-agent":
            # `user-agent` после правил — начало новой группы, а не
            # продолжение прежней: так порядок строк задаёт границы групп.
            if not current.add_agent(value):
                groups.append(current)
                current = _Group()
                current.add_agent(value)
        elif name in ("allow", "disallow"):
            current.add_rule(allow=name == "allow", value=value)
        elif name == "crawl-delay":
            current.set_delay(value)

    groups.append(current)
    return [group for group in groups if group.agents], sitemaps


def _pick_group(groups: list[_Group], agent: str) -> _Group | None:
    """Группа для нашего имени: самое длинное совпадение, иначе `*`.

    Длина важна: сайт может написать правила и для `Bot`, и для
    `ParsingPricesBot`, и адресованы нам вторые.
    """
    agent = agent.lower()
    best: _Group | None = None
    best_length = -1
    fallback: _Group | None = None

    for group in groups:
        for name in group.agents:
            if name == "*":
                fallback = fallback or group
            elif agent.startswith(name) and len(name) > best_length:
                best, best_length = group, len(name)
    return best or fallback


def parse_robots(text: str, agent: str) -> RobotsRules:
    """Разобрать содержимое robots.txt для нашего имени."""
    groups, sitemaps = _collect_groups(text)
    group = _pick_group(groups, agent)
    if group is None:
        return RobotsRules(status=RobotsStatus.RULES, sitemaps=sitemaps)
    return RobotsRules(
        status=RobotsStatus.RULES,
        rules=group.rules,
        crawl_delay=group.delay,
        sitemaps=sitemaps,
    )


def absent(sitemaps: list[str] | None = None) -> RobotsRules:
    """Файла нет: ограничений нет. Отдельная функция, чтобы исход
    «404» нельзя было спутать с «пустые правила»."""
    return RobotsRules(status=RobotsStatus.ABSENT, sitemaps=sitemaps or [])


def unreadable() -> RobotsRules:
    """Файл не прочитан. Обход по такому сайту не идёт."""
    return RobotsRules(status=RobotsStatus.UNREADABLE)
