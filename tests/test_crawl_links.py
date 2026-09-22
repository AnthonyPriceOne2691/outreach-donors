"""Тело статьи и ссылки из него.

Половина проверок здесь — про то, чего брать НЕ надо: ссылку из меню,
ссылку из подвала, ссылку на свой же домен. Допуск по ложным
рекламодателям десять процентов, и навигация донора выберет его одна.

Вторая половина — грабли, оплаченные ложными инцидентами в соседней
системе: `rel` через запятую, анкор картинкой, регистр и диакритика.
"""

from __future__ import annotations

from backend.features.crawl.article import (
    MIN_BODY_CHARS,
    BodySource,
    extract_article,
    link_density,
)
from backend.features.crawl.links import (
    anchor_text,
    collect_links,
    compare_key,
    harvest,
    parse_rel,
)
from selectolax.parser import HTMLParser

HOST = "donor.test"
LONG = "Текст статьи про ставки и коэффициенты. " * 20


def _page(body: str, *, extra: str = "") -> str:
    return f"""
    <html><body>
      <nav><a href="https://sponsor-nav.test/">Реклама в меню</a></nav>
      <header><a href="https://sponsor-header.test/">Шапка</a></header>
      {body}
      <aside class="related"><a href="https://sponsor-aside.test/">Читайте также</a></aside>
      <footer><a href="https://sponsor-footer.test/">Партнёр в подвале</a></footer>
      {extra}
    </body></html>
    """


def _links(html: str, page_url: str = f"https://{HOST}/post/1") -> list[str]:
    article = extract_article(html)
    assert article is not None, "тело статьи не выделено"
    return [link.target_root for link in collect_links(article.node, page_url, HOST)]


class TestArticleBody:
    def test_semantic_markup_wins(self) -> None:
        """Разметка сайта сильнее нашей догадки: она говорит, что автор
        считает статьёй, а догадка — что мы думаем о его вёрстке."""
        html = _page(f"<article><p>{LONG}</p></article>")

        article = extract_article(html)

        assert article is not None
        assert article.source is BodySource.SEMANTIC

    def test_engine_class_is_the_second_way(self) -> None:
        html = _page(f'<div class="entry-content"><p>{LONG}</p></div>')

        article = extract_article(html)

        assert article is not None
        assert article.source is BodySource.CONTENT_CLASS

    def test_density_is_the_last_resort(self) -> None:
        html = _page(f'<div class="wrap"><p>{LONG}</p><p>{LONG}</p></div>')

        article = extract_article(html)

        assert article is not None
        assert article.source is BodySource.DENSITY

    def test_layout_modifier_does_not_eat_the_article(self) -> None:
        """Замер 22.09.2026, живой донор: статья лежала в
        `<main class="site-content has-sidebar …">`, и слово `sidebar`
        внутри имени РАСКЛАДКИ уносило `<main>` вместе со статьёй.

        Четыре страницы из двадцати выглядели как «не статья» — то есть
        по этому донору обход находил ноль рекламодателей и молчал об этом.
        Настоящий сайдбар там же рядом, `<aside>`, и он убирается по тегу.
        """
        html = _page(
            f'<main class="site-content has-sidebar post-73425"><p>{LONG}</p></main>'
            '<aside class="sidebar"><a href="https://x.test/">меню</a></aside>'
        )

        article = extract_article(html)

        assert article is not None, "модификатор раскладки съел статью"
        assert LONG[:40] in article.text

    def test_real_sidebar_is_still_dropped(self) -> None:
        """Починка не должна оставить настоящий сайдбар: имя блока,
        начинающееся с маркера, — это по-прежнему шум."""
        html = _page(
            f'<div class="entry-content"><p>{LONG}</p></div>'
            '<div class="sidebar-wrapper"><a href="https://ads.test/">реклама</a></div>'
        )

        article = extract_article(html)

        assert article is not None
        assert "ads.test" not in article.node.html

    def test_wrapper_holding_the_whole_page_is_never_noise(self) -> None:
        """Страховка на имя, которого нет в списке: блок, в котором лежит
        почти весь текст страницы, — обёртка вёрстки, а не шум.

        Без неё одно неудачное имя класса превращает страницу в «не
        статья», и отличить это от честного «страница статьёй не является»
        нельзя ничем.
        """
        html = _page(f'<div class="menu-and-everything-else"><p>{LONG}</p><p>{LONG}</p></div>')

        article = extract_article(html)

        assert article is not None, "обёртка вёрстки выброшена как меню"

    def test_page_without_an_article_is_none_not_empty(self) -> None:
        """Раздел со списком и карточка товара статьями не являются.
        Вернув пустое тело, мы записали бы «ссылок нет» там, где их
        не искали."""
        html = _page('<div class="listing"><a href="https://x.test/">ссылка</a></div>')

        assert extract_article(html) is None

    def test_navigation_is_not_a_body_even_when_long(self) -> None:
        """Меню бывает длиннее статьи. Отличает их плотность ссылок,
        а не длина: это единственный признак, не зависящий от движка."""
        menu = "".join(
            f'<p><a href="https://x{n}.test/">Раздел {n} сайта</a></p>' for n in range(60)
        )
        html = f"<html><body><div class='wrap'>{menu}</div></body></html>"

        assert extract_article(html) is None

    def test_link_density_separates_menu_from_text(self) -> None:
        menu = HTMLParser(
            "<div><a href='/a'>раздел один</a><a href='/b'>раздел два</a></div>"
        ).css_first("div")
        text = HTMLParser(f"<div><p>{LONG}</p><a href='/a'>раз</a></div>").css_first("div")

        assert link_density(menu) > 0.9
        assert link_density(text) < 0.2

    def test_short_text_is_not_a_body(self) -> None:
        html = _page(f"<article><p>{'к' * (MIN_BODY_CHARS - 100)}</p></article>")

        assert extract_article(html) is None


class TestLinksFromBodyOnly:
    def test_navigation_and_footer_stay_out(self) -> None:
        """Главная причина, по которой тело выделяется отдельно: меню
        и подвал дают ссылки на каждой странице сайта."""
        html = _page(
            f'<article><p>{LONG}</p><p><a href="https://advertiser.test/">оффер</a></p></article>'
        )

        assert _links(html) == ["advertiser.test"]

    def test_related_block_inside_article_stays_out(self) -> None:
        """Блок «читайте также» лежит внутри `<article>` у половины
        движков — и его ссылки не редакционные."""
        html = _page(
            f"<article><p>{LONG}</p>"
            '<div class="related-posts"><a href="https://noise.test/">похожее</a></div>'
            '<p><a href="https://advertiser.test/">оффер</a></p></article>'
        )

        assert _links(html) == ["advertiser.test"]

    def test_own_subdomain_is_not_an_advertiser(self) -> None:
        html = _page(
            f"<article><p>{LONG}</p>"
            f'<p><a href="https://blog.{HOST}/x">свой раздел</a>'
            f'<a href="/relative">свой адрес</a>'
            f'<a href="https://advertiser.test/">оффер</a></p></article>'
        )

        assert _links(html) == ["advertiser.test"]

    def test_non_pages_are_skipped(self) -> None:
        html = _page(
            f"<article><p>{LONG}</p><p>"
            '<a href="mailto:a@b.test">почта</a>'
            '<a href="tel:+123">телефон</a>'
            '<a href="javascript:void(0)">кнопка</a>'
            '<a href="#top">наверх</a>'
            '<a href="https://advertiser.test/">оффер</a></p></article>'
        )

        assert _links(html) == ["advertiser.test"]

    def test_one_advertiser_one_link_per_page(self) -> None:
        """Статья, сославшаяся на домен трижды, — это одно размещение."""
        html = _page(
            f"<article><p>{LONG}</p><p>"
            '<a href="https://advertiser.test/a">раз</a>'
            '<a href="https://advertiser.test/a">два</a></p></article>'
        )

        assert _links(html) == ["advertiser.test"]


class TestAnchorAndRel:
    @staticmethod
    def _one(inner: str):  # type: ignore[no-untyped-def]
        html = _page(f"<article><p>{LONG}</p><p>{inner}</p></article>")
        article = extract_article(html)
        assert article is not None
        links = collect_links(article.node, f"https://{HOST}/post/1", HOST)
        assert len(links) == 1
        return links[0]

    def test_rel_separated_by_comma(self) -> None:
        """`rel="nofollow,ugc"` по пробелам даёт токен `nofollow,` —
        и ссылка считается dofollow. Стоило ложного инцидента."""
        assert parse_rel("nofollow,ugc") == {"nofollow", "ugc"}
        assert parse_rel(" sponsored ,  nofollow ") == {"sponsored", "nofollow"}
        assert parse_rel(None) == frozenset()

    def test_comma_separated_rel_is_not_dofollow(self) -> None:
        link = self._one('<a href="https://x.test/" rel="nofollow,ugc">оффер</a>')

        assert link.nofollow is True
        assert link.ugc is True
        assert link.dofollow is False

    def test_sponsored_alone_is_not_dofollow(self) -> None:
        """`sponsored` и `ugc` тоже говорят «вес не передавать».
        Посчитав такую ссылку dofollow, мы завысили бы её балл."""
        assert self._one('<a href="https://x.test/" rel="sponsored">о</a>').dofollow is False
        assert self._one('<a href="https://x.test/">о</a>').dofollow is True

    def test_anchor_from_image_alt(self) -> None:
        """У ссылки-баннера текста нет. Пустой анкор — это картинка,
        а не отсутствие анкора."""
        link = self._one('<a href="https://x.test/"><img src="/b.png" alt="Бонус 100%"></a>')

        assert link.anchor == "Бонус 100%"

    def test_anchor_from_title_when_nothing_else(self) -> None:
        link = self._one('<a href="https://x.test/" title="Играть"><img src="/b.png"></a>')

        assert link.anchor == "Играть"

    def test_anchor_text_beats_image(self) -> None:
        node = HTMLParser('<a href="/x"><img alt="картинка">Текст ссылки</a>').css_first("a")

        assert anchor_text(node) == "Текст ссылки"

    def test_compare_key_ignores_case_and_diacritics(self) -> None:
        """Вёрстка донора переводит заголовок в капс, а человек набирает
        без диакритики. Побайтовое сравнение дало бы расхождение на обоих."""
        assert compare_key("Bet365 Promoties") == compare_key("bet365 promoties")
        assert compare_key("versión móvil") == compare_key("version movil")
        assert compare_key("  два   пробела ") == "два пробела"

    def test_shown_anchor_keeps_its_own_form(self) -> None:
        """Ключ сравнения — не замена показываемой форме: её читает
        человек в карточке."""
        link = self._one('<a href="https://x.test/">Bet365 Promoties</a>')

        assert link.anchor == "Bet365 Promoties"
        assert link.anchor_key == "bet365 promoties"


class TestUnknownSuffix:
    """Список публичных суффиксов вшит снимком и стареет.

    Домен в зоне, которой снимок не знает, не должен исчезать молча:
    в отчёте это выглядело бы как «ссылок нет», а на деле мы потеряли
    рекламодателя в новой зоне.
    """

    def test_link_in_an_unknown_zone_is_kept_and_marked(self) -> None:
        html = _page(
            f"<article><p>{LONG}</p>"
            f'<p><a href="https://advertiser.zonewedontknow/">оффер</a></p></article>'
        )
        article = extract_article(html)
        assert article is not None

        links = collect_links(article.node, "https://donor.com/post/1", "donor.com")

        assert [link.target_root for link in links] == ["advertiser.zonewedontknow"]
        assert links[0].root_guessed is True

    def test_known_zone_is_not_marked(self) -> None:
        html = _page(
            f'<article><p>{LONG}</p><p><a href="https://ad.co.uk/">оффер</a></p></article>'
        )
        article = extract_article(html)
        assert article is not None

        links = collect_links(article.node, "https://donor.com/post/1", "donor.com")

        assert links[0].target_root == "ad.co.uk"
        assert links[0].root_guessed is False

    def test_own_subdomain_stays_internal_in_an_unknown_zone(self) -> None:
        """Ровно то, что поймал тест: при угаданном корне поддомен донора
        сравнивался по корню и выглядел чужим."""
        html = _page(
            f"<article><p>{LONG}</p>"
            f'<p><a href="https://blog.{HOST}/x">свой раздел</a></p></article>'
        )
        article = extract_article(html)
        assert article is not None

        assert collect_links(article.node, f"https://{HOST}/post/1", HOST) == []


class TestHarvestMarksPosition:
    """Ссылка вне тела не выбрасывается, а помечается.

    Требование говорит «ссылки только из тела», и объясняет себя:
    навигация и подвал дают ложных рекламодателей. Замер на боевой нише
    показал третий случай, которого в требовании нет: размещения живут
    в витринах офферов рядом со статьёй. Буква требования оставила бы
    нас без рекламодателей — на трёх донорах из тел вышло три ссылки
    против сотен, которые там есть.
    """

    def test_offer_widget_beside_the_article_is_kept_and_marked(self) -> None:
        html = _page(
            f"<article><p>{LONG}</p>"
            '<p><a href="https://in-body.com/">в статье</a></p></article>'
            '<div class="top-five"><a href="https://offer-widget.com/">Играть</a></div>'
        )

        links = {
            link.target_root: link.in_body
            for link in harvest(html, "https://donor.com/p", "donor.com")
        }

        assert links == {"in-body.com": True, "offer-widget.com": False}

    def test_navigation_and_footer_are_still_dropped(self) -> None:
        """Ровно то, о чём требование и предупреждало: эти ссылки есть
        на каждой странице сайта, и рекламодателем от них никто
        не становится."""
        html = _page(f"<article><p>{LONG}</p></article>")

        roots = {link.target_root for link in harvest(html, "https://donor.com/p", "donor.com")}

        assert roots == set()

    def test_page_without_an_article_still_gives_links(self) -> None:
        """Раздел со списком — не статья, но ссылки на нём бывают,
        и терять их значит терять рекламодателя."""
        html = _page('<div class="listing"><a href="https://offer.com/">оффер</a></div>')

        links = harvest(html, "https://donor.com/p", "donor.com")

        assert [(link.target_root, link.in_body) for link in links] == [("offer.com", False)]

    def test_a_link_is_counted_once_even_if_it_is_in_both_passes(self) -> None:
        html = _page(f'<article><p>{LONG}</p><p><a href="https://one.com/">оффер</a></p></article>')

        links = harvest(html, "https://donor.com/p", "donor.com")

        assert len(links) == 1
        assert links[0].in_body is True
