"""Дверь для авторов: страница приёма статей отличается от статьи о них."""

from __future__ import annotations

import pytest
from backend.features.donors.author_door import author_door, open_door
from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation


@pytest.mark.parametrize(
    "url",
    [
        "https://a.example/write-for-us/",
        "https://a.example/blog/write-for-us",
        "https://a.example/en/write-for-us.php",
        "https://a.example/technology-write-for-us/",
        "https://a.example/write-for-us-guest-post",
        "https://a.example/blog/write-for-hashed-out/",
        "https://a.example/become-a-contributor/",
        "https://a.example/become-jelvix-writer",
        "https://a.example/blog/become-guest-author",
        "https://a.example/contributor-guidelines",
        "https://a.example/contributor-program",
        "https://a.example/blog/guest-post-submission/",
        "https://a.example/guest-post-guidelines-for-small-business",
        "https://a.example/guest-post-blog-guidelines-requirements",
        "https://a.example/legal/contribute",
        "https://a.example/blog/contribute-to-the-company-blog",
        "https://a.example/gastautor-werden",
        "https://a.example/schreiben-sie-fuer-uns/",
        "https://a.example/escribe-para-nosotros",
        "https://a.example/scrivi-per-noi",
        "https://a.example/ecrire-pour-nous",
        "https://a.example/escreva-para-nos",
        "https://a.example/napisz-dla-nas",
    ],
)
def test_author_pages_are_doors(url: str) -> None:
    assert author_door(url, None) is not None, url


@pytest.mark.parametrize(
    "url",
    [
        # Статьи ПРО гостевые посты — их пишут продавцы инструментов.
        "https://a.example/glossary/guest-blogging/",
        "https://a.example/blog/guest-posting-opportunities",
        "https://a.example/blog/tools-for-guest-posting",
        "https://a.example/blog/guest-blogging-for-b2b-saas",
        "https://a.example/ai-prompts/guest-post-generator",
        "https://a.example/blog/how-to-write-for-seo",
        "https://a.example/guest-posting-sites/technology",
        "https://a.example/",
        "https://a.example/pricing",
    ],
)
def test_articles_about_guest_posts_are_not_doors(url: str) -> None:
    assert author_door(url, "Guest posting in 2026: a guide") is None, url


@pytest.mark.parametrize(
    ("url", "title"),
    [
        # Очередь №18: адрес и заголовок зовут «пишите», а страница — список
        # чужих площадок от агентства.
        (
            "https://a.example/write-for-us-technology-blogs/",
            "80+ Technology Write for Us Sites | Guest Post Blogs 2026",
        ),
        (
            "https://a.example/write-for-us-business",
            "Top 12 Write For Us Business Guest Post Sites",
        ),
        ("https://a.example/write-for-us-decor", "Best Guest Post Sites for Interior Design"),
        ("https://a.example/write-for-us", "35 guest posting opportunities (sorted by niche + DR)"),
        ("https://a.example/gastautor-werden", "50 Blogs, die Gastbeiträge annehmen"),
    ],
)
def test_a_list_of_other_sites_is_not_a_door(url: str, title: str) -> None:
    assert author_door(url, title) is None, title
    # Меню самого сайта — по-прежнему дверь: подборка не отменяет его.
    assert author_door(url, title, ("Blog", "Advertise")) == "меню главной: «Advertise»"


@pytest.mark.parametrize(
    "title",
    [
        "Write For Us - Tech Guest Post Opportunities - UK Tech Blog",
        # Год — не число площадок.
        "Write for Us (2026) - Submit Guest Posts on Our Tech Blogs",
        "Guest Post Submissions - Web, App Development, AI, SaaS",
        "Gastautor werden – Die besten Tipps",
    ],
)
def test_a_door_page_is_not_taken_for_a_list(title: str) -> None:
    assert author_door("https://a.example/write-for-us", title) is not None, title


def test_title_and_menu_open_the_door() -> None:
    assert author_door("https://a.example/p/123", "Write For Us | Tech Blog")
    assert author_door(None, None, ("Product", "Pricing", "Advertise"))
    assert author_door(None, None, ("Blog", "Write for us"))
    # Не целый пункт — продукт рекламной платформы, а не место на сайте.
    assert author_door(None, None, ("Advertising solutions", "Pricing")) is None


def _verdict(intent: Intent, rec: Recommendation) -> Judgement:
    return Judgement(intent, rec, "q", "причина", "m", 1)


def test_door_turns_only_brand_reject_into_review() -> None:
    door = "страница «write-for-us»"
    opened = open_door(_verdict(Intent.SELLS_OWN, Recommendation.REJECT), door)
    assert opened.recommendation is Recommendation.REVIEW
    assert opened.intent is Intent.SELLS_OWN
    # Посредник со страницей для авторов всё равно посредник.
    vendor = open_door(_verdict(Intent.LINK_VENDOR, Recommendation.REJECT), door)
    assert vendor.recommendation is Recommendation.REJECT
    # Без двери ничего не меняется.
    closed = open_door(_verdict(Intent.SELLS_OWN, Recommendation.REJECT), None)
    assert closed.recommendation is Recommendation.REJECT
