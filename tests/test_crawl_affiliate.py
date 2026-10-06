"""Партнёрская ссылка: метка в адресе и рекламодатель за сетью.

Признак общий для любой ниши — поэтому и проверки без ниши: метка
партнёрки, платный `utm_medium`, переход `/go/`, сеть, которая называет
рекламодателя, и сеть, которая его прячет. Рядом — то, что меткой
не считается: `?ref=` и `utm_medium=email` ставят всем подряд.
"""

from __future__ import annotations

import pytest
from backend.features.crawl.affiliate import affiliation


class TestMarks:
    @pytest.mark.parametrize(
        ("url", "mark"),
        [
            ("https://brand.com/signup?aff_id=77", "метка «aff_id» в адресе"),
            ("https://brand.com/?IRCLICKID=abc", "метка «irclickid» в адресе"),
            ("https://brand.com/?btag=a_1b_2", "метка «btag» в адресе"),
            ("https://brand.com/?utm_source=donor&utm_medium=Affiliate", "utm_medium=affiliate"),
            ("https://brand.com/go/summer-deal", "переход /go/"),
            ("https://brand.com/recommends/acme", "переход /recommends/"),
        ],
    )
    def test_a_mark_is_named(self, url: str, mark: str) -> None:
        found = affiliation(url)

        assert found is not None
        assert found.mark == mark
        assert found.network is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://brand.com/?ref=donor",  # Ghost ставит на каждую внешнюю ссылку
            "https://brand.com/?utm_source=newsletter&utm_medium=email",
            "https://brand.com/go",  # раздел без адреса за ним
            "https://brand.com/blog/go/to-the-park",  # «go» не первым разделом
            "https://brand.com/track/order-123",  # «где мой заказ», а не счётчик
            "https://brand.com/pricing",
        ],
    )
    def test_ordinary_addresses_carry_no_mark(self, url: str) -> None:
        assert affiliation(url) is None

    def test_broken_address_is_not_a_mark(self) -> None:
        assert affiliation("http://[::1") is None


class TestNetworks:
    def test_network_names_the_advertiser_in_a_parameter(self) -> None:
        url = (
            "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"
            "&ued=https%3A%2F%2Fwww.acmeshop.com%2Fsale"
        )

        found = affiliation(url)

        assert found is not None
        assert found.network == "awin1.com"
        assert found.target_host == "www.acmeshop.com"
        assert found.target_root == "acmeshop.com"

    def test_twice_encoded_destination_is_decoded(self) -> None:
        url = "https://acme.sjv.io/c/1/2/3?u=https%253A%252F%252Fstore.acme.com%252Fdeal"

        found = affiliation(url)

        assert found is not None
        assert found.target_root == "acme.com"

    def test_network_that_hides_the_advertiser(self) -> None:
        found = affiliation("https://click.linksynergy.com/fs-bin/click?id=abc&offerid=1")

        assert found is not None
        assert found.network == "linksynergy.com"
        assert found.target_root is None

    def test_a_parameter_that_is_not_an_address_is_skipped(self) -> None:
        """«p=2» — номер страницы, «url=» на саму сеть — её же переход."""
        url = "https://www.shareasale.com/r.cfm?p=2&url=https%3A%2F%2Fwww.shareasale.com%2Fx"

        found = affiliation(url)

        assert found is not None
        assert found.target_root is None

    def test_endlessly_encoded_value_is_not_an_address(self) -> None:
        url = "https://prf.hn/click/camref:1/destination:x?url=%25252525"

        found = affiliation(url)

        assert found is not None
        assert found.target_root is None
