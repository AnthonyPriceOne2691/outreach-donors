"""Поле гипотезы и разбор описания в eval судьи сегмента v4; пересчёт полос по сохранённым фактам.

- **Поле «страны компаний»** (`hypothesis_countries`) — у случая (`foreign_countries`: `fit`,
  `accept` или `review`; `not_write` — коды ISO) или ключами запуска для всего набора (`--other`,
  `--exclude`); у случая сильнее. Без того и другого — умолчание новой гипотезы: «на проверку».
- **Разбор описания** (`segment_scope`) — один вызов на описание, как у прогона; итог и его токены
  ложатся в итог eval (`scopes`), сбой — «на проверку» словами у сайтов этого описания.
- **Пересчёт по фактам** (`--from-facts итог.json`): факты сайтов и разбор описаний из прошлого итога
  eval — полосы ставит таблица правил заново, модель не зовётся. Так меряется правка поля гипотезы
  без модели — как пересуд прогона, — и другая таблица правил на тех же фактах.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_language
from backend.features.sales.hypothesis_countries import (
    CountryPolicy,
    OtherCountries,
    checked_codes,
)
from backend.features.sales.segment_facts import SiteFacts
from backend.features.sales.segment_judge import Said, SegmentClient, SegmentVerdict, SiteText
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_scope import Scope, read_scope
from scripts.eval_sales_segment_metrics import Result

#: Как набор называет положение «компании из других стран» (`fit` — у внешнего набора разметки).
OTHER_NAMES = {"fit": OtherCountries.ACCEPT, "accept": OtherCountries.ACCEPT}


class FactsError(RuntimeError):
    """Итог eval для пересчёта не годится: нет файла, фактов или разбора описаний."""


def policy_of_case(case: dict[str, Any], fallback: CountryPolicy) -> CountryPolicy:
    """Поле гипотезы случая; у случая его нет — то, что задано на запуске."""
    other, excluded = case.get("foreign_countries"), case.get("not_write")
    if other is None and excluded is None:
        return fallback
    named = OTHER_NAMES.get(str(other).strip().lower(), OtherCountries.REVIEW)
    codes = checked_codes(excluded) if excluded else sorted(fallback.excluded)
    return CountryPolicy(named if other is not None else fallback.other, frozenset(codes))


def policy_of_args(other: str, exclude: str) -> CountryPolicy:
    """Поле гипотезы ключами запуска: `--other review|accept`, `--exclude UA,PL`."""
    codes = checked_codes(code for code in exclude.split(",") if code.strip())
    return CountryPolicy(OtherCountries(other), frozenset(codes))


async def scopes_of(
    cases: Iterable[dict[str, Any]], client: SegmentClient
) -> dict[str, dict[str, Any]]:
    """Разбор каждого описания набора — один вызов на описание: отпечаток → итог и токены."""
    found: dict[str, dict[str, Any]] = {}
    for case in cases:
        digest = sj.segment_digest(case["segment"])
        if digest in found:
            continue
        read, asked = await client.scope(case["segment"])
        entry: dict[str, Any] = {"tokens": asked.tokens}
        if isinstance(read, Scope):
            entry |= read.as_json() | {"words": read.words}
        else:
            entry["failed"] = read
        found[digest] = entry
    return found


def scope_of(scopes: dict[str, dict[str, Any]], segment: str) -> Scope | None:
    """Разбор описания из итога eval; сбой или нет — `None`."""
    entry = scopes.get(sj.segment_digest(segment)) or {}
    if entry.get("failed") or "countries" not in entry:
        return None
    keys = ("countries", "kinds", "excludes_networks")
    read = read_scope(json.dumps({key: entry[key] for key in keys if key in entry}))
    return read if isinstance(read, Scope) else None


def result_of(case: dict[str, Any], verdict: SegmentVerdict, split: dict[str, int]) -> Result:
    """Вердикт по случаю — строкой итога eval, с правилом таблицы и фактами."""
    return Result(
        case["id"],
        case["hypothesis"],
        case["expect"],
        verdict.lane.value,
        verdict.reason,
        verdict.quote,
        verdict.decided_by.value,
        verdict.unanswered,
        verdict.tokens,
        split,
        tuple(case.get("tags") or ()),
        verdict.kind,
        segment_language.identify(case["text"]),
        verdict.cut_off,
        verdict.rule,
        verdict.facts.as_json() if verdict.facts is not None else None,
        verdict.retried,
        verdict.retry_held,
    )


def saved(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Прошлый итог eval: случаи по номеру и разбор описаний."""
    if not path.is_file():
        raise FactsError(f"итога eval нет: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    scopes = payload.get("scopes")
    if not isinstance(scopes, dict):
        raise FactsError(f"в итоге {path} нет разбора описаний — он записан до судьи v4")
    return {row["case_id"]: row for row in payload["results"]}, scopes


def _said(row: dict[str, Any] | None) -> Said:
    """Ответ модели из прошлого итога: факты — или почему их не было; токены вызова."""
    if row is None:
        return Said(None, "в прошлом итоге случая нет")
    facts = SiteFacts.from_json(row["facts"]) if row.get("facts") else None
    failure = "" if facts is not None else str(row.get("reason") or "фактов нет")
    return Said(facts, failure, None, int(row.get("tokens") or 0), unanswered=row["unanswered"])


def _verdict(
    case: dict[str, Any],
    row: dict[str, Any] | None,
    policy: CountryPolicy,
    scope: Scope | None,
    letters: str | None,
) -> SegmentVerdict:
    """Вердикт случая по фактам — тем же порядком, что у судьи (`segment_judge.decide`)."""
    text = SiteText.parse(case["text"])
    host = case["host"]
    ruled = sj.before_model(host, text)
    if ruled is not None:
        return sj.settle(host=host, policy=policy, scope=scope, ruled=ruled)
    language = sj.by_language(text.without_stub(), letters)
    if language is not None and language.lane is Lane.REJECT:
        return sj.settle(host=host, policy=policy, scope=scope, language=language)
    return sj.settle(host=host, policy=policy, scope=scope, said=_said(row), language=language)


def redecided(
    cases: Sequence[dict[str, Any]],
    path: Path,
    fallback: CountryPolicy,
    letters: str | None,
) -> list[Result]:
    """Полосы по фактам прошлого итога — таблицей правил, без модели (`segment_judge.settle`)."""
    rows, scopes = saved(path)
    found = []
    for case in cases:
        row = rows.get(case["id"])
        verdict = _verdict(
            case,
            row,
            policy_of_case(case, fallback),
            scope_of(scopes, case["segment"]),
            case.get("letters") or letters,
        )
        found.append(result_of(case, verdict, (row or {}).get("split") or {}))
    return found
