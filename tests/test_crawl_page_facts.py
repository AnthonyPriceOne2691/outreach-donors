"""Что статья говорит о себе: пометка рекламы и дата выхода.

Разметка в положительных случаях повторяет настоящую спонсорскую статью
(обход 06.10.2026): рубрика `category-sponsored-content` в классах статьи,
`"articleSection":["Sponsored Content"]` в JSON-LD со слэшами `\\/`,
первой строкой — «This article was sponsored by …». Отрицательные —
то, что выглядит похоже и пометкой не является: оговорка про партнёрские
ссылки, сквозная оговорка сайта, рубрики соседних статей, тема статьи.
Ложная пометка дороже пропущенной: она даёт +4 каждой ссылке из тела.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from backend.features.crawl.page_facts import PageFacts, read_page

BODY = "Budgeting helps you keep more of what you earn. " * 20


def _post(
    *,
    article_class: str = "post type-post",
    head: str = "",
    first: str = "",
    last: str = "",
    extra: str = "",
    body_class: str = "single",
) -> str:
    return f"""
    <html><head>{head}</head>
    <body class="{body_class}">
      <article class="{article_class}">
        <h1>10 budgeting moves for 2026</h1>
        <p>{first}</p><p>{BODY}</p><p>{last}</p>
      </article>
      {extra}
    </body></html>
    """


def _text(first: str = "", last: str = "") -> str:
    return " ".join(f"10 budgeting moves for 2026 {first} {BODY} {last}".split())


def _facts(html: str, text: str | None = None) -> PageFacts:
    return read_page(html, text)


class TestRubricAndSection:
    def test_rubric_of_the_post_is_a_label(self) -> None:
        html = _post(article_class="post-183493 post type-post category-sponsored-content")

        assert _facts(html).label == "рубрика sponsored-content"

    def test_tag_guest_post_is_a_label(self) -> None:
        html = _post(article_class="post type-post category-money tag-guest-post")

        assert _facts(html).label == "рубрика guest-post"

    def test_ordinary_rubrics_are_not_labels(self) -> None:
        """«partners» и «paid» поодиночке — обычные слова: «paid-off-debt»."""
        html = _post(article_class="post category-partners tag-paid-off-debt category-advertising")

        assert _facts(html).label is None

    def test_rubric_of_a_neighbour_is_not_ours(self) -> None:
        """Блок «похожие статьи» — тоже `<article>` со своими рубриками,
        но заголовка страницы в нём нет."""
        related = (
            '<section class="more"><article class="post category-sponsored">'
            "<h2>Someone else's sponsored post</h2></article></section>"
        )
        html = _post(extra=related)

        assert _facts(html).label is None

    def test_single_article_without_a_page_title_still_counts(self) -> None:
        html = (
            '<html><body><div class="logo">Site</div>'
            '<article class="post category-sponsored"><h2>Title</h2><p>text</p></article>'
            "</body></html>"
        )

        assert _facts(html).label == "рубрика sponsored"

    def test_section_in_meta(self) -> None:
        html = _post(head='<meta property="article:section" content="Sponsored">')

        assert _facts(html).label == "раздел «Sponsored»"

    def test_ordinary_section_in_meta_is_not_a_label(self) -> None:
        html = _post(head='<meta property="article:section" content="Banking">')

        assert _facts(html).label is None

    def test_section_in_json_ld_with_escaped_slashes(self) -> None:
        ld = (
            '<script type="application/ld+json">{"@graph":[{"@type":"Article",'
            '"@id":"https:\\/\\/site.test\\/sponsored-content\\/x\\/#article",'
            '"articleSection":["Sponsored Content"],"inLanguage":"en-US"}]}</script>'
        )

        assert _facts(_post(head=ld)).label == "раздел «Sponsored Content»"


class TestDisclosure:
    def test_first_line_disclosure(self) -> None:
        first = "This article was sponsored by Navy Federal Credit Union. Federally insured."

        facts = _facts(_post(first=first), _text(first=first))

        assert facts.label == "«This article was sponsored by Navy Federal Credit Union»"

    def test_last_line_disclosure(self) -> None:
        last = "This is a sponsored post written by me on behalf of Acme. All opinions are mine."

        facts = _facts(_post(last=last), _text(last=last))

        assert facts.label == "«This is a sponsored post written by me on behalf of Acme»"

    @pytest.mark.parametrize(
        "first",
        ["Guest post by Jane Doe: how I paid off my loans.", "Sponsored post: our favourite app."],
    )
    def test_label_as_the_first_words(self, first: str) -> None:
        text = " ".join(f"{first} {BODY}".split())

        assert _facts(_post(first=first), text).label is not None

    @pytest.mark.parametrize(
        "line",
        [
            # Оговорка про партнёрские ссылки — на каждой статье финансового блога.
            "This post may contain affiliate links, which means I may earn a commission.",
            # Сквозная оговорка сайта: про все статьи сразу, а не про эту.
            "The website publishes editorial content, news, press releases, contributed "
            "articles, sponsored content, advertorials and other material.",
            # Тема статьи, а не пометка.
            "How to land sponsored posts on Instagram when you have 500 followers.",
        ],
    )
    def test_look_alikes_are_not_labels(self, line: str) -> None:
        """Строка стоит и самой первой, и последней — там, где ищется раскрытие."""
        text = " ".join(f"{line} {BODY} {line}".split())

        assert _facts(_post(first=line, last=line), text).label is None

    def test_topic_title_that_starts_with_guest_post_is_not_a_label(self) -> None:
        """После «guest post» нужен знак или «by»: иначе это тема."""
        text = " ".join(f"Guest post pitching: 10 tips that work {BODY}".split())

        assert _facts(_post(), text).label is None

    def test_disclosure_in_the_middle_is_the_topic(self) -> None:
        """Фраза в середине длинной статьи — цитата или тема, а не раскрытие."""
        middle = "This post is sponsored by nobody, says the author we interviewed."
        text = " ".join(f"{BODY} {middle} {BODY}".split())

        assert _facts(_post(), text).label is None


class TestPublished:
    def test_open_graph_date(self) -> None:
        head = '<meta property="article:published_time" content="2026-10-06T11:20:13.882Z">'

        assert _facts(_post(head=head)).published == date(2026, 10, 6)

    def test_json_ld_date(self) -> None:
        ld = '<script type="application/ld+json">{"datePublished":"2025-12-10T06:00:00-08:00"}</script>'

        assert _facts(_post(head=ld)).published == date(2025, 12, 10)

    def test_meta_wins_over_json_ld(self) -> None:
        head = (
            '<meta property="article:published_time" content="2024-01-02">'
            '<script type="application/ld+json">{"datePublished":"2020-05-05"}</script>'
        )

        assert _facts(_post(head=head)).published == date(2024, 1, 2)

    def test_time_element_of_the_post(self) -> None:
        html = _post(first='<time datetime="2023-03-04T10:00:00+00:00">March 4</time>')

        assert _facts(html).published == date(2023, 3, 4)

    @pytest.mark.parametrize(
        "value",
        [
            "1970-01-01T00:00:00Z",  # нулевая дата движка
            "2026-02-30",  # такого дня нет
            "not a date",
            (datetime.now(UTC).date() + timedelta(days=30)).isoformat(),  # отложенная публикация
        ],
    )
    def test_impossible_dates_are_none(self, value: str) -> None:
        head = f'<meta property="article:published_time" content="{value}">'

        assert _facts(_post(head=head)).published is None

    def test_no_date_is_none(self) -> None:
        assert _facts(_post()).published is None
