"""Промпты судьи, переписывания письма и разбора ответа — файлами, байт в байт.

До 07.10.2026 эти тексты стояли строками в модулях, и поверхность модели
(`model_surface`) могла назвать только модуль целиком: правка разбора или
проверок рядом с промптом тоже требовала бы блока об изменении поведения
модели. Теперь текст лежит в `prompts/` рядом с модулем, а имя в модуле
прежнее: его берут сборка запроса и тесты.

Хэш ниже — текст, который уходил модели до переноса. Он держит две вещи:
перенос не поменял ни байта, и правка промпта не пройдёт молча.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import ModuleType

import pytest
from backend.features.donors import publisher_judge
from backend.features.letters import rewrite
from backend.features.replies import extract

#: Файл в `prompts/`, модуль, имя в нём и sha256 текста, каким он уходил
#: модели до переноса (main 58b9806). Хэш не секрет: detect-secrets считает
#: ключом любую длинную hex-строку, отсюда пометки.
PINNED: list[tuple[str, ModuleType, str, str]] = [
    (
        "judge",
        publisher_judge,
        "SYSTEM",
        "4365fcd9b0133639a62ec5dbe5a94680d2d9c55a11b6bcfd7a86acc279c3b96e",  # pragma: allowlist secret
    ),
    (
        "arbiter",
        publisher_judge,
        "ARBITER_SYSTEM",
        "9db7081c02f28b65827c5f965b0d134ca36bbf0cfcc07100b70bebea7d5667ca",  # pragma: allowlist secret
    ),
    (
        "rewrite",
        rewrite,
        "SYSTEM",
        "1a9724df15ff294148cea9c582858bbf4a79ffed7097dab8c240b801d43899ef",  # pragma: allowlist secret
    ),
    (
        "extract",
        extract,
        "SYSTEM",
        "4d62659c7f86596b85f09ae048b23dc0ab6e8fec4213011ff9794e7e0fe1d29e",  # pragma: allowlist secret
    ),
]


@pytest.mark.parametrize(
    ("stem", "module", "name", "digest"), PINNED, ids=[case[0] for case in PINNED]
)
def test_prompt_is_the_file_next_to_the_module(
    stem: str, module: ModuleType, name: str, digest: str
) -> None:
    path = Path(str(module.__file__)).with_name("prompts") / f"{stem}.md"
    text = getattr(module, name)

    assert text == path.read_text(encoding="utf-8").strip()
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == digest, (
        f"{module.__name__}.{name} разошёлся с зафиксированным текстом. Правка "
        f"prompts/{stem}.md — это правка поведения модели: новая PROMPT_VERSION, "
        "где она есть, прогон scripts/eval_*.py, где он есть, и новый хэш здесь"
    )
