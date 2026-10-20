"""Общая тема примеров: модель читает их главные и называет, что у них общего.

**Модель — та же, что собирает ключи** (`config/llm.KEYGEN_MODEL`: не ниже пятой,
и это замер), и вызов идёт тем же клиентом: тема — первый шаг той же сборки, и тема,
названная слабой моделью, увела бы пул туда же, куда уводят слабые ключи. Вызов —
один на сборку; токены, ключ и отказы — общие с генерацией.

**Ответ — для трёх читателей.** Человеку — тема по-русски коротко (экран прогона);
генератору — тема ниши на каждом языке рынка (её берут угол и футпринт,
`PoolBuilder(language_topics=…)`) и фраза по-английски о том, какие сайты ищем
(строка `sites we look for` просьбы к модели). Пример не про общую тему назван,
а не растворён в ней.

**Текст страниц — данные, а не указания**: так сказано в просьбе, и ответ всё равно
проверяется. Темы нет, ни одного языка рынка нет, объект не разобран — отказ словами
(`LlmError`), а не подстановка: ключи по выдуманной теме оплатили бы выдачу ни за чем.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.features.keywords.angles import load_prompt
from backend.features.keywords.client import Ask, KeygenClient, LlmError
from backend.features.keywords.example_pages import ExamplePage
from backend.features.keywords.examples import site_host
from backend.features.keywords.hygiene import normalize, rejection_reason
from backend.features.serp.markets import LANGUAGE_NAMES

logger = logging.getLogger(__name__)

#: Промпт вызова о теме (`prompts/examples_theme.md`).
THEME_PROMPT = "examples_theme"
#: Запас вывода «строками», как у фраз (`client.build_payload`): у рассуждающей
#: модели это не меньше 1 200 токенов, а ответ о теме — сотня-другая.
THEME_ANSWER_LINES = 20
#: Потолки полей ответа: тема — строка на экране, фраза — строка просьбы.
THEME_CHARS = 80
ABOUT_CHARS = 300
#: Тема ниши — именная группа, а не запрос: длиннее — модель написала предложение.
TOPIC_WORDS = 6


@dataclass(frozen=True, slots=True)
class Theme:
    """Что общего у примеров — словами для человека и для генератора."""

    theme: str  # по-русски, коротко: её читает человек
    about: str  # по-английски: какие сайты ищем — строка просьбы генератору
    topics: dict[str, str]  # язык рынка → тема ниши на нём
    off_theme: tuple[str, ...] = ()  # имена примеров не про общую тему


def page_block(number: int, page: ExamplePage) -> str:
    """Одна главная глазами модели: что сайт говорит о себе, по полю в строке."""
    lines = [f"Example {number}: {page.example.shown}"]
    if page.language:
        lines.append(f"page language: {page.language}")
    lines.append(f"title: {page.title}")
    if page.description:
        lines.append(f"description: {page.description}")
    if page.headings:
        lines.append("headings: " + " · ".join(page.headings))
    if page.menu:
        lines.append("menu: " + " · ".join(page.menu))
    if page.text:
        lines.append(f"text: {page.text}")
    return "\n".join(lines)


def build_theme_prompt(
    pages: Sequence[ExamplePage], *, country: str, languages: Sequence[str]
) -> str:
    """Просьба о теме: рынок, его языки и прочитанные главные — по блоку на пример."""
    blocks = "\n\n".join(page_block(number, page) for number, page in enumerate(pages, 1))
    return (
        f"market/country: {country}\n"
        f"languages of the market: {', '.join(languages)}\n"
        f"examples: {len(pages)}\n\n"
        f"{blocks}"
    )


def _object(content: str) -> dict[str, Any]:
    """Объект ответа. Обёртка в тройные кавычки и слова вокруг — не помеха,
    а не объект вовсе — отказ: «понять по-своему» ответ о теме нельзя."""
    text = (content or "").strip().strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        raise LlmError(f"модель ответила о теме не объектом: {text[:120]!r}")
    if end <= start:
        raise LlmError(f"ответ модели о теме оборван: {text[:120]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise LlmError(f"ответ модели о теме не разобран: {text[:120]!r}") from exc
    if not isinstance(data, dict):
        raise LlmError(f"модель ответила о теме не объектом: {text[:120]!r}")
    return data


def _line(value: object, limit: int) -> str:
    """Строка ответа одной строкой и не длиннее `limit`; не строка — пусто."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip()


def _topic(value: object) -> str:
    """Тема ниши, годная в просьбу и в футпринт: та же гигиена, что у фразы."""
    if not isinstance(value, str):
        return ""
    topic = normalize(value)
    if not topic or len(topic.split()) > TOPIC_WORDS:
        return ""
    reason = rejection_reason(topic)
    if reason is not None:
        logger.info("примеры: тему «%s» не берём — %s", topic, reason)
        return ""
    return topic


#: Имя языка → его код: ответ называет язык и так, и так.
_CODES = {name.lower(): code for code, name in LANGUAGE_NAMES.items()}


def _named(value: object, language: str) -> object:
    """Тема языка в ответе — под его именем или кодом, без оглядки на регистр."""
    if not isinstance(value, dict):
        return None
    given = {str(key).strip().lower(): item for key, item in value.items()}
    key = language.lower()
    return given.get(key) or given.get(_CODES.get(key, ""))


def _topics(value: object, languages: Sequence[str]) -> dict[str, str]:
    """Тема на каждом языке рынка. Языка нет в ответе — тема первого названного:
    угол её переведёт, а пропуск языка оставил бы его без пула."""
    found = {
        language: topic for language in languages if (topic := _topic(_named(value, language)))
    }
    if not found:
        raise LlmError(
            f"модель не назвала тему ниши ни на одном языке рынка ({', '.join(languages)})"
        )
    first = next(iter(found.values()))
    missing = [language for language in languages if language not in found]
    if missing:
        logger.warning("примеры: тема на языках %s не названа — берём «%s»", missing, first)
    return {language: found.get(language, first) for language in languages}


def _off_theme(value: object, hosts: Sequence[str]) -> tuple[str, ...]:
    """Примеры не про общую тему — только из тех, что были в просьбе. Названы все —
    пометка снимается: тема «чего-то общего», которого нет ни у кого, — противоречие
    ответа, и пометить всё значит не сказать ничего."""
    if not isinstance(value, list):
        return ()
    known = set(hosts)
    off: list[str] = []
    for item in value:
        try:
            host = site_host(str(item))
        except ValueError as exc:
            logger.info("примеры: пометка «%s» — не имя примера (%s)", item, exc)
            continue
        if host in known and host not in off:
            off.append(host)
    if off and len(off) >= len(known):
        logger.warning("примеры: модель назвала не про тему все примеры — пометку снимаем")
        return ()
    return tuple(off)


def parse_theme(content: str, *, languages: Sequence[str], hosts: Sequence[str]) -> Theme:
    """Ответ модели о теме → тема. `LlmError` — ответа, годного в сборку, нет."""
    data = _object(content)
    theme = _line(data.get("theme"), THEME_CHARS)
    if not theme:
        raise LlmError("модель не назвала общую тему примеров")
    topics = _topics(data.get("topics"), languages)
    about = _line(data.get("about"), ABOUT_CHARS) or next(iter(topics.values()))
    return Theme(
        theme=theme, about=about, topics=topics, off_theme=_off_theme(data.get("off_theme"), hosts)
    )


async def infer_theme(
    client: KeygenClient,
    pages: Sequence[ExamplePage],
    *,
    country: str,
    languages: Sequence[str],
) -> Theme:
    """Общая тема прочитанных главных. Отказ модели — `LlmError` с её причиной."""
    content = await client.reply(
        Ask(
            system=load_prompt(THEME_PROMPT),
            user=build_theme_prompt(pages, country=country, languages=languages),
            max_phrases=THEME_ANSWER_LINES,
        )
    )
    if content is None:
        said = client.refusals[-1] if client.refusals else "модель не ответила"
        raise LlmError(f"общая тема примеров не выведена — {said}")
    theme = parse_theme(content, languages=languages, hosts=[page.example.host for page in pages])
    logger.info(
        "примеры: тема «%s», по языкам %s, не про тему %s",
        theme.theme,
        theme.topics,
        list(theme.off_theme),
    )
    return theme
