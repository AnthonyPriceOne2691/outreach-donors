"""Продажи: домен IDN лида в письме, карточке телемаркетологу и отчёте недели — по-человечески.

18.11.2026 на проде у продаж все 72 письма 25 лидам с кириллическим доменом ушли с `xn--` в теме
или тексте: `{{site}}` подставлял имя из базы в ASCII. Письмо — через общую подстановку
(`compose._substitute`, проверки — `test_letters_idn_hosts.py`): здесь — что шаблон продаж её
проходит. В Kommo сайт уходит ссылкой — там ASCII. Домены и люди выдуманы.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.features.sales import handoff_text as wording
from backend.features.sales import letter
from backend.features.sales.chain_text import step_template
from backend.features.sales.handoff_text import Card
from backend.features.sales.weekly_counts import Handed
from backend.features.sales.weekly_text import lead_line

ACE = "xn--e1afmkfd.xn--p1ai"
SHOWN = "пример.рф"
BODY = (
    "[greeting] rewrite\nHello {{name}},\n\n"
    "[opening] rewrite\nThis is a made-up test opening about {{site}} for {{company}} only.\n\n"
    "[offer] fixed\nTest offer: nothing real is sold here, see https://{{site}}/catalog."
)


def test_sales_letter_names_the_site_as_written_and_keeps_links_ascii() -> None:
    template = step_template(step=1, language="en", subject="Test question for {{site}}", body=BODY)
    values = {"name": "Анна", "company": "Пример", "site": ACE}

    out = letter.rendered(template, values)
    text = "\n".join(zone.text for zone in out.zones)

    assert out.subject == f"Test question for {SHOWN}"
    assert f"about {SHOWN} for" in text
    assert f"https://{ACE}/catalog" in text
    assert ACE not in out.subject


def _card() -> Card:
    return Card(
        thread_id=7,
        lead_id=3,
        email=f"anna@{ACE}",
        name="Анна",
        company="Пример",
        site=ACE,
        hypothesis="Гипотеза",
        country="RU",
        language="ru",
        reply_id=None,
        reply_at=None,
        reply_subject="",
        reply_text="",
        sent=1,
        replies=0,
        first_sent_at=None,
    )


def test_handoff_note_shows_the_site_and_address_as_written_kommo_gets_ascii() -> None:
    card = _card()
    note = wording.note(card)

    assert f"Компания: Пример · {SHOWN}" in note
    assert f"От: anna@{SHOWN}" in note
    assert ACE not in note
    # В Kommo сайт уходит ссылкой (`https://…`), адрес — для письма: ASCII.
    assert card.new_lead.site == ACE


def test_weekly_line_shows_the_site_and_address_as_written() -> None:
    lead = Handed(
        hypothesis_id=1,
        hypothesis="Гипотеза",
        company="Пример",
        site=ACE,
        name="Анна",
        email=f"anna@{ACE}",
        link="https://service.example.test/threads/7",
        at=datetime(2026, 11, 18, 10, 0, tzinfo=UTC),
    )

    line = lead_line(lead)

    assert line.startswith(f"Пример ({SHOWN}) · Анна · anna@{SHOWN}")
    assert ACE not in line
