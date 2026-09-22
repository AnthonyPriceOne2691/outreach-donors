"""Замер второй стороны развилки Этапа 2: что про рекламодателей знает Ahrefs.

Первую сторону меряет `outreach crawl` — сколько страниц донор отдаёт
обходу. Здесь второй вопрос: **сколько из того же можно взять запросом,
не скачав ни одной страницы, и по какой цене.**

Три вопроса, на которые скрипт отвечает числами:

1. Сколько доменов-получателей Ahrefs знает по донору (`linkeddomains`)
   и сколько это стоит.
2. Даёт ли он **страницу и анкор** — то, без чего письмо рекламодателю
   не персонализировать (`all-backlinks` со стороны рекламодателя).
3. Во сколько юнитов обходится один донор целиком.

**Скрипт тратит боевые юниты и потому устроен как трата.** Остаток
спрашивается до первого платного запроса; печатается смета; есть жёсткий
потолок `--budget`, после которого замер останавливается на полуслове
и говорит об этом. Не смогли узнать остаток — не тратим.

Запуск:
    python scripts/measure_outlinks.py takebet.co.za supasoka.co.za --budget 2000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx
from backend.config import ahrefs as cfg

#: Минимум на любой запрос — из-за него мелкие выборки невыгодны.
MIN_REQUEST_UNITS = 50

#: Что берём со стороны рекламодателя. Без `traffic*`: они стоят ещё
#: по 10 юнитов на строку, а для вопроса «есть ли страница и анкор»
#: не нужны вовсе. Остальное — ровно входы скоринга: адрес страницы,
#: анкор, ссылка из тела статьи, пометки sponsored/ugc, dofollow.
BACKLINKS_SELECT = "url_from,anchor,is_content,is_sponsored,is_ugc,is_dofollow,first_seen"

#: Что берём про домен-получатель. Одним запросом на донора — вместо
#: запроса на каждого рекламодателя. `linked_domain_traffic`
#: и `domain_rating` намеренно не берём: они стоят дополнительных юнитов
#: на строку, а для отсева «кому донор ставит много ссылок» не нужны.
LINKED_SELECT = "domain,links_from_target,dofollow_links,linked_pages"

#: Поле фильтра «домен-источник». Не `domain_from` — такого столбца
#: у эндпоинта нет, и запрос с ним отвечает 400 (проверено живьём).
#: Берём корневой домен: ссылка с поддомена донора — тоже его ссылка.
SOURCE_DOMAIN_FIELD = "root_name_source"


class BudgetSpentError(RuntimeError):
    """Потолок траты исчерпан. Останов, а не предупреждение."""


@dataclass(slots=True)
class Spending:
    """Копилка замера: сколько списано и на что."""

    budget: int
    spent: int = 0
    by_call: list[tuple[str, int]] = field(default_factory=list)

    def add(self, label: str, units: int) -> None:
        self.spent += units
        self.by_call.append((label, units))

    def check(self) -> None:
        if self.spent >= self.budget:
            raise BudgetSpentError(
                f"потрачено {self.spent} юнитов при потолке {self.budget} — замер остановлен"
            )


@dataclass(slots=True)
class DonorProbe:
    """Что узнали про одного донора."""

    host: str
    outgoing_links: int | None = None
    linked_domains_total: int | None = None
    advertisers: list[str] = field(default_factory=list)
    units: int = 0
    pages_found: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""


def _cost(response: httpx.Response) -> int:
    """Списано по заголовкам. Нет заголовков — не ноль, а громкое «не знаем».

    Ключ общий с соседней системой, и тихий ноль здесь означал бы учёт,
    который однажды разойдётся со счётом.
    """
    for header in ("x-api-units-cost-total-actual", "x-api-units-cost-row-actual"):
        raw = response.headers.get(header)
        if raw is not None:
            try:
                return int(raw)
            except ValueError:
                print(f"  ВНИМАНИЕ: заголовок расхода не число: {raw!r} ({header})")
                break
    print(f"  ВНИМАНИЕ: ответ без заголовка расхода — трата не учтена ({response.url.path})")
    return 0


async def _get(
    client: httpx.AsyncClient, path: str, params: dict[str, Any], *, label: str, money: Spending
) -> tuple[list[dict[str, Any]], int]:
    """Один запрос: строки и цена. Отказ провайдера — остановка, не пустота."""
    money.check()
    response = await client.get(path, params=params)
    units = _cost(response)
    money.add(label, units)

    if response.status_code >= 400:
        raise RuntimeError(f"{label}: {response.status_code} {response.text[:200]}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}: ответ не объект — форма поменялась: {payload!r}")

    # Списки эндпоинты кладут под своим именем (`domains`, `backlinks`),
    # а агрегат приходит одним объектом под `metrics`. Оба вида нам нужны,
    # и различать их по имени ключа значит переписывать разбор на каждом
    # новом эндпоинте.
    rows = next((v for v in payload.values() if isinstance(v, list)), None)
    if rows is None:
        single = next((v for v in payload.values() if isinstance(v, dict)), None)
        if single is None:
            raise RuntimeError(f"{label}: в ответе нет ни строк, ни агрегата: {payload!r}")
        rows = [single]
    return rows, units


async def _quota(client: httpx.AsyncClient) -> tuple[int, int]:
    """Остаток у провайдера. Запрос бесплатный; не ответил — не тратим.

    Лимитов два — на пространство и на ключ, — и кончится работа
    по меньшему из них. Смотреть только на первый значит однажды
    упереться в неизвестно какой из них посреди платной работы.
    """
    response = await client.get("/v3/subscription-info/limits-and-usage")
    response.raise_for_status()
    data = response.json().get("limits_and_usage", {})
    workspace = int(data.get("units_limit_workspace") or 0) - int(
        data.get("units_usage_workspace") or 0
    )
    key = int(data.get("units_limit_api_key") or 0) - int(data.get("units_usage_api_key") or 0)
    left = min(workspace, key) if key else workspace
    return left, min(
        int(data.get("units_limit_workspace") or 0), int(data.get("units_limit_api_key") or 0)
    )


async def _probe_donor(
    client: httpx.AsyncClient, host: str, *, top: int, money: Spending
) -> DonorProbe:
    """Один донор: агрегат, список рекламодателей, проба страницы и анкора."""
    probe = DonorProbe(host=host)
    before = money.spent

    stats, _ = await _get(
        client,
        "/v3/site-explorer/outlinks-stats",
        {"target": host, "mode": "subdomains", "protocol": "both"},
        label=f"outlinks-stats {host}",
        money=money,
    )
    if stats:
        probe.outgoing_links = stats[0].get("outgoing_links")
        probe.linked_domains_total = stats[0].get("linked_domains")

    rows, _ = await _get(
        client,
        "/v3/site-explorer/linkeddomains",
        {"target": host, "mode": "subdomains", "select": LINKED_SELECT, "limit": top},
        label=f"linkeddomains {host}",
        money=money,
    )
    probe.advertisers = [str(r["domain"]).lower() for r in rows if r.get("domain")]
    probe.rows = [r for r in rows if r.get("domain")]
    probe.units = money.spent - before
    return probe


async def _probe_page_and_anchor(
    client: httpx.AsyncClient, donor: str, advertiser: str, *, limit: int, money: Spending
) -> tuple[int, list[dict[str, Any]], int]:
    """Есть ли у Ahrefs страница донора и анкор ссылки на рекламодателя.

    Спрашиваем со стороны рекламодателя: обратные ссылки на него,
    отфильтрованные по домену-источнику. Это тот же вопрос с другой
    стороны, и только с этой стороны у Ahrefs есть адрес страницы.
    """
    before = money.spent
    where = json.dumps({"field": SOURCE_DOMAIN_FIELD, "is": ["eq", donor]})
    rows, _ = await _get(
        client,
        "/v3/site-explorer/all-backlinks",
        {
            "target": advertiser,
            "mode": "subdomains",
            "protocol": "both",
            "history": "live",
            "select": BACKLINKS_SELECT,
            "where": where,
            "limit": limit,
        },
        label=f"all-backlinks {advertiser} ← {donor}",
        money=money,
    )
    return len(rows), rows[:3], money.spent - before


def _print_estimate(hosts: list[str], top: int, budget: int, left: int) -> None:
    calls = len(hosts) * 2 + min(len(hosts), 2)
    floor = calls * MIN_REQUEST_UNITS
    print("\n── Смета замера ──")
    print(f"  Доноров:            {len(hosts)}")
    print(f"  Запросов:           {calls} (по два на донора плюс две пробы страницы)")
    print(f"  Минимум по юнитам:  {floor} (минимум {MIN_REQUEST_UNITS} на любой запрос)")
    print(f"  Строк со стороны рекламодателя: до {top} — они и дают основную цену")
    print(f"  Потолок траты:      {budget}")
    print(f"  Остаток у провайдера: {left}")
    if budget > left:
        print("  ВНИМАНИЕ: потолок выше остатка — ограничителем будет провайдер, а не мы.")


def _print_donor(probe: DonorProbe) -> None:
    print(f"\n=== {probe.host}")
    print(f"  исходящих ссылок:     {probe.outgoing_links}")
    print(f"  доменов-получателей:  {probe.linked_domains_total}")
    print(f"  из них получено:      {len(probe.advertisers)}")
    print(f"  юнитов на донора:     {probe.units}")
    if probe.rows:
        dofollow = sum(1 for r in probe.rows if (r.get("dofollow_links") or 0) > 0)
        print(f"  из них с dofollow:    {dofollow}")
        print("  самые залинкованные:")
        top_rows = sorted(probe.rows, key=lambda r: -(r.get("links_from_target") or 0))[:5]
        for row in top_rows:
            print(
                f"    {row.get('domain')}: ссылок {row.get('links_from_target')}, "
                f"dofollow {row.get('dofollow_links')}, страниц {row.get('linked_pages')}"
            )
    if probe.note:
        print(f"  {probe.note}")


async def measure(hosts: list[str], *, top: int, limit: int, budget: int) -> int:
    money = Spending(budget=budget)
    headers = {"Authorization": f"Bearer {cfg.API_KEY}", "Accept": "application/json"}

    async with httpx.AsyncClient(base_url=cfg.BASE_URL, timeout=60.0, headers=headers) as client:
        try:
            left, total = await _quota(client)
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Остаток юнитов недоступен: {exc}", file=sys.stderr)
            print("Замер не запускается: тратить, не зная остатка, нельзя.", file=sys.stderr)
            return 2

        print(f"Остаток у провайдера: {left} из {total}")
        _print_estimate(hosts, top, budget, left)

        probes: list[DonorProbe] = []
        try:
            for host in hosts:
                probe = await _probe_donor(client, host, top=top, money=money)
                probes.append(probe)
                _print_donor(probe)

            # Страницу и анкор пробуем на первых двух донорах: вопрос
            # «отдаёт ли их Ahrefs вообще» отвечается один раз, а каждая
            # проба стоит строк.
            for probe in probes[:2]:
                if not probe.advertisers:
                    continue
                advertiser = probe.advertisers[0]
                found, sample, units = await _probe_page_and_anchor(
                    client, probe.host, advertiser, limit=limit, money=money
                )
                probe.pages_found, probe.sample = found, sample
                print(f"\n── Страница и анкор: {probe.host} → {advertiser}")
                print(f"  строк получено: {found}, юнитов: {units}")
                for row in sample:
                    print(
                        f"    {row.get('url_from')}\n"
                        f"      анкор: {row.get('anchor')!r}, "
                        f"в теле: {row.get('is_content')}, "
                        f"dofollow: {row.get('is_dofollow')}, "
                        f"sponsored: {row.get('is_sponsored')}, ugc: {row.get('is_ugc')}"
                    )
        except BudgetSpentError as stop:
            print(f"\nОСТАНОВ: {stop}", file=sys.stderr)
        except (RuntimeError, httpx.HTTPError) as exc:
            print(f"\nЗамер прерван: {exc}", file=sys.stderr)

    print("\n" + "=" * 60)
    print(f"Потрачено юнитов: {money.spent}")
    for label, units in money.by_call:
        print(f"  {units:>6}  {label}")
    per_donor = money.spent / len(probes) if probes else 0
    print(f"\nНа донора в среднем: {per_donor:.0f} юнитов")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hosts", nargs="+", help="домены доноров")
    parser.add_argument("--top", type=int, default=50, help="сколько доменов-получателей брать")
    parser.add_argument(
        "--limit", type=int, default=10, help="строк со стороны рекламодателя на пробу"
    )
    parser.add_argument("--budget", type=int, default=2000, help="жёсткий потолок траты в юнитах")
    args = parser.parse_args()

    if not cfg.API_KEY:
        print("Нет ключа Ahrefs: замер не запускается.", file=sys.stderr)
        return 2
    return asyncio.run(measure(args.hosts, top=args.top, limit=args.limit, budget=args.budget))


if __name__ == "__main__":
    sys.exit(main())
