"""Подвал страницы без тега `<footer>` (сверка ТЗ 11.10.2026): «партнёры из футера».

Требование «кому не пишем» называет партнёров из футера, а обход снимал подвал
только по тегу. Подвал, свёрстанный блоком — `<div class="footer">`, `#footer`,
`.site-footer`, `role="contentinfo"`, — отдавал своих партнёров в кандидаты: во втором
проходе по всей странице (`harvest`) как ссылки «вне тела», а при вёрстке без
разметки статьи — и прямо в тело.

Половина проверок — про то, чего снимать НЕ надо: подвал карточки в витрине
офферов держит кнопку рекламодателя, а обёртка с «подвальным» именем — всю статью.
"""

from __future__ import annotations

import pytest
from backend.features.crawl.article import extract_article
from backend.features.crawl.links import harvest

HOST = "donor.example.test"
PAGE = f"https://{HOST}/post/1"
LONG = "Long editorial paragraph about betting odds and how the market moves. " * 12
PARTNER = "https://partner.example.test/"


def _roots(html: str) -> dict[str, bool]:
    """Домен-получатель → в теле ли статьи ссылка на него."""
    return {link.target_root: link.in_body for link in harvest(html, PAGE, HOST)}


def _page(footer: str) -> str:
    return f"""
    <html><body>
      <div class="wrap">
        <article><p>{LONG}</p><p>See <a href="https://source.example.test/">the source</a>.</p>
        </article>
      </div>
      {footer}
    </body></html>
    """


@pytest.mark.parametrize(
    "footer",
    [
        f'<div class="footer"><a href="{PARTNER}">Our partner</a></div>',
        f'<div id="footer"><a href="{PARTNER}">Our partner</a></div>',
        f'<div class="site-footer dark"><a href="{PARTNER}">Our partner</a></div>',
        f'<section class="page-footer"><a href="{PARTNER}">Our partner</a></section>',
        f'<div id="colophon"><a href="{PARTNER}">Our partner</a></div>',
        f'<div role="contentinfo"><a href="{PARTNER}">Our partner</a></div>',
        f'<div class="footer-widgets"><ul><li><a href="{PARTNER}">Partner</a></li></ul></div>',
        f'<div class="bottom"><div class="footer_partners"><a href="{PARTNER}">P</a></div></div>',
    ],
)
def test_a_footer_built_of_blocks_gives_no_partners(footer: str) -> None:
    roots = _roots(_page(footer))

    assert "partner.example.test" not in roots
    assert roots["source.example.test"] is True, "ссылка статьи на месте"


def test_a_card_footer_in_an_offer_showcase_stays() -> None:
    """Подвал карточки — не подвал страницы: в витрине офферов он держит
    кнопку рекламодателя, и её ищет весь Этап 2."""
    showcase = f"""
      <div class="top-bookmakers">
        <div class="offer-card">
          <div class="card-body">Welcome bonus up to 100</div>
          <div class="card-footer">
            <a href="https://bookie.example.test/?btag=42" rel="sponsored">Claim bonus</a>
          </div>
        </div>
      </div>
      <div class="footer"><a href="{PARTNER}">Our partner</a></div>
    """

    roots = _roots(_page(showcase))

    assert roots.get("bookie.example.test") is False, "кнопка витрины — «рядом со статьёй»"
    assert "partner.example.test" not in roots


@pytest.mark.parametrize("name", ["entry-footer", "post-footer", "modal-footer"])
def test_footers_of_parts_are_not_page_footers(name: str) -> None:
    html = _page(f'<div class="{name}"><a href="https://part.example.test/">x</a></div>')

    assert "part.example.test" in _roots(html)


def test_a_wrapper_named_like_a_footer_keeps_the_article() -> None:
    """Обёртка вёрстки с «подвальным» модификатором держит всю страницу:
    выбросить её значит выбросить статью (`MAX_NOISE_SHARE`)."""
    html = f"""
    <html><body><div id="page" class="layout footer-sticky">
      <p>{LONG}</p><p>Read <a href="https://source.example.test/">the source</a>.</p>
      <p>{LONG}</p>
    </div></body></html>
    """

    assert "source.example.test" in _roots(html)


def test_without_article_markup_the_footer_is_not_taken_as_the_body() -> None:
    """Вёрстка без `<article>` и `<main>`: тело ищется по плотности текста,
    и подвал с абзацами про партнёров уходил в него вместе со статьёй."""
    html = f"""
    <html><body><div class="container">
      <div class="text"><p>{LONG}</p><p>{LONG}</p></div>
      <div class="site-footer">
        <p>Our partners: <a href="{PARTNER}">Partner</a> and friends of the site.</p>
        <p>All rights reserved. Some long legal text about cookies and terms.</p>
      </div>
    </div></body></html>
    """

    article = extract_article(html)

    assert article is not None
    assert "Our partners" not in article.text
    assert "partner.example.test" not in _roots(html)
