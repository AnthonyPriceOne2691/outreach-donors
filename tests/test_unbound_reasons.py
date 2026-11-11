"""Почему ответ не привязан — правило без базы.

Путь через вебхук и базу проверяет `test_unbound_replies.py`; здесь —
само правило, на котором он стоит: какая причина у какого письма и как
метка читается без подписи. Отдельно от базы, потому что границы
правила (плюс в ящике отправителя, метка с чужой подписью рядом с чужой
цепочкой) проще перебрать, чем собирать под каждую письмо и форму.
"""

from __future__ import annotations

import pytest
from backend.features.core.domain import Stage
from backend.features.letters import reply_to
from backend.features.letters.probe import PROBE_MESSAGE_ID
from backend.features.replies import binding, unbound
from backend.features.replies.binding import Unbound
from backend.features.replies.inbound import Incoming

SECRET = "s" * 32
REPLIES = "replies.ours.test"

#: Метки строки списка — слово в слово: экран ставит их после «Почему не привязан: ».
SHORT = {
    Unbound.NO_LABEL: "написано заново, не ответом на наше письмо",
    Unbound.FOREIGN_THREAD: "ответ на чужое или пересланное письмо",
    Unbound.BAD_SIGNATURE: "подпись метки не сошлась",
    Unbound.NO_SUCH_LETTER: "письма из метки нет в базе",
}
#: Причины, по которым отправителя ищут руками, — у них слова называют, кого именно.
BY_HAND = (Unbound.NO_LABEL, Unbound.FOREIGN_THREAD, Unbound.NO_SUCH_LETTER)


def incoming(
    *to: str, in_reply_to: str | None = None, references: tuple[str, ...] = ()
) -> Incoming:
    return Incoming(
        message_id="<in-1@site.test>",
        to=to,
        from_email="editor@donor.example.test",
        subject="Re: Advertising rates",
        text="250 EUR",
        in_reply_to=in_reply_to,
        references=references,
    )


class TestReason:
    def test_label_binds_and_leaves_no_reason(self) -> None:
        label = f"anna+{reply_to.label_for(417, secret=SECRET)}@{REPLIES}"

        bound = binding.bind(incoming(label), secret=SECRET)

        assert (bound.message_id, bound.unbound) == (417, None)

    def test_our_thread_binds_and_leaves_no_reason(self) -> None:
        bound = binding.bind(
            incoming("info@ours.test", in_reply_to="<ours-1@mail.test>"),
            by_message_id=[("<ours-1@mail.test>", 5)],
            secret=SECRET,
        )

        assert (bound.message_id, bound.way, bound.unbound) == (5, binding.BindingWay.HEADERS, None)

    def test_nothing_to_bind_by(self) -> None:
        bound = binding.bind(incoming("info@ours.test"), secret=SECRET)

        assert bound.unbound is Unbound.NO_LABEL

    def test_references_that_are_not_ours(self) -> None:
        bound = binding.bind(
            incoming("info@ours.test", references=("<theirs@elsewhere.test>",)), secret=SECRET
        )

        assert bound.unbound is Unbound.FOREIGN_THREAD

    def test_bad_signature_is_louder_than_a_foreign_thread(self) -> None:
        """Сменённый секрет даёт метку с чужой подписью у каждого ответа —
        и у каждого же чужую цепочку: наш `Message-ID` тут ни при чём.
        Назвать надо первое, иначе дело будут искать не там."""
        forged = f"anna+m417.{'0' * reply_to.SIGNATURE_LEN}@{REPLIES}"

        bound = binding.bind(incoming(forged, in_reply_to="<x@elsewhere.test>"), secret=SECRET)

        assert bound.unbound is Unbound.BAD_SIGNATURE

    def test_missing_letter_is_its_own_reason(self) -> None:
        assert binding.no_such_letter().unbound is Unbound.NO_SUCH_LETTER
        assert not binding.no_such_letter().bound


class TestLabelWithoutSignature:
    def test_number_after_the_last_plus(self) -> None:
        """Плюс бывает своим у ящика отправителя: `anna+sales@`."""
        address = f"anna+sales+m17.{'a' * reply_to.SIGNATURE_LEN}@{REPLIES}"

        assert reply_to.labelled_number(address) == 17
        # Для привязки такой адрес — ничей: подпись выдумана.
        assert reply_to.message_id_from(address, secret=SECRET) is None

    def test_no_label_no_number(self) -> None:
        assert reply_to.labelled_number(f"anna+sales@{REPLIES}") is None
        assert reply_to.labelled_number(f"m17.{'a' * reply_to.SIGNATURE_LEN}@{REPLIES}") is None

    def test_first_label_among_the_addresses(self) -> None:
        label = f"anna+{reply_to.label_for(9, secret=SECRET)}@{REPLIES}"

        assert unbound.labelled(["boss@donor.example.test", label]) == 9
        assert unbound.labelled(None) is None


class TestWords:
    def test_every_reason_has_its_own_words(self) -> None:
        said = {unbound.explain(reason, None) for reason in Unbound}

        assert len(said) == len(Unbound)
        assert not any("кодом" in words for words in said)

    def test_unknown_code_says_so_instead_of_pretending(self) -> None:
        assert unbound.explain("gone_fishing", None) == (
            "Причина записана кодом, которого этот сервер не знает (gone_fishing)."
        )

    def test_long_preview_is_cut_with_an_ellipsis(self) -> None:
        """Одна строка в пределах: переводы строк и двойные пробелы письма
        строку списка не рвут."""
        text = "Hello,\n\n" + "our   rate is 250 EUR. " * 20

        cut = unbound.preview(text)

        assert len(cut) <= unbound.PREVIEW_CHARS
        assert cut.startswith("Hello, our rate is 250 EUR.")
        assert cut.endswith("…")
        assert "  " not in cut
        assert "\n" not in cut


class TestShortLabel:
    """Метка в несколько слов для строки списка (11.11.2026): причина остаётся на виду,
    слова целиком — по запросу. Метку и слова выбирает один и тот же код причины."""

    def test_every_reason_has_its_own_label(self) -> None:
        assert {reason: unbound.explain_short(reason, None) for reason in Unbound} == SHORT

    @pytest.mark.parametrize("reason", [*Unbound, None, "gone_fishing"])
    def test_label_continues_the_phrase_on_the_screen(self, reason: str | None) -> None:
        """«Почему не привязан: …» — со строчной буквы, без точки и в одну строку."""
        label = unbound.explain_short(reason, None)

        assert label[0].islower()
        assert not label.endswith(".")
        assert len(label) < len(unbound.explain(reason, None))

    def test_reply_to_the_probe_letter_is_named_by_the_same_check(self) -> None:
        """Пробное письмо — тот же код «письма нет», и метка с номером 0 отличает его
        и в метке, и в словах: одно без другого сказало бы «письма нет» про исправный приём."""
        probe = f"anna+{reply_to.label_for(PROBE_MESSAGE_ID, secret=SECRET)}@{REPLIES}"
        gone = f"anna+{reply_to.label_for(417, secret=SECRET)}@{REPLIES}"

        assert unbound.explain_short(Unbound.NO_SUCH_LETTER, [probe]) == "ответ на пробное письмо"
        assert unbound.explain(Unbound.NO_SUCH_LETTER, [probe]).startswith("Ответ на пробное")
        assert (
            unbound.explain_short(Unbound.NO_SUCH_LETTER, [gone]) == SHORT[Unbound.NO_SUCH_LETTER]
        )
        # Номер 0 при другой причине пробным письмом ответ не делает.
        assert unbound.explain_short(Unbound.BAD_SIGNATURE, [probe]) == SHORT[Unbound.BAD_SIGNATURE]

    def test_unrecorded_reason(self) -> None:
        assert unbound.explain_short(None, None) == "причина не записана"

    def test_unknown_code_stays_in_the_full_words_only(self) -> None:
        assert unbound.explain_short("gone_fishing", None) == "неизвестная причина"
        assert "gone_fishing" in unbound.explain("gone_fishing", None)


class TestWhomToLookFor:
    """Кого ищут по ответу — словом его направления: на ящик продаж отвечает лид, а не донор."""

    @pytest.mark.parametrize(
        ("stage", "whom"),
        [
            (None, "Донора"),
            (Stage.DONORS, "Донора"),
            (Stage.ADVERTISERS, "Рекламодателя"),
            (Stage.SALES, "Лида"),
        ],
        ids=["неизвестно", "доноры", "рекламодатели", "продажи"],
    )
    @pytest.mark.parametrize("reason", BY_HAND)
    def test_words_name_the_direction(
        self, reason: Unbound, stage: Stage | None, whom: str
    ) -> None:
        words = unbound.explain(reason, None, stage)

        assert words.endswith(f" {whom} ищут по отправителю и тексту.")
        assert "{" not in words

    def test_without_a_direction_the_words_are_the_old_ones(self) -> None:
        """Так причину говорит приём (`pipeline`): направления он не спрашивает."""
        assert unbound.explain(Unbound.NO_LABEL, None) == (
            "Метки в адресе нет, и заголовков цепочки тоже: письмо написано заново, "
            "а не ответом на наше. Донора ищут по отправителю и тексту."
        )

    @pytest.mark.parametrize("stage", list(Stage))
    def test_where_nobody_is_looked_for_the_direction_changes_nothing(self, stage: Stage) -> None:
        """Подпись чинят, а пробное письмо — добрая новость: искать некого ни в одном направлении."""
        probe = [f"anna+{reply_to.label_for(PROBE_MESSAGE_ID, secret=SECRET)}@{REPLIES}"]

        for reason, addresses in ((Unbound.BAD_SIGNATURE, None), (Unbound.NO_SUCH_LETTER, probe)):
            words = unbound.explain(reason, addresses, stage)
            assert words == unbound.explain(reason, addresses)
            assert "ищут" not in words
