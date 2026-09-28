"""Почему ответ не привязан — правило без базы.

Путь через вебхук и базу проверяет `test_unbound_replies.py`; здесь —
само правило, на котором он стоит: какая причина у какого письма и как
метка читается без подписи. Отдельно от базы, потому что границы
правила (плюс в ящике отправителя, метка с чужой подписью рядом с чужой
цепочкой) проще перебрать, чем собирать под каждую письмо и форму.
"""

from __future__ import annotations

from backend.features.letters import reply_to
from backend.features.replies import binding, unbound
from backend.features.replies.binding import Unbound
from backend.features.replies.inbound import Incoming

SECRET = "s" * 32
REPLIES = "replies.ours.test"


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
