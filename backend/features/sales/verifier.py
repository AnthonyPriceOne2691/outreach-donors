"""Проверка адреса лида платным сервисом — до первого письма.

Проверка MX говорит только о домене; жив ли сам ящик, знает лишь сервис,
который поговорит с почтовым сервером от своего имени. Разговор SMTP со
своего IP отклонён планом: он бьёт по репутации и запрещён многими
серверами. Поэтому — внешний проверяльщик за интерфейсом.

**Две реализации, выбирает `SALES_VERIFIER_PROVIDER`.** `fixture` отвечает
по правилам из самого адреса: без сети, без денег, одинаково сегодня и
завтра — на нём живут разработка и проверка проводки. `live` — Hunter Email
Verifier. Умолчание — `fixture`; на `live` переключает человек: ключ общий
с соседней системой, счётчик проверок у Hunter — свой, отдельный от её поисков
(свой месячный потолок — `verify_cap.py`). `live` без ключа — отказ на старте,
до первого лида, а не на первом платном вызове.

**Выдуманный вердикт при живом проверяльщике не годен** (`unproven`): лид, чей адрес
проверял `fixture` (`verification_status` — `fixture:…`), в очередь не идёт и его письмо
не уходит — причина словами «очистите базу заново»; очистка берёт его снова (`due`) и
проверит адрес по-настоящему. При `fixture` всё как было: пилот на выдуманных вердиктах
не ломается.

**Вердикт и отказ сервиса — разные вещи** (`okf/provider-refusals.md`).
Вердикт — в том числе `unknown`, «провайдер не знает», — возвращается
и записывается лиду; к письмам лид с `unknown` не идёт, очистка спросит снова через
`RECHECK_DAYS` дней (`unconfirmed.py`, решение владельца 13.10). Отказ — сеть, 5xx,
непонятный ответ — исключение: лид остаётся в прежнем статусе с причиной, следующая
очистка повторит проверку. Квота, закрытая учётка и частота (`STOPS`) — свои исключения:
очистка на них останавливает платную часть прохода, остальные лиды ждут её с причиной.

**Повторы и частота — общим `net/retry`, как у выдачи и модели.** 429, 5xx и обрыв
связи адрес повторяет до `ATTEMPTS` раз с паузой по `Retry-After` (не дольше 30 с) или
1 и 2 с; запросы прохода идут не чаще `RATE_PER_MINUTE`. Что повторять, решает правило
поиска адресов (`provider.transient`): маркер раньше кода — квота и закрытая учётка
приезжают тем же 429, что частота, и не повторяются. 429 и после повторов — частота не
прошла: следующие адреса прохода бились бы в тот же предел, и проход встаёт словами.
Отказы 429 единиц не списывали (замер 22.09.2026, `okf/provider-refusals.md`): повторы
платят только за вердикт.

**202 и отказ домену** (`hunter_codes.py`, тест прогона сбора 14.10): «ещё проверяет» проход
переспрашивает тут же, бесплатно; 400 `blacklisted_domain` общий разбор называет
`ProviderDomainRefusedError` — отказ постоянный, и очистка отклоняет лида сразу, без повторов и
единиц.

Исключения и разбор маркеров отказа — общие с поиском адресов
(`contacts/provider.py`): сервис тот же, маркеры те же, вторую копию
их таблицы заводить нельзя.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlalchemy import ColumnElement, false, or_, select

from backend.config import contacts as contacts_cfg
from backend.config import sales as cfg
from backend.config.startup_checks import ConfigError
from backend.features.contacts.provider import (
    BASE_URL,
    ProviderBlockedError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    _raise_refusal,
    transient,
)
from backend.features.core.domain import GONE_STATUSES, MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP
from backend.features.sales import hunter_codes
from backend.features.sales.models import LeadStatus, SalesLeadModel, SalesThreadModel
from backend.shared.net.retry import RateLimiter, reason_of, unsent, with_retries

logger = logging.getLogger(__name__)

FIXTURE = "fixture"
LIVE = "live"
KNOWN = (FIXTURE, LIVE)
#: Почему лид, проверенный выдуманным проверяльщиком, не годен при живом — словами отчёта.
MADE_UP = "адрес проверен тестовым проверяльщиком — очистите базу заново"
#: Операция журнала расхода: платная проверка адреса, единица — проверка.
OPERATION = "sales_verify"

#: Попыток на адрес: 429, 5xx и обрыв связи бывают временными (`net/retry.with_retries`).
ATTEMPTS = 3
#: Запросов к Hunter в минуту на проход — с запасом ниже его предела частоты: проход идёт
#: в несколько потоков (`cleaning.VERIFY_CONCURRENCY`), а быстрый ответ по известному
#: адресу без ограничителя упирал бы проход в 429 на каждом следующем адресе.
RATE_PER_MINUTE = 150
#: 429 и после повторов с паузой — словами очистки и итога прохода.
TOO_OFTEN = (
    f"частота превышена (HTTP 429) и после {ATTEMPTS} попыток с паузой — "
    "проверка адресов встала до следующей очистки"
)
#: Отказы, после которых платная часть прохода встаёт: повтор следующим адресом ответа не
#: изменит. Прочие `ProviderError` — отказ одному адресу, проход идёт дальше.
STOPS: tuple[type[ProviderError], ...] = (
    ProviderQuotaError,
    ProviderBlockedError,
    ProviderRateLimitError,
)

#: Слово провайдера «не знаю»: адрес не подтверждён — к письмам не идёт (`unconfirmed.py`).
UNKNOWN = "unknown"
#: Через сколько дней очистка спросит снова про адрес с первым `unknown`. Hunter отвечает так и
#: временно (сервер лежал, отложил ответ) — такие заминки проходят за дни, неделя берёт их с
#: запасом; а база гипотезы при разгоне доменов уходит в письма неделями, и перепроверенный
#: лид успевает в свою очередь. Слова «через N дней» в консоли согласованы с семью.
RECHECK_DAYS = 7

#: Слово провайдера → доставляем ли. `None` — провайдер не знает: это вердикт,
#: а не отказ; слово остаётся в `verification_status`, к письмам лид не идёт.
DELIVERABLE_BY_STATUS: dict[str, bool | None] = {
    "valid": True,
    "accept_all": True,  # сервер принимает всё: ящик не подтверждён, но и не отвергнут
    "webmail": True,
    UNKNOWN: None,
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
        # Свой на проход: проверяльщик собирается на каждую очистку (`build_verifier`).
        self._limiter = RateLimiter(per_minute=RATE_PER_MINUTE)

    async def verify(self, email: str) -> Verdict:
        response = await hunter_codes.asked(lambda: self._get(email))  # 202 — переспросить тут же
        body = _json(response)
        code = response.status_code
        if code == httpx.codes.UNAUTHORIZED:
            raise _unauthorized(body, code)
        if body is not None and body.get("errors"):
            _raise_refusal(body, code)  # маркер раньше кода: 429 бывает закрытой учёткой
        if code == httpx.codes.TOO_MANY_REQUESTS:
            raise ProviderRateLimitError(TOO_OFTEN)  # повторы с паузой уже позади (`_get`)
        if code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise ProviderError(f"провайдер не ответил (HTTP {code}) — повторим следующей очисткой")
        if code == httpx.codes.ACCEPTED:
            raise ProviderError(hunter_codes.STILL_CHECKING)  # переспросы позади (`asked`)
        if body is None:
            raise ProviderError(
                f"ответ не разобран как JSON (HTTP {code}): {response.text[:120]!r}"
            )
        return _verdict(body, code)

    async def _get(self, email: str) -> httpx.Response:
        """Ответ Hunter — с повторами временных отказов общим `with_retries` по правилу поиска
        адресов (`provider.transient`): 429 с маркером квоты или закрытой учётки, 401 и прочие
        коды не повторяются. Последний ответ приходит как есть, его разбирает `verify`."""
        try:
            # Ключ идёт ЗАГОЛОВКОМ, а не параметром адреса: в адресе он утекал
            # бы в журналы прокси и в текст ошибки провайдера.
            return await with_retries(
                lambda: self._http.get(
                    f"{BASE_URL}/email-verifier",
                    params={"email": email},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=contacts_cfg.HUNTER_TIMEOUT_SEC,
                ),
                attempts=ATTEMPTS,
                topic="продажи: проверка адреса",
                limiter=self._limiter,
                retry_when=transient,
            )
        except httpx.HTTPError as exc:
            # Несобранный запрос — без цепочки причин: текст такого исключения
            # несёт заголовок целиком, а в нём ключ (`net.retry.reason_of`).
            error = ProviderError(f"сеть до провайдера не дошла: {reason_of(exc)}")
            raise error from None if unsent(exc) else exc


def _unauthorized(body: dict[str, Any] | None, code: int) -> ProviderBlockedError:
    """Ключ не принят. Код однозначен при любом маркере, а повторять бессмысленно: каждый
    следующий лид получил бы тот же отказ. Причина уходит в слова очистки лида и итог задачи
    на экране — без имени настройки; имя — строкой журнала: ключ меняет администратор."""
    said = f", {details}" if (details := _details(body)) else ""
    logger.warning(
        "продажи: ключ проверки адресов не принят — проверить CONTACTS_HUNTER_API_KEY",
        extra={"status": code, "details": details},
    )
    return ProviderBlockedError(
        f"ключ проверки адресов не принят провайдером (HTTP 401{said}) — ключ меняет администратор"
    )


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


def configured() -> str:
    """Какой проверяльщик задан — `fixture` или `live`. Негодная настройка — отказ словами
    (`ConfigError`) без сети: так её судят и сборка проверяльщика, и экран — до постановки
    очистки в очередь (`api/sales/clean.py`), а не в итоге задачи через минуты."""
    name = (cfg.VERIFIER_PROVIDER or "").strip().lower()
    if name == LIVE and not contacts_cfg.HUNTER_API_KEY:
        raise ConfigError(
            "SALES_VERIFIER_PROVIDER=live, а CONTACTS_HUNTER_API_KEY пуст. "
            "Заполнить ключ или вернуть fixture — без ключа проверка адресов не стартует"
        )
    if name not in KNOWN:
        raise ConfigError(
            f"SALES_VERIFIER_PROVIDER=«{cfg.VERIFIER_PROVIDER}» — такого проверяльщика нет. "
            f"Известные: {', '.join(KNOWN)}"
        )
    return name


def build_verifier(http: httpx.AsyncClient) -> EmailVerifier:
    """Проверяльщик по настройке. Отказ — на старте, до первого лида."""
    if configured() == LIVE:
        return HunterVerifier(http, api_key=contacts_cfg.HUNTER_API_KEY)
    logger.warning(
        "продажи: проверяльщик адресов — fixture, вердикты выдуманные: "
        "годится для проверки проводки, не для писем"
    )
    return FixtureVerifier()


def live() -> bool:
    """Задан ли живой проверяльщик — без проверки ключа: годность лида судят сборка,
    отправка и экран, а ключ — сборка проверяльщика (`configured`)."""
    return (cfg.VERIFIER_PROVIDER or "").strip().lower() == LIVE


def recheck(real: bool) -> ColumnElement[bool]:
    """Лиды `ready`, которых очистка возьмёт снова: выдуманный вердикт, а проверяльщик живой
    (`real`). При выдуманном — никто: его вердикты годны."""
    if not real:
        return false()
    return (
        SalesLeadModel.status == LeadStatus.READY
    ) & SalesLeadModel.verification_status.startswith(f"{FIXTURE}:")


#: Первое письмо ушло из очереди: ушло, уходит или вернулось отказом доставки.
WENT = (*GONE_STATUSES, MessageStatus.SENDING)


def _unknown(real: bool) -> ColumnElement[bool]:
    """Последний вердикт лида — `unknown` проверяльщика того же рода, что у прохода (`real` —
    живой: не выдуманный). Пустой вердикт сюда не входит, а не даёт NULL: `NOT` над ним потерял
    бы из очистки лидов без вердикта."""
    status = SalesLeadModel.verification_status
    made_up = status.startswith(f"{FIXTURE}:")
    return (
        status.is_not(None)
        & SalesLeadModel.verified_at.is_not(None)
        & status.endswith(f":{UNKNOWN}")
        & (~made_up if real else made_up)
    )


def waits(real: bool, now: datetime) -> ColumnElement[bool]:
    """Лиды с первым `unknown`, чей день перепроверки не настал: очистка их пока не берёт."""
    return _unknown(real) & (SalesLeadModel.verified_at > now - timedelta(days=RECHECK_DAYS))


def again(real: bool, now: datetime) -> ColumnElement[bool]:
    """Лиды `ready` с `unknown`, чей день перепроверки настал, — если первое письмо им не ушло:
    такой лид не пишется (`unconfirmed.first_letter`), и очистка решит его. Ушло — цепочка идёт,
    перепроверки нет: второй `unknown` снял бы лида посреди цепочки."""
    went = select(SalesThreadModel.lead_id).join(
        MessageModel, MessageModel.thread_id == SalesThreadModel.thread_id
    )
    went = went.where(
        SalesThreadModel.lead_id == SalesLeadModel.id,
        MessageModel.step == FIRST_STEP,
        MessageModel.status.in_(WENT),
    )
    return (
        (SalesLeadModel.status == LeadStatus.READY)
        & _unknown(real)
        & (SalesLeadModel.verified_at <= now - timedelta(days=RECHECK_DAYS))
        & ~went.exists()
    )


def due(real: bool, now: datetime) -> ColumnElement[bool]:
    """Кого берёт очистка: лидов `new`, кроме ждущих дня перепроверки `unknown` (`waits`), — и
    перепроверку: при живом проверяльщике (`real`) `ready` с выдуманным вердиктом, а `ready` с
    `unknown`, которым не писали, — в свой день (`again`). Одно условие у прохода и у числа
    «ждут очистки» на экране."""
    fresh = (SalesLeadModel.status == LeadStatus.NEW) & ~waits(real, now)
    return or_(fresh, recheck(real), again(real, now))


def unproven(lead: SalesLeadModel) -> str | None:
    """Почему адрес лида не проверен по-настоящему — словами; `None` — проверен или
    проверяльщик выдуманный: при `fixture` выдуманный вердикт годен, как было."""
    made_up = (lead.verification_status or "").startswith(f"{FIXTURE}:")
    return MADE_UP if made_up and live() else None
