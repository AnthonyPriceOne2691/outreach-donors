"""Почему ответ — автоответ (`classify.Verdict.reason` → `replies.kind_reason`, 27.11.2026).

Заголовков письма в базе нет: спорный автоответ (по тексту — человек, по подписи автомата — нет)
проверить было нечем. Причина — название сработавшего правила, а у подписи автомата — ещё и
заголовок со значением из нашего списка. Проверяется: обе причины, их отсутствие у других видов,
что в причину не попадает ни строка письма, ни адрес, и что любая возможная причина влезает в
колонку. Что кладёт приём — `test_replies_inbox.py::TestKindReason`, переразбор —
`test_replies_relabel.py`, карточка переписки — `test_reply_auto_sum.py`.
"""

from __future__ import annotations

import pytest
from backend.features.core.domain import ReplyKind
from backend.features.core.models.outreach import KIND_REASON_LENGTH
from backend.features.replies import classify
from backend.features.replies.inbound import Incoming

OUR_SUBJECT = "Re: Guest article on donor.test"


def reply(
    text: str,
    *,
    subject: str = OUR_SUBJECT,
    headers: dict[str, str] | None = None,
    sender: str = "editor@donor.test",
) -> Incoming:
    return Incoming(
        message_id="<reply-1@donor.test>",
        to=("anna+m417.7d3a91c2e5@replies.ours.test",),
        from_email=sender,
        subject=subject,
        text=text,
        headers=headers or {},
    )


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"Precedence": "bulk"}, "автоответчик по заголовкам (Precedence: bulk)"),
        ({"Precedence": "Auto_Reply"}, "автоответчик по заголовкам (Precedence: auto_reply)"),
        (
            {"Auto-Submitted": "auto-replied (vacation)"},
            "автоответчик по заголовкам (Auto-Submitted: auto-replied)",
        ),
        ({"X-Autoreply": "yes"}, "автоответчик по заголовкам (X-Autoreply)"),
        ({"X-Autorespond": "on"}, "автоответчик по заголовкам (X-Autorespond)"),
    ],
)
def test_a_machine_signature_names_the_header_and_its_value(
    headers: dict[str, str], reason: str
) -> None:
    verdict = classify.classify(reply("Thanks, we will get back to you.", headers=headers))

    assert verdict.kind is ReplyKind.AUTO_REPLY
    assert verdict.rule == classify.BY_HEADERS
    assert verdict.reason == reason


@pytest.mark.parametrize(
    ("text", "extra", "reason"),
    [
        ("I am out of the office until Monday.", {}, "автоответчик по тексту"),
        ("Thanks.", {"subject": "Automatic reply: Guest article"}, "автоответчик по теме"),
        (
            "We have received your request. Ticket #48213. Our team will reply soon.",
            {},
            "подтверждение заявки",
        ),
        (
            "Notification about your account settings.",
            {"sender": "noreply@site.test"},
            "письмо робота без суммы и файла",
        ),
    ],
)
def test_words_name_the_rule_alone(text: str, extra: dict[str, str], reason: str) -> None:
    verdict = classify.classify(reply(text, **extra))  # type: ignore[arg-type]

    assert verdict.kind is ReplyKind.AUTO_REPLY
    assert verdict.sign is None
    assert verdict.reason == reason


@pytest.mark.parametrize(
    ("text", "headers", "kind"),
    [
        ("We charge 250 EUR per post.", {}, ReplyKind.HUMAN),
        ("Please remove me from your list.", {}, ReplyKind.UNSUBSCRIBE),
        (
            "Delivery has failed to these recipients.",
            {"X-Failed-Recipients": "a@b.test"},
            ReplyKind.BOUNCE,
        ),
    ],
)
def test_other_kinds_have_no_reason(text: str, headers: dict[str, str], kind: ReplyKind) -> None:
    verdict = classify.classify(reply(text, headers=headers))

    assert verdict.kind is kind
    assert verdict.reason is None


def test_the_reason_never_carries_the_letter() -> None:
    """В причину идут только наши слова: значение заголовка с адресом и текстом не переносится."""
    headers = {"Precedence": "bulk; list=<editor@donor.test> Out of office: back Monday"}

    verdict = classify.classify(reply("Out of office: back Monday", headers=headers))

    assert verdict.reason == "автоответчик по заголовкам (Precedence: bulk)"
    assert verdict.reason is not None
    assert "@" not in verdict.reason
    assert "Monday" not in verdict.reason


def test_every_possible_reason_fits_the_column() -> None:
    signs = [
        f"{name}: {value}" if value else name
        for name, values in classify._MACHINE_SIGNS
        for value in values
    ]
    reasons = [
        classify.Verdict(ReplyKind.AUTO_REPLY, rule=name, sign=sign).reason
        for name, kind, _ in classify.RULES
        if kind is ReplyKind.AUTO_REPLY
        for sign in ([*signs] if name == classify.BY_HEADERS else [None])
    ]

    assert reasons
    assert all(reason is not None and len(reason) <= KIND_REASON_LENGTH for reason in reasons)
