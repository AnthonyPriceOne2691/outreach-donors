"""Живая проверка одного адреса: `outreach sales-verify-email --email имя@домен`.

Включение Hunter (`SALES_VERIFIER_PROVIDER=live`) проверяется одной командой до первой
очистки: тот же проверяльщик, что у очистки (`verifier.build_verifier` — та же настройка, тот
же ключ, те же повторы и разбор отказов), один адрес, одна платная проверка, итог словами.
Лидов команда не трогает; пишет только строку расхода `sales_verify` — единица Hunter мимо
журнала не уходит.

Коды выхода — как у `sales-clean`: 0 — вердикт получен; 2 — адрес не адрес или проверяльщик
выдуманный (`fixture`: Hunter не спрошен — настройка не та или контейнер не пересоздан), а
`live` без ключа — тоже 2, отказом настроек общей таблицей консоли; 7 — квота, закрытая
учётка, ключ не принят или частота и после повторов: повтор без человека не поможет; 8 —
сеть, 5xx, «ещё проверяет» или непонятный ответ: повтор поможет.
"""

from __future__ import annotations

import argparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cli.prune import in_session
from backend.cli.sales import EXIT_BAD_INPUT, EXIT_OK, EXIT_UNAVAILABLE, EXIT_VERIFIER_STOPPED
from backend.features.contacts.provider import ProviderError
from backend.features.core import usage
from backend.features.sales.verifier import FIXTURE, OPERATION, STOPS, Verdict, build_verifier
from backend.features.sales.verify_cap import MonthCapError, MonthCapped, capped

#: Слово Hunter → что оно значит словами.
SAID = {
    "valid": "адрес есть",
    "accept_all": "сервер принимает любой адрес — ящик не подтверждён, но и не отвергнут",
    "webmail": "ящик на бесплатной почте",
    "unknown": "провайдер не знает",
    "invalid": "адрес не существует",
    "disposable": "одноразовый ящик",
}
NOT_LIVE = (
    "Проверяльщик адресов: fixture — вердикты выдуманные, Hunter не спрошен.\n"
    "Живая проверка: SALES_VERIFIER_PROVIDER=live и ключ CONTACTS_HUNTER_API_KEY в .env, "
    "затем пересоздать контейнеры (up -d, не restart: restart не перечитывает .env) — "
    "и команду повторить."
)


async def cmd_sales_verify_email(args: argparse.Namespace) -> int:
    """Проверить один адрес проверяльщиком по настройке — живьём, одной единицей."""
    return await in_session(lambda session: run_verify_email(session, args))


async def run_verify_email(
    session: AsyncSession, args: argparse.Namespace, *, http: httpx.AsyncClient | None = None
) -> int:
    """Проверка на готовой сессии. Проверяльщик — по настройке, как у очистки."""
    email = args.email.strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain.strip("."):
        print(f"«{args.email}» — не адрес почты: нужен вид имя@домен.зона")
        return EXIT_BAD_INPUT
    if http is not None:
        return await _verify_with(session, http, email)
    async with httpx.AsyncClient() as own:
        return await _verify_with(session, own, email)


async def _verify_with(session: AsyncSession, http: httpx.AsyncClient, email: str) -> int:
    built = build_verifier(http)  # `live` без ключа — `ConfigError` до запроса
    if built.name == FIXTURE:
        print(NOT_LIVE)
        return EXIT_BAD_INPUT
    verifier = await capped(session, built)  # тот же месячный потолок, что у очистки
    print(f"Проверяльщик адресов: {verifier.name} — живой, ключ общий с донорами.")
    try:
        verdict = await verifier.verify(email)
    except ProviderError as exc:
        who = "Проверка не сделана" if isinstance(exc, MonthCapError) else "Hunter отказал"
        print(f"{who}: {exc}.")
        print("Вердикта нет — в журнал расхода ничего не записано, лиды не тронуты.")
        return EXIT_VERIFIER_STOPPED if isinstance(exc, STOPS) else EXIT_UNAVAILABLE
    if verdict.units:
        usage.record(session, operation=OPERATION, units=verdict.units)
        await session.commit()
    print(_verdict_line(email, verdict))
    print(f"Единиц Hunter: {verdict.units} — записано в журнал расхода ({OPERATION}, продажи).")
    if isinstance(verifier, MonthCapped):
        print(f"{verifier.month.sentence}.")
    return EXIT_OK


def _verdict_line(email: str, verdict: Verdict) -> str:
    score = "не названа" if verdict.score is None else str(verdict.score)
    fate = (
        "очистка отсеет такого лида"
        if verdict.deliverable is False
        else "очистка пустит такого лида к письмам"
    )
    return f"{email}: {verdict.status}, уверенность {score} — {SAID[verdict.status]}; {fate}."


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    check = sub.add_parser(
        "sales-verify-email",
        help="проверить один адрес проверяльщиком продаж живьём: одна единица Hunter, итог словами",
    )
    check.add_argument("--email", required=True, help="адрес, который проверить: имя@домен.зона")
