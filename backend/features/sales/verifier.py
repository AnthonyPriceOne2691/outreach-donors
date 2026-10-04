"""Проверка адреса лида платным сервисом — до первого письма.

Проверка MX говорит только о домене; жив ли сам ящик, знает лишь сервис,
который поговорит с почтовым сервером от своего имени. Разговор SMTP со
своего IP отклонён планом: он бьёт по репутации и запрещён многими
серверами. Поэтому — внешний проверяльщик за интерфейсом.

**Две реализации, выбирает `SALES_VERIFIER_PROVIDER`.** `fixture` отвечает
по правилам из самого адреса: без сети, без денег, одинаково сегодня и
завтра — на нём живут разработка и проверка проводки. `live` — Hunter Email
Verifier. Умолчание — `fixture`; на `live` переключает человек: ключ общий
с соседней системой, квота одна на обе. `live` без ключа — отказ на старте,
до первого лида, а не на первом платном вызове.

**Вердикт и отказ сервиса — разные вещи** (`okf/provider-refusals.md`).
Вердикт — в том числе `unknown`, «провайдер не знает», — возвращается
и записывается лиду. Отказ — сеть, 429, 5xx, непонятный ответ —
исключение: лид остаётся `new` с причиной, следующая очистка повторит
проверку. Квота и закрытая учётка — свои исключения, их повторять нельзя,
и очистка на них останавливает платную часть прохода.

Исключения и разбор маркеров отказа — общие с поиском адресов
(`contacts/provider.py`): сервис тот же, маркеры те же, вторую копию
их таблицы заводить нельзя.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from backend.config import contacts as contacts_cfg
from backend.config import sales as cfg
from backend.config.startup_checks import ConfigError
from backend.features.contacts.provider import (
    BASE_URL,
    ProviderBlockedError,
    ProviderError,
    _raise_refusal,
)

logger = logging.getLogger(__name__)

FIXTURE = "fixture"
LIVE = "live"
KNOWN = (FIXTURE, LIVE)

#: Слово провайдера → доставляем ли. `None` — провайдер не знает: это вердикт,
#: а не отказ; лид идёт дальше, слово остаётся в `verification_status`.
DELIVERABLE_BY_STATUS: dict[str, bool | None] = {
    "valid": True,
    "accept_all": True,  # сервер принимает всё: ящик не подтверждён, но и не отвергнут
    "webmail": True,
    "unknown": None,
    "invalid": False,
    "disposable": False,
}

#: Почему адресом нельзя пользоваться — словами, по слову провайдера.
UNDELIVERABLE_WORDS = {
    "invalid": "адрес не существует",
    "disposable": "одноразовый ящик",
}


@dataclass(frozen=True, slots=True)
class Verdict:
    """Что проверяльщик сказал об адресе."""

    #: Слово провайдера: valid, accept_all, webmail, unknown, invalid, disposable.
    status: str
    #: Уверенность 0–100, если провайдер её даёт.
    score: int | None
    #: Сколько платных единиц стоила проверка. У `fixture` ноль: выдуманная
    #: трата в журнал расхода не пишется — он для денег, а не для прогонов.
    units: int = 0

    @property
    def deliverable(self) -> bool | None:
        return DELIVERABLE_BY_STATUS[self.status]

    @property
    def words(self) -> str:
        return UNDELIVERABLE_WORDS.get(self.status, "")


@runtime_checkable
class EmailVerifier(Protocol):
    """Проверяльщик адреса. За интерфейсом, чтобы смена сервиса не трогала очистку."""

    name: str

    async def verify(self, email: str) -> Verdict:
        """Вердикт по адресу. Отказ сервиса — `ProviderError` и наследники."""
        ...


class FixtureVerifier:
    """Вердикт по правилам из самого адреса: без сети и без денег.

    Правило — по началу локальной части, чтобы тест и демо задавали исход
    самим адресом: `bounce@` — ящика нет, `disposable@` — одноразовый,
    `unknown@` — провайдер не знает, `catchall@` — сервер принимает всё;
    прочие годные. Уверенность выдумана и не кругла: чтобы не спутать
    с настоящей и чтобы испорченное сравнение роняло тест.
    """

    name = FIXTURE

    _RULES: tuple[tuple[tuple[str, ...], str, int], ...] = (
        (("bounce", "invalid", "dead"), "invalid", 7),
        (("disposable",), "disposable", 11),
        (("unknown",), "unknown", 48),
        (("catchall", "catch-all", "acceptall", "accept-all"), "accept_all", 61),
    )

    async def verify(self, email: str) -> Verdict:
        local = email.partition("@")[0].lower()
        for markers, status, score in self._RULES:
            if local.startswith(markers):
                return Verdict(status, score)
        return Verdict("valid", 93)


class HunterVerifier:
    """Hunter Email Verifier. Разбор ответа защитный: чужой формат однажды
    поменяется, и молчать об этом нельзя."""

    name = "hunter"

    def __init__(self, http: httpx.AsyncClient, *, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def verify(self, email: str) -> Verdict:
        response = await self._get(email)
        body = _json(response)
        code = response.status_code
        if code == httpx.codes.UNAUTHORIZED:
            # Ключ не принят. Код однозначен при любом маркере, а повторять
            # бессмысленно: каждый следующий лид получил бы тот же отказ.
            said = f", {details}" if (details := _details(body)) else ""
            raise ProviderBlockedError(
                f"ключ не принят провайдером (HTTP 401{said}) — проверить CONTACTS_HUNTER_API_KEY"
            )
        if body is not None and body.get("errors"):
            _raise_refusal(body, code)  # маркер раньше кода: 429 бывает закрытой учёткой
        if code == httpx.codes.TOO_MANY_REQUESTS or code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise ProviderError(f"провайдер не ответил (HTTP {code}) — повторим следующей очисткой")
        if code == httpx.codes.ACCEPTED:
            raise ProviderError("провайдер ещё проверяет адрес — повторим следующей очисткой")
        if body is None:
            raise ProviderError(
                f"ответ не разобран как JSON (HTTP {code}): {response.text[:120]!r}"
            )
        return _verdict(body, code)

    async def _get(self, email: str) -> httpx.Response:
        try:
            # Ключ идёт ЗАГОЛОВКОМ, а не параметром адреса: в адресе он утекал
            # бы в журналы прокси и в текст ошибки провайдера.
            return await self._http.get(
                f"{BASE_URL}/email-verifier",
                params={"email": email},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=contacts_cfg.HUNTER_TIMEOUT_SEC,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"сеть до провайдера не дошла: {exc!r}") from exc


def _json(response: httpx.Response) -> dict[str, Any] | None:
    """Тело как объект; не JSON или не объект — `None`, судит вызывающий по коду."""
    try:
        body = response.json()
    except ValueError:
        # Не JSON — исход, а не потеря: вызывающий назовёт его по коду ответа.
        logger.debug("продажи: ответ проверяльщика не JSON", extra={"status": response.status_code})
        return None
    return body if isinstance(body, dict) else None


def _details(body: dict[str, Any] | None) -> str:
    errors = (body or {}).get("errors")
    first = errors[0] if isinstance(errors, list) and errors else {}
    return str(first.get("details") or "") if isinstance(first, dict) else str(first)


def _verdict(body: dict[str, Any], code: int) -> Verdict:
    data = body.get("data")
    if not isinstance(data, dict):
        raise ProviderError(f"в ответе нет объекта data (HTTP {code}): {str(body)[:120]!r}")
    status = data.get("status")
    if not isinstance(status, str) or status not in DELIVERABLE_BY_STATUS:
        # «Мы не поняли» громко: молча назвать это «провайдер не знает» —
        # и смена формата выглядела бы как база сплошь из неизвестных адресов.
        raise ProviderError(
            f"формат ответа поменялся: status={status!r} (HTTP {code}) — читать лог, не гадать"
        )
    score = data.get("score")
    return Verdict(status, score if isinstance(score, int) else None, units=1)


def build_verifier(http: httpx.AsyncClient) -> EmailVerifier:
    """Проверяльщик по настройке. Отказ — на старте, до первого лида."""
    name = (cfg.VERIFIER_PROVIDER or "").strip().lower()
    if name == FIXTURE:
        logger.warning(
            "продажи: проверяльщик адресов — fixture, вердикты выдуманные: "
            "годится для проверки проводки, не для писем"
        )
        return FixtureVerifier()
    if name == LIVE:
        if not contacts_cfg.HUNTER_API_KEY:
            raise ConfigError(
                "SALES_VERIFIER_PROVIDER=live, а CONTACTS_HUNTER_API_KEY пуст. "
                "Заполнить ключ или вернуть fixture — без ключа проверка адресов не стартует"
            )
        return HunterVerifier(http, api_key=contacts_cfg.HUNTER_API_KEY)
    raise ConfigError(
        f"SALES_VERIFIER_PROVIDER=«{cfg.VERIFIER_PROVIDER}» — такого проверяльщика нет. "
        f"Известные: {', '.join(KNOWN)}"
    )
