"""Почему почта не доставляет письмо — видом причины и словами (`letters/delivery_trouble.py`).

Образцы — тексты настоящих серверов и платформы, а не одного письма: письмо №404 висело
на «error dialing remote address: dial tcp …:25: i/o timeout», а то же семейство пишут Gmail,
Outlook, Яндекс, Postfix, Exim и спам-фильтры — и не всегда по-английски. Адреса в образцах —
служебные: домены `example`, сети `192.0.2.0/24` и `2001:db8::/32` для документации.
"""

from __future__ import annotations

import pytest
from backend.features.letters.deferrals import attempts_word
from backend.features.letters.delivery_trouble import (
    SHOWN_TEXT,
    STORED_TEXT,
    WORDS,
    Trouble,
    reason_of,
    words_of,
)

#: Ответы почты по видам: отсрочки и отказы разных серверов, платформы и языков.
SAMPLES: list[tuple[Trouble, str]] = [
    # Сервер получателя не отвечает.
    (Trouble.UNREACHABLE, "error dialing remote address: dial tcp 192.0.2.10:25: i/o timeout"),
    (
        Trouble.UNREACHABLE,
        "error dialing remote address: dial tcp 192.0.2.10:25: connect: connection refused",
    ),
    (Trouble.UNREACHABLE, "dial tcp [2001:db8::1]:25: connect: no route to host"),
    (Trouble.UNREACHABLE, "dial tcp 192.0.2.10:25: connect: network is unreachable"),
    (Trouble.UNREACHABLE, "connect to mx.example.com[192.0.2.10]:25: Connection timed out"),
    (
        Trouble.UNREACHABLE,
        "lost connection with mx.example.com[192.0.2.10] while receiving the initial server "
        "greeting",
    ),
    (
        Trouble.UNREACHABLE,
        '451 4.4.0 Primary target IP address responded with: "421 4.4.1 Connection timed out." '
        "Attempted failover to alternate host, but that did not succeed.",
    ),
    # У домена нет почтового сервера.
    (
        Trouble.NO_MAIL_SERVER,
        "unable to get mx info: failed to get IPs from PTR record: lookup <nil>: "
        "unrecognized address",
    ),
    (
        Trouble.NO_MAIL_SERVER,
        "dial tcp: lookup mail.example.com on 127.0.0.53:53: no such host",
    ),
    (
        Trouble.NO_MAIL_SERVER,
        "Host or domain name not found. Name service error for name=example.com type=MX: "
        "Host not found, try again",
    ),
    (
        Trouble.NO_MAIL_SERVER,
        "DNS Error: DNS type 'mx' lookup of example.com responded with code NXDOMAIN Domain "
        "name not found: example.com",
    ),
    (
        Trouble.NO_MAIL_SERVER,
        "556 5.1.10 <user@example.com>: Recipient address rejected: Domain example.com does "
        "not accept mail (nullMX)",
    ),
    (Trouble.NO_MAIL_SERVER, "550 5.4.4 SMTPSEND.DNS.NonExistentDomain; nonexistent domain"),
    # «Повторите позже»: серый список, временный сбой, код класса 4.
    (Trouble.TRY_LATER, "400 try again later"),
    (
        Trouble.TRY_LATER,
        "450 4.2.0 <user@example.com>: Recipient address rejected: Greylisted, see "
        "http://greylist.example.com/help/example.com.html",
    ),
    (Trouble.TRY_LATER, "451 4.7.1 Greylisting in action, please come back later"),
    (Trouble.TRY_LATER, "451 Temporary local problem - please try later"),
    (Trouble.TRY_LATER, "451 4.7.500 Server busy. Please try again later from [192.0.2.1]."),
    (
        Trouble.TRY_LATER,
        "451 4.7.1 Sorry, the service is currently unavailable. Please come back later.",
    ),
    (Trouble.TRY_LATER, "451 4.3.0 Error: queue file write error"),
    (Trouble.TRY_LATER, "421 4.3.2 Service not available, closing transmission channel"),
    (Trouble.TRY_LATER, "451 Попробуйте позже"),
    (Trouble.TRY_LATER, "451 Bitte versuchen Sie es später noch einmal"),
    (Trouble.TRY_LATER, "451 Veuillez réessayer plus tard"),
    (Trouble.TRY_LATER, "451 Por favor, inténtelo de nuevo más tarde"),
    # Ящик получателя переполнен.
    (
        Trouble.MAILBOX_FULL,
        "452 4.2.2 The email account that you tried to reach is over quota. Please direct the "
        "recipient to https://support.example.com/mail/?p=OverQuotaTemp",
    ),
    (Trouble.MAILBOX_FULL, "552 5.2.2 Mailbox size limit exceeded"),
    (
        Trouble.MAILBOX_FULL,
        "554 5.2.2 mailbox full; STOREDRV.Deliver.Exception:QuotaExceededException",
    ),
    (
        Trouble.MAILBOX_FULL,
        "550 Message was not accepted -- invalid mailbox. Local mailbox user@example.com is "
        "unavailable: user is over quota",
    ),
    (Trouble.MAILBOX_FULL, "452 Dovecot: Quota exceeded (mailbox for user is full)"),
    (Trouble.MAILBOX_FULL, "452 Ящик переполнен"),
    (Trouble.MAILBOX_FULL, "552 Das Postfach des Empfängers ist voll"),
    (Trouble.MAILBOX_FULL, "452 Boîte aux lettres pleine"),
    (Trouble.MAILBOX_FULL, "452 4.2.2 Buzón lleno"),
    (Trouble.MAILBOX_FULL, "552 5.2.2 Casella piena"),
    # Сервер получателя ограничил частоту писем от нас.
    (
        Trouble.RATE_LIMITED,
        "421 Too many concurrent SMTP connections from this IP address; please try again later.",
    ),
    (
        Trouble.RATE_LIMITED,
        "421 4.7.28 [192.0.2.1 15] Our system has detected an unusual rate of unsolicited mail "
        "originating from your IP address. To protect our users from spam, mail sent from your "
        "IP address has been temporarily rate limited.",
    ),
    (
        Trouble.RATE_LIMITED,
        "450 4.2.1 The user you are trying to contact is receiving mail at a rate that prevents "
        "additional messages from being delivered.",
    ),
    (
        Trouble.RATE_LIMITED,
        "421 4.7.0 [TSS04] Messages from 192.0.2.1 temporarily deferred due to unexpected volume "
        "or user complaints - 4.16.55.1",
    ),
    (
        Trouble.RATE_LIMITED,
        "451 4.7.650 The mail server [192.0.2.1] has been temporarily rate limited due to IP "
        "reputation.",
    ),
    (
        Trouble.RATE_LIMITED,
        "450 4.2.1 The recipient has exceeded message rate limit. Try again later.",
    ),
    (Trouble.RATE_LIMITED, "421 Слишком много соединений с вашего адреса"),
    (Trouble.RATE_LIMITED, "421 Trop de connexions, veuillez verifier votre configuration."),
    # Сервер получателя отклоняет наш адрес или IP.
    (
        Trouble.BLOCKED,
        "554 5.7.1 Service unavailable; Client host [192.0.2.1] blocked using "
        "zen.spamhaus.example; https://www.spamhaus.example/query/ip/192.0.2.1",
    ),
    (Trouble.BLOCKED, "550 5.7.606 Access denied, banned sending IP [192.0.2.1]."),
    (
        Trouble.BLOCKED,
        "421 4.7.0 [192.0.2.1] Our system has detected that this message is suspicious due to "
        "the very low reputation of the sending IP address.",
    ),
    (
        Trouble.BLOCKED,
        "550 5.7.26 This mail has been blocked because the sender is unauthenticated. All "
        "senders must authenticate with either SPF or DKIM.",
    ),
    (Trouble.BLOCKED, "450 4.7.25 Client host rejected: cannot find your hostname, [192.0.2.1]"),
    (
        Trouble.BLOCKED,
        "421 4.7.0 [TS01] Messages from 192.0.2.1 temporarily deferred due to user complaints",
    ),
    (
        Trouble.BLOCKED,
        "550 5.7.1 Unfortunately, messages from [192.0.2.1] weren't sent. Please contact your "
        "Internet service provider since part of their network is on our block list (S3150).",
    ),
    (Trouble.BLOCKED, "550 5.7.1 Sender address rejected: Domain not found"),
    (Trouble.BLOCKED, "421 4.7.1 Temporarily rejected for policy reasons"),
    (Trouble.BLOCKED, "550 Ваш IP-адрес заблокирован"),
    (Trouble.BLOCKED, "550 Отправитель в чёрном списке"),
    # Сбой шифрования связи.
    (Trouble.TLS, "remote error: tls: handshake failure"),
    (
        Trouble.TLS,
        "tls: failed to verify certificate: x509: certificate has expired or is not yet valid",
    ),
    (Trouble.TLS, "530 5.7.0 Must issue a STARTTLS command first"),
    (
        Trouble.TLS,
        "450 4.4.317 Cannot connect to remote server [Message=451 5.7.3 STARTTLS is required "
        "to send mail]",
    ),
    # Ящика получателя нет или он отключён.
    (
        Trouble.NO_MAILBOX,
        "550 5.1.1 The email account that you tried to reach does not exist. Please try "
        "double-checking the recipient's email address for typos or unnecessary spaces.",
    ),
    (Trouble.NO_MAILBOX, "550 5.2.1 The email account that you tried to reach is disabled."),
    (
        Trouble.NO_MAILBOX,
        "554 delivery error: dd This user doesn't have a example.com account (user@example.com)",
    ),
    (Trouble.NO_MAILBOX, "552 1 Requested mail action aborted, mailbox not found"),
    (Trouble.NO_MAILBOX, "550 5.5.0 Requested action not taken: mailbox unavailable"),
    (
        Trouble.NO_MAILBOX,
        "550 5.1.10 RESOLVER.ADR.RecipientNotFound; Recipient not found by SMTP address lookup",
    ),
    (
        Trouble.NO_MAILBOX,
        "550 5.1.1 <user@example.com>: Recipient address rejected: User unknown in local "
        "recipient table",
    ),
    (Trouble.NO_MAILBOX, "550 No Such User Here"),
    (Trouble.NO_MAILBOX, "500 unknown recipient"),
    (Trouble.NO_MAILBOX, "554 5.7.1 <user@example.com>: Relay access denied"),
    (Trouble.NO_MAILBOX, "550 5.7.1 Пользователь не найден"),
    (Trouble.NO_MAILBOX, "550 5.1.1 <user@example.com>: Empfänger unbekannt"),
    (Trouble.NO_MAILBOX, "550 5.1.1 Utilisateur inconnu"),
    (Trouble.NO_MAILBOX, "550 5.1.1 El usuario no existe"),
    # Сервер получателя счёл письмо спамом.
    (
        Trouble.SPAM,
        "550 5.7.1 [192.0.2.1] Our system has detected that this message is likely unsolicited "
        "mail. To reduce the amount of spam, this message has been blocked.",
    ),
    (Trouble.SPAM, "554 5.7.1 [2] Message rejected under suspicion of SPAM"),
    (
        Trouble.SPAM,
        "552 5.7.0 This message was blocked because its content presents a potential security "
        "issue.",
    ),
    (Trouble.SPAM, "550 5.7.350 Remote server returned message detected as spam"),
    (Trouble.SPAM, "554 Письмо похоже на спам"),
    (Trouble.SPAM, "Spam Content"),
    # Платформа не отправляла сама (`dropped`).
    (Trouble.SUPPRESSED, "Bounced Address"),
    (Trouble.SUPPRESSED, "Unsubscribed Address"),
    (Trouble.SUPPRESSED, "Spam Reporting Address"),
    # Платформа перестала повторять.
    (Trouble.EXPIRED, "550 4.4.7 QUEUE.Expired; message expired"),
    (Trouble.EXPIRED, "retry timeout exceeded"),
    # Не узнано — ответ почты как есть.
    (Trouble.UNKNOWN, "550 There was a problem"),
    (Trouble.UNKNOWN, "Recipient List over Package Quota"),
    (Trouble.UNKNOWN, "Invalid SMTPAPI header"),
]


@pytest.mark.parametrize(("kind", "text"), SAMPLES)
def test_each_answer_is_named_by_its_kind(kind: Trouble, text: str) -> None:
    found = reason_of(text)

    assert found.kind is kind, found.words
    if kind is not Trouble.UNKNOWN:
        assert found.words.startswith(WORDS[kind])


def test_every_kind_has_samples() -> None:
    """Семейство целиком: у каждого вида — свои образцы, а не один на всех."""
    assert {kind for kind, _ in SAMPLES} == set(Trouble)


class TestWords:
    def test_the_404_case_reads_as_words_with_a_short_code(self) -> None:
        said = reason_of("error dialing remote address: dial tcp 192.0.2.10:25: i/o timeout")

        assert said.words == "сервер получателя не отвечает (порт 25, тайм-аут)"
        assert said.text == "error dialing remote address: dial tcp 192.0.2.10:25: i/o timeout"

    @pytest.mark.parametrize(
        ("text", "words"),
        [
            (
                "dial tcp [2001:db8::1]:25: connect: no route to host",
                "сервер получателя не отвечает (порт 25, нет маршрута)",
            ),
            (
                "connect to mx.example.com[192.0.2.10]:25: Connection refused",
                "сервер получателя не отвечает (порт 25, соединение отклонено)",
            ),
            (
                "452 4.2.2 The email account that you tried to reach is over quota.",
                "ящик получателя переполнен (4.2.2)",
            ),
            (
                "450 4.2.0 <user@example.com>: Recipient address rejected: Greylisted",
                "сервер получателя просит повторить позже (серый список, 4.2.0)",
            ),
            ("remote error: tls: handshake failure", WORDS[Trouble.TLS] + " (TLS)"),
            (
                "Bounced Address",
                "платформа не отправляла: адрес в её стоп-листе (раньше отказывал)",
            ),
            ("Spam Reporting Address", WORDS[Trouble.SUPPRESSED] + " (жаловался на спам)"),
            ("550 There was a problem", "ответ почты: «550 There was a problem»"),
        ],
    )
    def test_hint_is_what_exactly_happened(self, text: str, words: str) -> None:
        assert reason_of(text).words == words

    def test_status_names_the_kind_when_there_are_no_words(self) -> None:
        """Отсрочка без текста, но с кодом события: вид — по коду, код — в скобках."""
        said = reason_of(None, status="4.4.1")

        assert (said.kind, said.words) == (
            Trouble.UNREACHABLE,
            f"{WORDS[Trouble.UNREACHABLE]} (4.4.1)",
        )

    @pytest.mark.parametrize(
        ("code", "kind"),
        [
            ("5.1.1", Trouble.NO_MAILBOX),
            ("5.1.2", Trouble.NO_MAIL_SERVER),
            ("4.2.2", Trouble.MAILBOX_FULL),
            ("4.4.2", Trouble.UNREACHABLE),
            ("5.7.1", Trouble.BLOCKED),
            ("4.4.7", Trouble.EXPIRED),
            ("4.3.5", Trouble.TRY_LATER),
            ("5.0.0", Trouble.UNKNOWN),
        ],
    )
    def test_enhanced_code_is_the_fallback(self, code: str, kind: Trouble) -> None:
        assert reason_of(f"{code} ok").kind is kind

    @pytest.mark.parametrize(
        ("text", "kind"),
        [
            ("452 ok", Trouble.TRY_LATER),
            ("552 ok", Trouble.MAILBOX_FULL),
            ("553 ok", Trouble.NO_MAILBOX),
            ("554 ok", Trouble.UNKNOWN),
        ],
    )
    def test_basic_code_is_the_last_fallback(self, text: str, kind: Trouble) -> None:
        assert reason_of(text).kind is kind

    def test_an_address_is_not_a_code(self) -> None:
        """«45.4.3.21» — адрес, а не код 4.3.21: «повторите позже» по нему было бы выдумкой."""
        assert reason_of("host 45.4.3.21 said ok").kind is Trouble.UNKNOWN

    def test_nothing_said_is_said_so(self) -> None:
        assert reason_of(None).words == "причину почта не назвала"
        assert reason_of("отказ доставки").words == "причину почта не назвала"

    def test_unknown_text_is_cut_and_stored_text_fits_the_column(self) -> None:
        long = "550 " + "x" * 400
        said = reason_of(f"  {long}\n  tail  ")

        assert len(said.text) == STORED_TEXT
        quoted = said.words.removeprefix("ответ почты: «").removesuffix("»")
        assert len(quoted) == SHOWN_TEXT
        assert quoted.endswith("…")

    def test_multiline_answer_is_one_line(self) -> None:
        said = reason_of("550-5.1.1 The email account\n550-5.1.1 does not exist")

        assert said.text == "550-5.1.1 The email account 550-5.1.1 does not exist"
        assert said.kind is Trouble.NO_MAILBOX

    def test_words_of_a_stored_kind_rebuild_from_its_text(self) -> None:
        assert words_of(Trouble.UNREACHABLE, "dial tcp 192.0.2.10:25: i/o timeout") == (
            "сервер получателя не отвечает (порт 25, тайм-аут)"
        )


@pytest.mark.parametrize(
    ("count", "said"),
    [
        (1, "1 попытка"),
        (2, "2 попытки"),
        (5, "5 попыток"),
        (11, "11 попыток"),
        (14, "14 попыток"),
        (21, "21 попытка"),
        (22, "22 попытки"),
        (112, "112 попыток"),
    ],
)
def test_attempts_are_counted_in_words(count: int, said: str) -> None:
    assert attempts_word(count) == said
