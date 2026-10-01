"""Каналы связи по ревью #118: код, служебный адрес и соседняя цифра — не контакт.

Каналы уходят в выгрузку прогона по файлу колонками «явные» и «угаданные»,
и по ним пишут люди. Поэтому главное здесь — обратная сторона находки:
CSS, скрипт, пиксель, раздел соцсети и часы работы каналом не становятся.
Рядом с каждой такой проверкой стоит законная форма того же канала: правка,
которая убирает мусор ценой настоящих контактов, — не правка.
"""

from __future__ import annotations

import time

import pytest
from backend.features.contacts.messengers import (
    MessengerKind,
    extract_handle_guesses,
    extract_handles,
    harvest_handles,
)
from backend.features.core.domain import PageKind


def _values(html: str, kind: MessengerKind) -> set[str]:
    return {handle.value for handle in extract_handles(html) if handle.kind is kind}


def _guessed(html: str, kind: MessengerKind) -> set[str]:
    return {handle.value for handle in extract_handle_guesses(html) if handle.kind is kind}


class TestExplicitMeansDeclared:
    """Находка 6: явным считается то, что сайт объявил ссылкой, а не код рядом.

    До правки явные правила шли по сырому HTML вместе со стилями и скриптами,
    без левой границы у схем и без проверки того, что стоит в пути. Сайт без
    единого контакта, но с иконками соцсетей и пикселем ВКонтакте, выходил
    с колонками skype=`before; hover`, vk=`rtrg` — и считался «с мессенджером».
    """

    @pytest.mark.parametrize(
        "html",
        [
            # Иконки соцсетей: псевдокласс за именем класса — по форме схема `skype:`.
            "<style>.fa-skype:before{content:'x'} .social a.skype:hover{color:red}</style>",
            # Минифицированный скрипт: ключ объекта и тернарный оператор.
            "<script>var n={skype:t.skype};x=n?skype:t.skype</script>",
            # Слово, которое кончается на схему: номер брони — не телефон.
            '<a href="https://site.com/booking/hotel:7654321">бронь</a>',
        ],
    )
    def test_code_around_a_scheme_is_not_a_channel(self, html: str) -> None:
        assert extract_handles(html) == set()

    def test_vk_pixel_is_not_a_community(self) -> None:
        """Стандартный пиксель ретаргетинга ВКонтакте стоит на половине рунета."""
        html = (
            '<noscript><img src="https://vk.com/rtrg?p=VK-RTRG-123456-aBcDe"'
            ' style="position:fixed;left:-999px;" alt=""/></noscript>'
        )
        assert _values(html, MessengerKind.VK) == set()

    @pytest.mark.parametrize(
        "path",
        [
            "photo-1_2",
            "topic-1_2",
            "market-1",
            "wall-1_2",
            # Объект с положительным владельцем — страница пользователя, не его адрес.
            "photo1_2",
            "wall12_34",
            # «Написать сообществу»: прежде давало ник `write`.
            "write-12345",
            "write123",
            # Раздел с подпутём: на месте имени стоит имя раздела.
            "music/playlist/-1_2",
        ],
    )
    def test_vk_object_or_section_is_not_a_profile(self, path: str) -> None:
        assert _values(f'<a href="https://vk.com/{path}">vk</a>', MessengerKind.VK) == set()

    def test_sentence_dot_is_not_part_of_vk_name(self) -> None:
        assert _values("<p>Мы в VK: vk.com/mygroup.</p>", MessengerKind.VK) == {"mygroup"}

    @pytest.mark.parametrize(
        "href",
        [
            "https://t.me/addlist/aBcDeF12",
            "https://t.me/boost/adsdesk",
            "https://t.me/boost?c=123456",
            "https://t.me/contact/AbCdEf123",
            "https://t.me/invoice/1aBcDeF",
            "https://tlgrm.ru/channels/@adsdesk",
        ],
    )
    def test_telegram_service_path_is_not_a_nick(self, href: str) -> None:
        assert _values(f'<a href="{href}">tg</a>', MessengerKind.TELEGRAM) == set()

    def test_long_path_is_not_cut_to_a_nick(self) -> None:
        """Ник длиннее 32 знаков не бывает, и обрезок длинного пути — не ник."""
        html = f'<a href="https://t.me/{"a" * 40}">tg</a>'
        assert _values(html, MessengerKind.TELEGRAM) == set()

    def test_skype_login_is_not_cut_from_an_address(self) -> None:
        """`skype:adsdesk@site.com` — не логин `adsdesk`, а адрес целиком."""
        html = '<a href="skype:adsdesk@site.com?chat">Skype</a>'
        assert _values(html, MessengerKind.SKYPE) == set()

    def test_page_with_icons_and_pixel_only_has_no_channel(self) -> None:
        html = """
        <html><head><style>.fa-skype:before{content:'x'} a.skype:hover{color:red}</style>
        <script>!function(){var t={skype:e.skype};VK.Retargeting.Init("VK-RTRG-1")}()</script>
        </head><body><p>Статья про футбол.</p>
        <noscript><img src="https://vk.com/rtrg?p=VK-RTRG-123456-aBcDe"/></noscript>
        </body></html>
        """
        assert harvest_handles(html, page_kind=PageKind.HOME) == set()

    @pytest.mark.parametrize(
        ("html", "kind", "value"),
        [
            ('<a href="skype:adsdesk?chat">s</a>', MessengerKind.SKYPE, "adsdesk"),
            (
                "<a href='skype:live:.cid.123abc?chat'>s</a>",
                MessengerKind.SKYPE,
                "live:.cid.123abc",
            ),
            ("<a href=tel:+79161234567>t</a>", MessengerKind.PHONE, "79161234567"),
            ("<p>Tel:+7 916 123-45-67</p>", MessengerKind.PHONE, "79161234567"),
            ('<a href="https://t.me/adsdesk/123">пост</a>', MessengerKind.TELEGRAM, "adsdesk"),
            ('<a href="https://t.me/adsdesk/s/5">история</a>', MessengerKind.TELEGRAM, "adsdesk"),
            ('<a href="https://t.me/adsdesk/">tg</a>', MessengerKind.TELEGRAM, "adsdesk"),
            ('<a href="https://vk.com/adsdesk?w=wall-1_2">vk</a>', MessengerKind.VK, "adsdesk"),
            ('<a href="https://vk.com/club123456">vk</a>', MessengerKind.VK, "club123456"),
            ('<a href="https://vk.com/ivan.petrov">vk</a>', MessengerKind.VK, "ivan.petrov"),
            (
                '<script type="application/ld+json">{"sameAs":["https://t.me/adsdesk"]}</script>',
                MessengerKind.TELEGRAM,
                "adsdesk",
            ),
            (
                '<script>var chat={telegram:"https://t.me/adsdesk"}</script>',
                MessengerKind.TELEGRAM,
                "adsdesk",
            ),
        ],
    )
    def test_declared_channel_is_still_explicit(
        self, html: str, kind: MessengerKind, value: str
    ) -> None:
        """Ссылка, схема в атрибуте, JSON-LD и конфиг виджета — законные места канала."""
        assert _values(html, kind) == {value}


class TestNumberIsOneNumber:
    """Находка 10: номер — это группы цифр одного номера, без соседей и без потерь.

    Захват шёл сквозь пробелы, дефисы и скобки до 19 знаков, а проверка
    считала только цифры 7–15. Видимый текст склеивал абзацы через пробел,
    и номер добирал часы работы, год из подвала или соседний номер; а там,
    где разделителей много, наоборот, терял последнюю цифру.
    """

    @pytest.mark.parametrize(
        "html",
        [
            "<p>WhatsApp: +7 916 123-45-67</p><p>9:00 - 18:00</p>",
            "<p>WhatsApp: +7 916 123-45-67</p><footer>2024</footer>",
            "<p>WhatsApp: +7 916 123 45 67</p><p>8 800 123 45 67</p>",
            "<p>WhatsApp: +7 916 123-45-67<br>2024</p>",
            "<table><tr><td>WhatsApp: +7 916 123-45-67</td><td>2024</td></tr></table>",
            # Часы в той же строке: скобка — разделитель номера, `9:00` — нет.
            "<p>WhatsApp: +7 916 123-45-67 (9:00–18:00)</p>",
        ],
    )
    def test_neighbouring_digits_stay_out(self, html: str) -> None:
        assert _guessed(html, MessengerKind.WHATSAPP) == {"79161234567"}

    def test_two_numbers_in_one_line_are_not_glued(self) -> None:
        """Где кончается первый номер, не скажет никто: лучше ничего, чем склейка."""
        html = "<p>WhatsApp: +7 916 123 45 67 8 800 123 45 67</p>"
        assert _guessed(html, MessengerKind.WHATSAPP) == set()

    def test_spaced_dashes_do_not_cost_the_last_digit(self) -> None:
        html = '<a href="tel:+7 (916) 123 - 45 - 67">позвонить</a>'
        assert _values(html, MessengerKind.PHONE) == {"79161234567"}

    @pytest.mark.parametrize(
        "number",
        [
            "+٧٩١٦١٢٣٤٥٦٧",
            "٧٩١٦١٢٣٤٥٦٧",
            "７９１６１２３４５６７",
            "＋７９１６１２３４５６７",
        ],
    )
    def test_digits_of_any_script_reach_the_table_as_ascii(self, number: str) -> None:
        """Арабско-индийские и полноширинные цифры: в таблицу и в набор — латиницей."""
        assert _values(f'<a href="tel:{number}">t</a>', MessengerKind.PHONE) == {"79161234567"}

    def test_unparsed_dotted_tail_gives_nothing_rather_than_a_stub(self) -> None:
        """Точки номер не разбирает, и обрезок `7916123` хуже, чем ничего."""
        assert _guessed("<p>WhatsApp: +7 916 123.45.67</p>", MessengerKind.WHATSAPP) == set()

    def test_non_breaking_hyphen_is_a_separator(self) -> None:
        """Неразрывный дефис ставит типографика, чтобы номер не переносился."""
        html = "<p>WhatsApp: +7 916 123‑45‑67</p>"
        assert _guessed(html, MessengerKind.WHATSAPP) == {"79161234567"}

    @pytest.mark.parametrize(
        "html",
        [
            "<p>WhatsApp: <b>+7 916</b> 123-45-67</p>",
            "<dl><dt>WhatsApp</dt><dd>+7 916 123-45-67</dd></dl>",
            "<p>WhatsApp:\n      +7 916\n      123-45-67</p>",
        ],
    )
    def test_number_split_by_markup_is_still_one_number(self, html: str) -> None:
        """Строчный тег и перенос в исходнике — не граница абзаца, подпись в соседнем блоке — законна."""
        assert _guessed(html, MessengerKind.WHATSAPP) == {"79161234567"}

    def test_long_digit_runs_stay_linear(self) -> None:
        """Номер теперь берётся прогоном без потолка в знаках — и прогон обязан быть линейным."""
        html = (
            "<p>WhatsApp: " + "1 " * 50_000 + "</p>"
            '<a href="tel:' + "1-" * 50_000 + '">t</a>'
            "<p>WhatsApp: " + "1 (" * 30_000 + "</p>"
        )
        started = time.perf_counter()
        harvest_handles(html, page_kind=PageKind.HOME)
        assert time.perf_counter() - started < 1.0


class TestGuessIsANick:
    """Находка 13: догадка из текста — ник, а не слово рядом с названием мессенджера.

    Разделитель у Skype был необязательным, у короткого `tg` не было границы
    слова, а конец ника не отличал схему адреса от конца фразы. Отсюда ники
    `https`, `for`, `and`, `com`, `friday`, `telegram` — и обратное: ник
    с точкой в конце предложения не находился вовсе.
    """

    @pytest.mark.parametrize(
        ("html", "kind"),
        [
            ("<p>Telegram: https://t.me/adsdesk</p>", MessengerKind.TELEGRAM),
            ("<p>Skype for Business</p>", MessengerKind.SKYPE),
            ("<p>Contact us via Skype and email</p>", MessengerKind.SKYPE),
            ("<p>Download it from skype.com</p>", MessengerKind.SKYPE),
            ("<p>Next mtg: Friday</p>", MessengerKind.TELEGRAM),
            ("<footer><a>Skype</a> <a>Telegram</a></footer>", MessengerKind.SKYPE),
            ("<p>Skype: chat</p>", MessengerKind.SKYPE),
            # Дефис без пробела — составное слово, а не подпись канала.
            ("<p>Telegram-chat adsdesk</p>", MessengerKind.TELEGRAM),
            # Логин не обрезается на собаке: это адрес, а не имя в Skype.
            ("<p>Skype: adsdesk@site.com</p>", MessengerKind.SKYPE),
        ],
    )
    def test_word_next_to_a_messenger_is_not_a_nick(self, html: str, kind: MessengerKind) -> None:
        assert _guessed(html, kind) == set()

    @pytest.mark.parametrize(
        "html", ["<p>Пишите в телеграм @adsdesk.</p>", "<p>Telegram: @adsdesk.</p>"]
    )
    def test_sentence_dot_does_not_hide_a_nick(self, html: str) -> None:
        assert _guessed(html, MessengerKind.TELEGRAM) == {"adsdesk"}

    def test_compound_word_does_not_add_a_nick(self) -> None:
        """«Telegram-channel: @adsdesk» — ник один, `channel` не ник."""
        html = "<p>Telegram-channel: @adsdesk</p>"
        assert _guessed(html, MessengerKind.TELEGRAM) == {"adsdesk"}

    @pytest.mark.parametrize(
        ("html", "kind", "value"),
        [
            ("<p>Telegram: adsdesk</p>", MessengerKind.TELEGRAM, "adsdesk"),
            ("<p>Телеграм — @ads_desk</p>", MessengerKind.TELEGRAM, "ads_desk"),
            ("<p>tg: adsdesk, почта ниже</p>", MessengerKind.TELEGRAM, "adsdesk"),
            ("<dl><dt>Telegram</dt><dd>@adsdesk</dd></dl>", MessengerKind.TELEGRAM, "adsdesk"),
            ("<p>Skype: ads.desk_2024</p>", MessengerKind.SKYPE, "ads.desk_2024"),
            ("<p>Skype - adsdesk</p>", MessengerKind.SKYPE, "adsdesk"),
            ("<p>Скайп: adsdesk.</p>", MessengerKind.SKYPE, "adsdesk"),
            (
                "<table><tr><td>Skype:</td><td>adsdesk</td></tr></table>",
                MessengerKind.SKYPE,
                "adsdesk",
            ),
            ("<p>Skype: live:.cid.123abc</p>", MessengerKind.SKYPE, "live:.cid.123abc"),
        ],
    )
    def test_labelled_nick_is_still_found(self, html: str, kind: MessengerKind, value: str) -> None:
        assert _guessed(html, kind) == {value}
