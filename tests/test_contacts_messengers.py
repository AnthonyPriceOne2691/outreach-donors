"""Немейловые каналы связи со страницы: мессенджеры и телефон.

Проверяется не только «ник нашёлся», но и обратное — что страница без
канала не отдаёт ничего. Ложный канал хуже пропущенного: пропущенный
домен уйдёт в очередь на ручной разбор, а придуманный ник попадёт
в таблицу как контакт, и обнаружится это только тем, что по нему
никто не ответил.
"""

from __future__ import annotations

import pytest
from backend.features.contacts.messengers import (
    Handle,
    MessengerKind,
    Trust,
    extract_handle_guesses,
    extract_handles,
)


def _values(html: str, kind: MessengerKind) -> set[str]:
    return {handle.value for handle in extract_handles(html) if handle.kind is kind}


def _guessed(html: str, kind: MessengerKind) -> set[str]:
    return {handle.value for handle in extract_handle_guesses(html) if handle.kind is kind}


class TestTelegram:
    def test_link(self) -> None:
        html = '<a href="https://t.me/WebMaster_01">Telegram</a>'
        assert _values(html, MessengerKind.TELEGRAM) == {"webmaster_01"}

    def test_channel_preview_path(self) -> None:
        """`t.me/s/name` — та же площадка, только предпросмотр канала."""
        assert _values('<a href="https://t.me/s/adsdesk">канал</a>', MessengerKind.TELEGRAM) == {
            "adsdesk"
        }

    @pytest.mark.parametrize(
        "href",
        [
            "https://telegram.me/adsdesk",
            "https://tlgrm.ru/adsdesk",
            "tg://resolve?domain=adsdesk",
        ],
    )
    def test_other_forms_of_the_same_link(self, href: str) -> None:
        assert _values(f'<a href="{href}">тг</a>', MessengerKind.TELEGRAM) == {"adsdesk"}

    def test_scheme_outside_href_is_found(self) -> None:
        """Схему половина сайтов вешает на onclick — обход только ссылок её не увидел бы."""
        html = "<button onclick=\"location='tg://resolve?domain=adsdesk'\">написать</button>"
        assert _values(html, MessengerKind.TELEGRAM) == {"adsdesk"}

    @pytest.mark.parametrize("href", ["https://about.me/johndoe", "https://client.me/login"])
    def test_another_domain_ending_in_t_me(self, href: str) -> None:
        """Главная ловушка: `t.me/` лежит внутри чужого хоста.

        `abou|t.me/`, `clien|t.me/` — про-фили about.me стоят на контактных
        страницах сплошь, и без границы перед доменом каждый такой адрес
        отдавал бы «телеграм-ник», совершенно правдоподобный на вид.
        """
        assert extract_handles(f'<a href="{href}">профиль</a>') == set()

    def test_group_invite_is_not_a_contact(self) -> None:
        """По приглашению в группу написать нельзя, а в таблице оно смотрелось бы контактом."""
        html = (
            '<a href="https://t.me/joinchat/AAAAAEkk2WdoDrB4-Q8-gg">чат</a>'
            '<a href="https://t.me/+AbCdEf123456">и ещё</a>'
        )
        assert _values(html, MessengerKind.TELEGRAM) == set()

    def test_share_button_is_not_a_contact(self) -> None:
        html = '<a href="https://t.me/share/url?url=https://site.com/post">поделиться</a>'
        assert _values(html, MessengerKind.TELEGRAM) == set()

    def test_short_nick_is_not_taken(self) -> None:
        assert _values('<a href="https://t.me/ab">x</a>', MessengerKind.TELEGRAM) == set()

    def test_nick_must_start_with_a_letter(self) -> None:
        assert _values('<a href="https://t.me/1234567">x</a>', MessengerKind.TELEGRAM) == set()


class TestSkype:
    def test_scheme_with_action(self) -> None:
        html = '<a href="skype:AdsDesk?chat">Skype</a>'
        assert _values(html, MessengerKind.SKYPE) == {"adsdesk"}

    def test_invite_link(self) -> None:
        html = '<a href="https://join.skype.com/invite/aBcD1234">Skype</a>'
        assert _values(html, MessengerKind.SKYPE) == {"abcd1234"}

    def test_new_style_id_from_text(self) -> None:
        """`live:.cid.xxx` — новый идентификатор; в тексте он встречается без слова Skype."""
        assert _guessed("<p>live:.cid.8f1c2d3e4f</p>", MessengerKind.SKYPE) == {
            "live:.cid.8f1c2d3e4f"
        }

    def test_labelled_in_text(self) -> None:
        assert _guessed("<p>Skype: ads.desk_2024</p>", MessengerKind.SKYPE) == {"ads.desk_2024"}


class TestWhatsAppAndViber:
    @pytest.mark.parametrize(
        "href",
        [
            "https://wa.me/79161234567",
            "https://api.whatsapp.com/send?phone=79161234567",
            "https://web.whatsapp.com/send/?phone=%2B79161234567",
            "whatsapp://send?phone=+7 (916) 123-45-67",
        ],
    )
    def test_number_is_normalised_to_digits(self, href: str) -> None:
        assert _values(f'<a href="{href}">WA</a>', MessengerKind.WHATSAPP) == {"79161234567"}

    def test_viber(self) -> None:
        html = '<a href="viber://chat?number=+380671234567">Viber</a>'
        assert _values(html, MessengerKind.VIBER) == {"380671234567"}

    def test_labelled_in_text(self) -> None:
        assert _guessed("<p>WhatsApp: +7 916 123-45-67</p>", MessengerKind.WHATSAPP) == {
            "79161234567"
        }


class TestPhoneAndVk:
    def test_tel_link(self) -> None:
        html = '<a href="tel:+7 (916) 123-45-67">позвонить</a>'
        assert _values(html, MessengerKind.PHONE) == {"79161234567"}

    @pytest.mark.parametrize("value", ["123456", "12-34-56", "1234567890123456789"])
    def test_not_every_digit_run_is_a_number(self, value: str) -> None:
        """Шесть цифр — это цена или год; девятнадцать не бывает ни у кого.

        Короткий номер с разделителями («12-34-56») длиной проходит, и
        отсечь его может только подсчёт самих цифр.
        """
        assert _values(f'<a href="tel:{value}">позвонить</a>', MessengerKind.PHONE) == set()

    def test_vk_profile(self) -> None:
        assert _values('<a href="https://vk.com/webmaster_01">VK</a>', MessengerKind.VK) == {
            "webmaster_01"
        }

    def test_vk_widget_is_not_a_contact(self) -> None:
        """Кнопка «поделиться» стоит на каждой второй странице."""
        html = (
            '<a href="https://vk.com/share.php?url=x">поделиться</a>'
            '<script src="https://vk.com/js/api/openapi.js"></script>'
        )
        assert _values(html, MessengerKind.VK) == set()


class TestGuesses:
    def test_bare_nick_in_text(self) -> None:
        assert _guessed("<p>Пишите @adsdesk по размещению</p>", MessengerKind.TELEGRAM) == {
            "adsdesk"
        }

    def test_email_local_part_is_not_a_nick(self) -> None:
        """Главный ложный источник догадки: собака внутри адреса."""
        html = "<p>Почта: info@adsdesk.com, ещё editor@site.com</p>"
        assert _guessed(html, MessengerKind.TELEGRAM) == set()

    @pytest.mark.parametrize(
        "text",
        ["Наш канал: youtube.com/@SportsDaily", "Мы в сети — instagram.com/@adsdesk"],
    )
    def test_handle_of_another_network_is_not_telegram(self, text: str) -> None:
        """В подвале адрес печатают текстом, и `/@name` — это ютуб, а не телеграм.

        Отличает их только то, что стоит перед собакой: слэш чужого адреса
        или пробел живой фразы «пишите @nick».
        """
        assert _guessed(f"<p>{text}</p>", MessengerKind.TELEGRAM) == set()

    def test_labelled_nick(self) -> None:
        assert _guessed("<p>Телеграм — @ads_desk</p>", MessengerKind.TELEGRAM) == {"ads_desk"}

    def test_label_without_at_sign(self) -> None:
        assert _guessed("<p>Telegram: adsdesk</p>", MessengerKind.TELEGRAM) == {"adsdesk"}

    def test_guess_does_not_repeat_an_explicit_link(self) -> None:
        """Иначе один ник вернулся бы дважды и сводить их пришлось бы вызывающему."""
        html = '<a href="https://t.me/adsdesk">@adsdesk</a>'
        assert extract_handles(html) == {Handle(MessengerKind.TELEGRAM, "adsdesk", Trust.EXPLICIT)}
        assert extract_handle_guesses(html) == set()

    def test_trust_is_marked(self) -> None:
        explicit = extract_handles('<a href="https://t.me/adsdesk">тг</a>')
        guessed = extract_handle_guesses("<p>пишите @adsdesk</p>")
        assert {handle.trust for handle in explicit} == {Trust.EXPLICIT}
        assert {handle.trust for handle in guessed} == {Trust.GUESSED}


class TestEmptyAndOrdinaryPages:
    @pytest.mark.parametrize(
        "html", ["", "<html><body><p>Обычная статья про футбол.</p></body></html>"]
    )
    def test_nothing_is_invented(self, html: str) -> None:
        assert extract_handles(html) == set()
        assert extract_handle_guesses(html) == set()

    def test_page_with_several_channels_at_once(self) -> None:
        html = """
        <footer>
          <a href="https://t.me/adsdesk">Telegram</a>
          <a href="skype:ads.desk?call">Skype</a>
          <a href="https://wa.me/79161234567">WhatsApp</a>
          <a href="tel:+79161234567">Телефон</a>
          <a href="mailto:ads@site.com">Почта</a>
        </footer>
        """
        assert extract_handles(html) == {
            Handle(MessengerKind.TELEGRAM, "adsdesk", Trust.EXPLICIT),
            Handle(MessengerKind.SKYPE, "ads.desk", Trust.EXPLICIT),
            Handle(MessengerKind.WHATSAPP, "79161234567", Trust.EXPLICIT),
            Handle(MessengerKind.PHONE, "79161234567", Trust.EXPLICIT),
        }
