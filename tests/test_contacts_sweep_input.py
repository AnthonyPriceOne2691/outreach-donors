"""Список доменов для прогона по файлу: кривая ячейка стоит строки, а не файла.

Ревью #118 нашло пять способов, которыми чтение теряло весь файл или часть
доменов, и все пять выглядели законно: отказ с чужим текстом («Invalid IPv6
URL») без номера строки, трассировка на разделителе из оболочки, домен,
пропавший без следа, собственная выгрузка сервиса, объявленная «без колонки
с доменом», и подсказка ошибки, которая молча теряла первый домен списка.

Правило одно на все случаи: строка, из которой домена не вышло, считается
и называется — номер и ячейка как есть, — а остальной файл читается.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.cli.contact_sweep import EXIT_OK, cmd_contacts_file
from backend.features.contacts import file_sweep
from backend.features.donors.export import COLUMNS
from tests.contacts_sweep_fakes import Web, cli_args, install, page, write


class TestOneBadCellCostsOneLine:
    def test_bracket_in_a_cell_does_not_drop_the_file(self, tmp_path: Path) -> None:
        """До правки `urlsplit` бросал «Invalid IPv6 URL» на весь файл."""
        source = write(
            tmp_path / "list.csv",
            "host\nsite.com\nbrand-new-site.com [old]\nshop.de[2]\nshop.de\n",
        )
        assert file_sweep.read_hosts(source) == ["site.com", "brand-new-site.com", "shop.de"]

    def test_unreadable_cell_is_counted_and_named(self, tmp_path: Path) -> None:
        source = write(tmp_path / "list.csv", "host;dr\nsite.com;4\nshop.de[2];5\nn/a;6\n;7\n")
        listed = file_sweep.read_list(source)
        assert listed.hosts == ["site.com"]
        assert listed.unreadable == [(3, "shop.de[2]"), (4, "n/a")]
        assert listed.empty == 1

    def test_cell_with_a_note_after_the_domain(self) -> None:
        assert file_sweep.host_from_cell("brand-new-site.com (new)") == "brand-new-site.com"
        assert file_sweep.host_from_cell("site.com[old]") == ""
        assert file_sweep.host_from_cell("Сайт магазина") == ""

    async def test_command_walks_the_rest_and_names_the_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        install(monkeypatch, Web({"site.com": {"/": page("пусто")}}))
        source = write(tmp_path / "list.csv", "host\nsite.com\nshop.de[2]\n")

        assert await cmd_contacts_file(cli_args(source)) == EXIT_OK

        out = capsys.readouterr().out
        assert "строка 3" in out
        assert "shop.de[2]" in out


class TestDelimiter:
    def test_tab_typed_in_the_shell(self, tmp_path: Path) -> None:
        """`--delimiter '\\t'` из оболочки приезжает двумя знаками — это табуляция."""
        source = write(tmp_path / "list.tsv", "host\tdr\nsite.com\t40\n")
        assert file_sweep.read_hosts(source, delimiter="\\t") == ["site.com"]

    def test_tab_is_guessed_without_a_hint(self, tmp_path: Path) -> None:
        source = write(tmp_path / "list.tsv", "host\tdr\nsite.com\t40\n")
        assert file_sweep.read_hosts(source) == ["site.com"]

    def test_impossible_delimiter_is_said_in_words(self, tmp_path: Path) -> None:
        source = write(tmp_path / "list.csv", "host\nsite.com\n")
        with pytest.raises(ValueError, match="разделител"):
            file_sweep.read_hosts(source, delimiter=";;")


class TestCellForms:
    def test_link_with_an_address_in_its_query(self, tmp_path: Path) -> None:
        """`//` внутри параметра не делает ячейку адресом со схемой."""
        source = write(tmp_path / "list.csv", "url\nsite.com/go?url=https://shop.de/\n")
        assert file_sweep.read_hosts(source) == ["site.com"]

    def test_international_name_is_a_domain(self, tmp_path: Path) -> None:
        source = write(tmp_path / "list.csv", "host\nпример.рф\nhttps://WWW.Shop.DE/path\n")
        assert file_sweep.read_hosts(source) == ["пример.рф", "shop.de"]


class TestHeader:
    def test_own_donor_export_is_accepted(self, tmp_path: Path) -> None:
        """Выгрузка доноров самого сервиса: колонка домена там называется «домен»."""
        header = ";".join(title for title, _ in COLUMNS)
        cells = ";".join(["site.com", *([""] * (len(COLUMNS) - 1))])
        source = write(tmp_path / "donors.csv", f"{header}\n{cells}\n", encoding="utf-8-sig")
        assert file_sweep.read_hosts(source) == ["site.com"]

    def test_list_without_a_header(self, tmp_path: Path) -> None:
        source = write(tmp_path / "list.txt", "first-site.com\nsecond-site.com\n")
        assert file_sweep.read_hosts(source) == ["first-site.com", "second-site.com"]

    def test_hint_of_the_old_error_keeps_the_first_domain(self, tmp_path: Path) -> None:
        """Прежняя ошибка советовала `--column first-site.com`, и первый домен
        уходил в заголовок молча."""
        source = write(tmp_path / "list.txt", "first-site.com\nsecond-site.com\n")
        assert file_sweep.read_hosts(source, column="first-site.com") == [
            "first-site.com",
            "second-site.com",
        ]

    def test_headerless_file_with_several_columns(self, tmp_path: Path) -> None:
        source = write(tmp_path / "list.csv", "Магазин;shop.de;12\nСайт;site.com;40\n")
        assert file_sweep.read_hosts(source) == ["shop.de", "site.com"]


class TestEncoding:
    def test_file_not_in_utf8_says_what_to_do(self, tmp_path: Path) -> None:
        """Excel на русской Windows сохраняет «CSV» в cp1251 — ошибка говорит, что делать."""
        source = tmp_path / "list.csv"
        source.write_bytes("домен;заметка\nsite.com;старый\n".encode("cp1251"))
        with pytest.raises(ValueError, match="CSV UTF-8"):
            file_sweep.read_hosts(source)
