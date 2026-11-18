"""Домены IDN в тексте — письменностью сайта, в ссылке и адресе — ASCII (18.11.2026).

База хранит имена хостов в ASCII: «пример.рф» — `xn--e1afmkfd.xn--p1ai`. На проде у продаж все 72
письма 25 лидам с кириллическим доменом ушли с `xn--` в теме или тексте: `{{site}}` подставлял имя
из базы. Здесь — раскодировщик (`shared/net/idn.py`) и подстановка писем (`compose._substitute`,
общая у доноров, рекламодателей и продаж): в тексте имя раскодировано, в ссылке и в адресе почты —
ASCII; слова переадресации на экране — тоже по-человечески. Домены выдуманы.
"""

from __future__ import annotations

import pytest
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.letters import compose
from backend.features.letters.template import Template, Zone, ZoneKind
from backend.features.replies.lead_redirect import State
from backend.features.replies.redirect_shown import words_of
from backend.shared.net.idn import has_ace, shown_address, shown_host

ACE = "xn--e1afmkfd.xn--p1ai"
SHOWN = "пример.рф"


class TestShownHost:
    @pytest.mark.parametrize(
        ("host", "shown"),
        [
            (ACE, SHOWN),
            ("XN--E1AFMKFD.XN--P1AI", SHOWN),
            (f"www.{ACE}", f"www.{SHOWN}"),
            ("xn--80ai1b.example.test", "сад.example.test"),
            ("xn--r8jz45g.xn--zckzah", "例え.テスト"),
            ("xn--strae-oqa.example.test", "straße.example.test"),
        ],
    )
    def test_ace_name_is_shown_as_written(self, host: str, shown: str) -> None:
        assert has_ace(host)
        assert shown_host(host) == shown

    @pytest.mark.parametrize(
        "host",
        [
            "Shop.Example.COM",  # без `xn--` — как есть, и регистр тоже
            SHOWN,  # уже письменностью сайта
            "",
            "xn--zz-bad.xn--p1ai",  # битая метка: `xn--…` лучше, чем ничего
        ],
    )
    def test_other_names_stay_as_they_are(self, host: str) -> None:
        assert shown_host(host) == host

    def test_address_decodes_only_the_domain(self) -> None:
        assert shown_address(f"Info.Desk@{ACE}") == f"Info.Desk@{SHOWN}"
        assert shown_address("без-собаки") == "без-собаки"
        assert shown_address("info@shop.example.test") == "info@shop.example.test"


def _template(text: str, subject: str = "Про {{host}}") -> Template:
    return Template(subject=subject, zones=(Zone(name="body", kind=ZoneKind.FIXED, text=text),))


def _values(**more: str) -> dict[str, str]:
    return {
        "sender_name": "Отправитель",
        "postal_address": "Адрес",
        "unsubscribe_url": "https://u.test/x",
    } | more


class TestSubstitute:
    def test_text_gets_the_name_as_written_link_and_address_keep_ascii(self) -> None:
        text = (
            "Здравствуйте, команда {{host}}! Страница https://{{host}}/price, "
            '<a href="//{{host}}">сайт</a>, почта info@{{host}} и ({{host}}).'
        )
        out = compose.render(_template(text), _values(host=ACE))

        assert out.subject == f"Про {SHOWN}"
        assert out.zones[0].text == (
            f"Здравствуйте, команда {SHOWN}! Страница https://{ACE}/price, "
            f'<a href="//{ACE}">сайт</a>, почта info@{ACE} и ({SHOWN}).'
        )

    @pytest.mark.parametrize("name", ["donor_host", "example_host", "site"])
    def test_every_host_placeholder_is_shown_as_written(self, name: str) -> None:
        out = compose.render(
            _template(f"Площадка {{{{{name}}}}}.", subject="Тема"), _values(**{name: ACE})
        )
        assert out.zones[0].text == f"Площадка {SHOWN}."

    def test_ascii_hosts_and_other_values_do_not_change(self) -> None:
        values = _values(host="Shop.Example.test", anchor=ACE)
        out = compose.render(_template("{{host}} и {{anchor}}", subject="Тема"), values)
        # Анкор — не имя сайта: подставляется как есть, даже если похож на домен.
        assert out.zones[0].text == f"Shop.Example.test и {ACE}"

    def test_values_stay_ascii_for_everyone_who_takes_them(self) -> None:
        values = compose.values_for(host=ACE, domain_id=None)
        assert values["host"] == ACE


class TestRedirectWords:
    def test_words_show_the_address_domain_as_written_and_keep_the_field(self) -> None:
        row = LeadRedirectModel(
            reply_id=1,
            thread_id=1,
            state=State.REFUSED,
            address=f"sales@{ACE}",
            reason="ящик не наш",
        )
        shown = words_of(row, None)

        assert f"sales@{SHOWN}" in shown.words
        assert ACE not in shown.words
        # Поле адреса — как в базе: по нему ищут и пишут.
        assert shown.address == f"sales@{ACE}"
