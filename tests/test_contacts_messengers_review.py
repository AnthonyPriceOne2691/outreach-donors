"""Каналы связи по ревью #118: код, служебный адрес и соседняя цифра — не контакт.

Каналы уходят в выгрузку прогона по файлу колонками «явные» и «угаданные»,
и по ним пишут люди. Поэтому главное здесь — обратная сторона находки:
CSS, скрипт, пиксель, раздел соцсети и часы работы каналом не становятся.
Рядом с каждой такой проверкой стоит законная форма того же канала: правка,
которая убирает мусор ценой настоящих контактов, — не правка.
"""

from __future__ import annotations

import pytest
from backend.features.contacts.messengers import (
    MessengerKind,
    extract_handles,
    harvest_handles,
)
from backend.features.core.domain import PageKind


def _values(html: str, kind: MessengerKind) -> set[str]:
    return {handle.value for handle in extract_handles(html) if handle.kind is kind}


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
