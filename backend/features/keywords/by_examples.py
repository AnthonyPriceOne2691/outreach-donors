"""Сборка ключей по примерам сайтов: примеры → главные → общая тема → тот же генератор.

Человек показывает сайты, которые ему нужны, — сервис ищет похожие. Ключи собирает
тот же генератор, что у сборки по теме (`generator.PoolBuilder`: углы, гигиена,
дедуп, добор, футпринты набора «guest»), а тема для него берётся с главных
примеров (`example_theme.py`). Дальше всё как у прогона: ключи падают в поле,
человек их правит, смета и кап — последний рубеж перед тратой.

**Порядок — от дешёвого к платному.** Строки поля проверяются до сети
(`examples.read_examples`), набор углов и рынок — до главных (`PoolBuilder.check`),
главные — до модели, и ни одной прочитанной главной — отказ без единого токена:
тема из пустоты оплатила бы выдачу ни за чем.

**Отказ одного примера — не отказ сборки.** Тема выводится по тем, что открылись,
а не открывшиеся названы с причиной: человек видит, на чём стоит тема.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from backend.features.keywords.client import KeygenClient
from backend.features.keywords.example_pages import ExamplePage, PageReader
from backend.features.keywords.example_theme import Theme, infer_theme
from backend.features.keywords.examples import read_examples
from backend.features.keywords.generator import Pool, PoolBuilder

logger = logging.getLogger(__name__)


class NothingReadError(ValueError):
    """Ни одна главная примеров не прочитана: тему вывести не из чего."""


@dataclass(frozen=True, slots=True)
class ExamplesPool:
    """Пул по примерам и то, на чём он стоит."""

    pool: Pool
    theme: Theme
    pages: list[ExamplePage]  # по примеру на строку поля, в её порядке
    repeats: int  # строк поля, повторивших уже взятый сайт
    theme_tokens: int  # из них — на вызов о теме


def _nothing_read(pages: Sequence[ExamplePage]) -> str:
    sites = "; ".join(f"{page.example.shown} — {page.reason}" for page in pages)
    return (
        f"Ни одна главная примеров не прочитана — тему вывести не из чего: {sites}. "
        "Дайте другие примеры или соберите ключи по теме"
    )


async def build_from_examples(
    client: KeygenClient,
    reader: PageReader,
    lines: Sequence[str],
    *,
    country: str,
    languages: Sequence[str],
    preset_name: str,
    cap: int,
) -> ExamplesPool:
    """Пул по примерам. Отказы — исключениями, со словами для человека:
    `ExamplesError` (строки поля), `NothingReadError` (ни одной главной),
    отказ набора углов или доли (`PoolBuilder.check`) и `LlmError` модели — ключа нет
    (до главных) или она отказала."""
    examples = read_examples(lines)
    PoolBuilder(client).check(cap=cap, languages=languages, preset_name=preset_name)
    client.require_key()

    pages = await reader.read_all(examples.sites)
    read = [page for page in pages if page.read]
    logger.info(
        "примеры: прочитано %d из %d — %s",
        len(read),
        len(pages),
        {page.example.host: page.reason or "прочитана" for page in pages},
    )
    if not read:
        raise NothingReadError(_nothing_read(pages))

    theme = await infer_theme(client, read, country=country, languages=languages)
    theme_tokens = client.tokens_spent
    pool = await PoolBuilder(client, examples=theme.about, language_topics=theme.topics).build(
        cap=cap, country=country, languages=languages, preset_name=preset_name
    )
    return ExamplesPool(
        pool=pool, theme=theme, pages=pages, repeats=examples.repeats, theme_tokens=theme_tokens
    )
