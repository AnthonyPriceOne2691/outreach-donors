"""Проверка ключа платного сервиса поиска адресов — живой ли и сколько осталось.

Эндпоинт аккаунта бесплатный: он не тратит поисковые запросы, поэтому
спрашивать остаток у провайдера можно перед каждым прогоном, а не считать
его по своей таблице (delivery/CONSTITUTION.md, «остаток спрашивается
у провайдера»).

Запуск:
    .venv/bin/python scripts/check_hunter.py

Разовая проверка, что поиск по домену действительно работает. Тратит
один платный запрос, поэтому только по явному флагу:

    .venv/bin/python scripts/check_hunter.py --domain example.com
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx
from backend.config import contacts

BASE_URL = "https://api.hunter.io/v2"
TIMEOUT = 20.0


class CheckFailedError(RuntimeError):
    """Проверка не прошла. Сообщение говорит, что делать."""


def _unpack(resp: httpx.Response) -> dict[str, Any]:
    """Ответ провайдера в словарь.

    «Провайдер отказал» и «мы не поняли ответ» — разные исходы, и второй
    обязан быть громким: молча вернув пустое, мы бы решили, что ключ мёртв,
    хотя поменялся формат ответа.
    """
    try:
        body = resp.json()
    except ValueError as exc:
        raise CheckFailedError(
            f"ответ не разобран как JSON (HTTP {resp.status_code}): {resp.text[:200]!r}. "
            "Проверить, не отвечает ли вместо провайдера прокси или страница-заглушка"
        ) from exc

    if not isinstance(body, dict):
        raise CheckFailedError(f"ждали объект, пришло {type(body).__name__}: {body!r}")

    errors = body.get("errors")
    if errors:
        first = errors[0] if isinstance(errors, list) and errors else errors
        details = first.get("details") if isinstance(first, dict) else first
        code = first.get("code") if isinstance(first, dict) else resp.status_code
        hint = {
            401: "ключ не принят — проверить CONTACTS_HUNTER_API_KEY в .env",
            403: "ключ принят, но доступ к эндпоинту закрыт тарифом",
            429: "частота превышена — повторить позже",
        }.get(int(code or 0), "смотреть сообщение провайдера")
        raise CheckFailedError(f"провайдер отказал ({code}): {details}. {hint}")

    data = body.get("data")
    if not isinstance(data, dict):
        raise CheckFailedError(f"в ответе нет объекта data: {body!r}")
    return data


def _account(client: httpx.Client) -> dict[str, Any]:
    resp = client.get("/account", params={"api_key": contacts.HUNTER_API_KEY})
    return _unpack(resp)


def _domain_search(client: httpx.Client, domain: str) -> dict[str, Any]:
    resp = client.get(
        "/domain-search",
        params={"domain": domain, "api_key": contacts.HUNTER_API_KEY, "limit": 5},
    )
    return _unpack(resp)


def _print_account(data: dict[str, Any]) -> None:
    requests = data.get("requests")
    if not isinstance(requests, dict):
        raise CheckFailedError(f"в ответе нет блока requests: {data!r}")

    print("Ключ принят.")
    print(f"  аккаунт:    {data.get('email', '—')}")
    print(f"  тариф:      {data.get('plan_name', '—')} ({data.get('plan_level', '—')})")
    print(f"  сброс:      {data.get('reset_date', '—')}")

    for name, title in (("searches", "поиски"), ("verifications", "проверки адресов")):
        block = requests.get(name)
        if not isinstance(block, dict):
            print(f"  {title}: блока нет в ответе — формат мог поменяться")
            continue
        used, available = block.get("used"), block.get("available")
        left = available - used if isinstance(used, int) and isinstance(available, int) else "?"
        print(f"  {title}: потрачено {used} из {available}, осталось {left}")


def _print_search(domain: str, data: dict[str, Any]) -> None:
    emails = data.get("emails")
    if not isinstance(emails, list):
        raise CheckFailedError(f"в ответе поиска нет списка emails: {data!r}")

    print(f"\nПоиск по домену {domain}: адресов найдено {len(emails)}.")
    for item in emails[:5]:
        if not isinstance(item, dict):
            print(f"  строка не разобрана: {item!r}")
            continue
        print(
            f"  {item.get('value', '—')}  "
            f"тип={item.get('type', '—')}  "
            f"уверенность={item.get('confidence', '—')}  "
            f"источников={len(item.get('sources') or [])}"
        )
    if not emails:
        print("  пусто — законный исход: провайдер про этот домен ничего не знает")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--domain",
        help="проверить живой поиск по домену. ТРАТИТ один платный запрос",
    )
    args = parser.parse_args()

    if not contacts.HUNTER_API_KEY:
        print(
            "CONTACTS_HUNTER_API_KEY пуст. Положить ключ в .env и запустить снова.",
            file=sys.stderr,
        )
        return 2

    try:
        with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
            _print_account(_account(client))
            if args.domain:
                _print_search(args.domain, _domain_search(client, args.domain))
    except CheckFailedError as exc:
        print(f"Ключ не проверен: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(
            f"Сеть до провайдера не дошла: {exc!r}. Проверить доступность api.hunter.io",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
