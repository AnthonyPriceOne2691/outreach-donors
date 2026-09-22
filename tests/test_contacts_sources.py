"""Источники адресов: платный сервис, RDAP и проверка MX.

Каждый вид ответа провайдера сначала воспроизводится здесь, потом чинится
в коде. Через месяц никто не вспомнит, почему в разборе стоит то или иное
условие, — а тест помнит.
"""

from __future__ import annotations

from typing import Any

import dns.exception
import dns.resolver
import httpx
import pytest
from backend.features.contacts import mx as mx_module
from backend.features.contacts import rdap
from backend.features.contacts.mx import MailRoute, mail_route
from backend.features.contacts.provider import (
    HunterProvider,
    ProviderBlockedError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
)

pytestmark = pytest.mark.asyncio


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _reply(payload: dict[str, Any], status: int = 200) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


class TestPaidProvider:
    async def test_quota_comes_from_the_provider(self) -> None:
        """Остаток спрашивается у сервиса: ключ общий с соседней системой,
        и своя таблица расхода про её траты ничего не знает."""
        payload = {"data": {"requests": {"searches": {"used": 40, "available": 100}}}}

        async with _http(_reply(payload)) as client:
            quota = await HunterProvider(client, api_key="k").quota()

        assert quota.left == 60

    async def test_addresses_are_read_with_confidence(self) -> None:
        payload = {
            "data": {
                "emails": [
                    {"value": "Editor@Site.com", "confidence": 92},
                    {"value": "guess@site.com", "confidence": 30},
                ]
            }
        }

        async with _http(_reply(payload)) as client:
            found = await HunterProvider(client, api_key="k").find_emails("site.com")

        assert [c.email for c in found] == ["editor@site.com"]
        assert found[0].confidence == 92

    async def test_quota_exceeded_is_its_own_outcome(self) -> None:
        payload = {"errors": [{"id": "usage_exceeded", "code": 403, "details": "кончилось"}]}

        async with _http(_reply(payload, 403)) as client:
            with pytest.raises(ProviderQuotaError):
                await HunterProvider(client, api_key="k").find_emails("site.com")

    async def test_rate_limit_is_its_own_outcome(self) -> None:
        payload = {"errors": [{"id": "too_many_requests", "code": 429, "details": "позже"}]}

        async with _http(_reply(payload, 429)) as client:
            with pytest.raises(ProviderRateLimitError):
                await HunterProvider(client, api_key="k").find_emails("site.com")

    async def test_restricted_account_is_not_a_rate_limit(self) -> None:
        """Живой отказ Hunter 22.09.2026: закрытая учётка приезжает
        с кодом 429, то есть по числу неотличима от «слишком часто».

        Разница не косметическая: `rate_limited` говорит лестнице
        «повторим позже», и ступень повторялась бы вечно, потому что
        повтор здесь не лечит ничего. Тело ответа — дословно то, что
        пришло от провайдера.
        """
        payload = {
            "errors": [
                {
                    "id": "restricted_account",
                    "code": 429,
                    "details": "Your account was restricted. Please log in to Hunter for more information.",
                }
            ]
        }

        async with _http(_reply(payload, 429)) as client:
            with pytest.raises(ProviderBlockedError, match="зайти в кабинет"):
                await HunterProvider(client, api_key="k").find_emails("site.com")

    async def test_unknown_marker_is_not_guessed_as_transient(self) -> None:
        """Незнакомый маркер на 429 не выдаётся за превышенную частоту:
        именно такая догадка и прятала закрытую учётку."""
        payload = {"errors": [{"id": "some_new_marker", "code": 429, "details": "что-то"}]}

        async with _http(_reply(payload, 429)) as client:
            with pytest.raises(ProviderError, match="some_new_marker") as caught:
                await HunterProvider(client, api_key="k").find_emails("site.com")
        assert not isinstance(caught.value, ProviderRateLimitError)

    async def test_wrong_key_is_loud(self) -> None:
        payload = {"errors": [{"id": "wrong_token", "code": 401, "details": "ключ не принят"}]}

        async with _http(_reply(payload, 401)) as client:
            with pytest.raises(ProviderError, match="401"):
                await HunterProvider(client, api_key="k").find_emails("site.com")

    async def test_changed_format_is_loud_not_empty(self) -> None:
        """«Мы не поняли ответ» обязано быть громким: молчаливая пустота
        выглядела бы как «сервис перестал находить адреса»."""

        async with _http(_reply({"data": {"results": []}})) as client:
            with pytest.raises(ProviderError, match="нет списка адресов"):
                await HunterProvider(client, api_key="k").find_emails("site.com")

    async def test_not_json_is_loud(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>заглушка прокси</html>")

        async with _http(handler) as client:
            with pytest.raises(ProviderError, match="JSON"):
                await HunterProvider(client, api_key="k").quota()

    async def test_missing_key_says_what_to_do(self) -> None:
        async with _http(_reply({})) as client:
            with pytest.raises(ProviderError, match="CONTACTS_HUNTER_API_KEY"):
                await HunterProvider(client, api_key="").find_emails("site.com")

    async def test_empty_answer_is_legal(self) -> None:
        async with _http(_reply({"data": {"emails": []}})) as client:
            assert await HunterProvider(client, api_key="k").find_emails("site.com") == []


class TestRdap:
    async def test_owner_address(self) -> None:
        payload = {
            "entities": [
                {
                    "roles": ["registrant"],
                    "vcardArray": ["vcard", [["email", {}, "text", "Owner@Site.com"]]],
                }
            ]
        }

        async with _http(_reply(payload)) as client:
            found = await rdap.find_emails(client, "site.com")

        assert [c.email for c in found] == ["owner@site.com"]

    async def test_registrar_contact_is_skipped(self) -> None:
        """Письмо в abuse@ регистратора до владельца сайта не дойдёт."""
        payload = {
            "entities": [
                {
                    "roles": ["abuse", "registrar"],
                    "vcardArray": ["vcard", [["email", {}, "text", "abuse@registrar.com"]]],
                }
            ]
        }

        async with _http(_reply(payload)) as client:
            assert await rdap.find_emails(client, "site.com") == []

    async def test_nested_entity_is_found(self) -> None:
        payload = {
            "entities": [
                {
                    "roles": ["technical"],
                    "entities": [
                        {
                            "roles": ["registrant"],
                            "vcardArray": ["vcard", [["email", {}, "text", "owner@site.com"]]],
                        }
                    ],
                }
            ]
        }

        async with _http(_reply(payload)) as client:
            found = await rdap.find_emails(client, "site.com")

        assert [c.email for c in found] == ["owner@site.com"]

    async def test_unknown_domain_is_empty_not_an_error(self) -> None:
        async with _http(_reply({"errorCode": 404}, 404)) as client:
            assert await rdap.find_emails(client, "site.com") == []

    async def test_broken_answer_is_loud(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="не json")

        async with _http(handler) as client:
            with pytest.raises(rdap.RdapUnavailableError):
                await rdap.find_emails(client, "site.com")

    async def test_odd_vcard_shape_does_not_crash_the_run(self) -> None:
        payload = {"entities": [{"roles": ["registrant"], "vcardArray": "не список"}]}

        async with _http(_reply(payload)) as client:
            assert await rdap.find_emails(client, "site.com") == []


class _FakeResolver:
    """Ответчик DNS: словарь «тип записи → что вернуть или чем упасть»."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.timeout = 0.0
        self.lifetime = 0.0
        self.asked: list[str] = []

    async def resolve(self, host: str, rdtype: str) -> Any:
        self.asked.append(rdtype)
        result = self.answers.get(rdtype)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise dns.resolver.NoAnswer
        return result


@pytest.fixture(autouse=True)
def _forget_resolver_health() -> Any:
    """Память о мёртвом системном резолвере живёт на процесс — между
    тестами её надо забывать, иначе они начинают зависеть от порядка."""
    mx_module.health.reset()
    yield
    mx_module.health.reset()


@pytest.fixture
def dns_answers(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(*answers: dict[str, Any]) -> list[_FakeResolver]:
        """Резолверы по порядку: первый — системный, дальше запасные."""
        resolvers = [_FakeResolver(a) for a in answers]
        monkeypatch.setattr(
            "backend.features.contacts.mx._resolvers", lambda _timeout: list(resolvers)
        )
        return resolvers

    return install


class TestMailRoute:
    async def test_mx_record(self, dns_answers: Any) -> None:
        dns_answers({"MX": ["mx1.site.com"]})
        assert await mail_route("site.com") is MailRoute.MX

    async def test_no_mx_but_a_record_still_takes_mail(self, dns_answers: Any) -> None:
        """По RFC 5321 при отсутствии MX почта идёт на адрес из A.
        Выбросив такие домены, мы потеряли бы часть базы на ровном месте."""
        system, *_ = dns_answers({"MX": None, "A": ["1.2.3.4"]})
        assert await mail_route("site.com") is MailRoute.IMPLICIT
        assert system.asked == ["MX", "A"]

    async def test_nothing_at_all(self, dns_answers: Any) -> None:
        dns_answers({"MX": None, "A": None})
        assert await mail_route("site.com") is MailRoute.NONE

    async def test_domain_does_not_exist(self, dns_answers: Any) -> None:
        dns_answers({"MX": dns.resolver.NXDOMAIN()})
        assert await mail_route("site.com") is MailRoute.NONE

    async def test_dns_failure_is_unknown_not_refusal(self, dns_answers: Any) -> None:
        """Отсутствие данных — не отказ: домен идёт дальше по лестнице."""
        dns_answers({"MX": dns.exception.Timeout()})
        assert await mail_route("site.com") is MailRoute.UNKNOWN


class TestResolverFallback:
    """Боевой прогон нашёл то, чего не видели заглушки: системный резолвер
    машины был настроен на адреса без маршрута, и ступень возвращала
    «неизвестно» по всем доменам, стоя при этом таймаута на каждом."""

    async def test_spare_resolver_answers_when_the_system_one_is_silent(
        self, dns_answers: Any
    ) -> None:
        system, spare = dns_answers(
            {"MX": dns.exception.Timeout()},
            {"MX": ["mx1.site.com"]},
        )
        assert await mail_route("site.com") is MailRoute.MX
        assert system.asked == ["MX"]
        assert spare.asked == ["MX"]

    async def test_unknown_only_when_every_resolver_is_silent(self, dns_answers: Any) -> None:
        dns_answers({"MX": dns.exception.Timeout()}, {"MX": dns.exception.Timeout()})
        assert await mail_route("site.com") is MailRoute.UNKNOWN

    async def test_dead_system_resolver_stops_being_asked(self, dns_answers: Any) -> None:
        """Иначе каждый домен платит полным таймаутом за известный отказ:
        на сотне доменов это восемь минут ожидания ни за чем."""
        dns_answers({"MX": dns.exception.Timeout()}, {"MX": ["mx1.site.com"]})

        for _ in range(mx_module.SYSTEM_FAILURES_BEFORE_SKIP):
            assert await mail_route("site.com") is MailRoute.MX

        assert not mx_module.health.system_is_trusted

    async def test_a_successful_answer_restores_trust(self, dns_answers: Any) -> None:
        dns_answers({"MX": dns.exception.Timeout()}, {"MX": ["mx1.site.com"]})
        await mail_route("site.com")
        assert mx_module.health.failures == 1

        dns_answers({"MX": ["mx1.site.com"]})
        await mail_route("site.com")
        assert mx_module.health.failures == 0
