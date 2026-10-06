"""Скоринг и дени-лист: куплено, спорно, мимо или «кому не пишем».

Веса требования проверяются буквально — это его строки. Но половина
проверок здесь про другое: про то, что **правил требования на боевой
нише не хватает**, и это замерено, а не предположено. Платные размещения
там `nofollow`, без пометки `sponsored` и вне тела статьи — то есть
по буквальным правилам набирают ноль, а dofollow-награду получают
регистратор домена и счётчик посещаемости.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from backend.features.core.domain import Verdict
from backend.features.crawl.anchors import (
    AnchorKind,
    anchor_kind,
    is_address,
    is_commercial_anchor,
)
from backend.features.crawl.denylist import DenyReason, denial_for
from backend.features.crawl.links import OutLink
from backend.features.crawl.markers import has_marker, marker_reason
from backend.features.crawl.scoring import (
    BOUGHT_AT,
    REVIEW_AT,
    boost_across_donors,
    score_candidates,
    score_link,
)

DONOR = "donor.com"


def _link(
    *,
    root: str = "advertiser.com",
    anchor: str = "подробнее об истории клуба",
    page: str = f"https://{DONOR}/news/1",
    url: str | None = None,
    nofollow: bool = False,
    sponsored: bool = False,
    ugc: bool = False,
    in_body: bool = True,
) -> OutLink:
    return OutLink(
        page_url=page,
        url=url or f"https://{root}/",
        target_host=root,
        target_root=root,
        anchor=anchor,
        anchor_key=anchor.lower(),
        nofollow=nofollow,
        sponsored=sponsored,
        ugc=ugc,
        in_body=in_body,
    )


class TestRequirementWeights:
    """Строки требования проверяются буквально: это его шкала."""

    def test_rel_sponsored_is_the_heaviest(self) -> None:
        score = score_link(_link(sponsored=True))

        assert score.points >= BOUGHT_AT
        assert any("sponsored" in reason for reason in score.reasons)

    def test_marker_in_the_page_address(self) -> None:
        score = score_link(_link(page=f"https://{DONOR}/sponsored/betting-guide"))

        assert score.points >= BOUGHT_AT

    def test_dofollow_from_body_to_a_commercial_domain(self) -> None:
        score = score_link(_link(anchor="Play now", in_body=True))

        # +2 за dofollow из тела и +1 за коммерческий анкор.
        assert score.points == 3

    def test_plain_editorial_link_scores_nothing(self) -> None:
        assert score_link(_link()).points == 0


class TestNicheReality:
    """Замер: платные размещения ниши по буквальным правилам — ноль."""

    def test_commercial_anchor_under_nofollow_is_a_placement(self) -> None:
        """Донор закрыл коммерческую ссылку от передачи веса и не пометил
        её `sponsored`. Требование награждает обратное — dofollow."""
        score = score_link(_link(anchor="Bet now", nofollow=True, in_body=False))

        assert score.points >= REVIEW_AT
        assert any("nofollow" in reason for reason in score.reasons)

    def test_repetition_across_pages_lifts_to_bought(self) -> None:
        """Партнёрская программа получает ссылки с десятков страниц,
        редакционное упоминание — с одной."""
        links = [
            _link(anchor="Bet now", nofollow=True, page=f"https://{DONOR}/news/{n}")
            for n in range(5)
        ]

        candidate = score_candidates(links)[0]

        assert candidate.pages == 5
        assert candidate.verdict is Verdict.BOUGHT
        assert any("страниц донора" in reason for reason in candidate.reasons)

    def test_a_single_editorial_mention_stays_out(self) -> None:
        candidate = score_candidates([_link()])[0]

        assert candidate.verdict is Verdict.SKIPPED

    def test_dofollow_to_infrastructure_is_not_rewarded(self) -> None:
        """Верхушка dofollow-ссылок донора — регистратор и счётчик.
        Награждать их значит писать письма тем, кто ничего не покупал."""
        links = [
            _link(root="statcounter.com", anchor="Web Analytics", page=f"https://{DONOR}/p/{n}")
            for n in range(9)
        ]

        candidate = score_candidates(links)[0]

        assert candidate.verdict is Verdict.BLOCKED


class TestCommercialAnchor:
    def test_word_not_substring(self) -> None:
        """`bet` живёт внутри `better` и `alphabet`: без разбиения
        на слова коммерческим оказывался любой текст."""
        assert is_commercial_anchor("Bet now") is True
        assert is_commercial_anchor("a better alphabet") is False

    def test_phrases_are_caught(self) -> None:
        assert is_commercial_anchor("Sign up today") is True
        assert is_commercial_anchor("claim your bonus") is True

    def test_plain_text_is_not_commercial(self) -> None:
        assert is_commercial_anchor("подробнее об истории клуба") is False


class TestOtherNicheFalsePositives:
    """Боевой обход двух финансовых площадок 06.10: «куплена — пишем» вышло
    10 из 10 ложных, спорных 7 из 7. Каждый класс ошибки — отдельной проверкой,
    рядом — то, что метка и призыв по-прежнему ловятся."""

    def test_a_company_name_in_the_article_address_is_not_a_label(self) -> None:
        """Статья о компании «Partners Group»: подстрока «partner» дала +4
        каждой ссылке страницы — Google, TradingView, Dealroom."""
        page = f"https://{DONOR}/partners-group-managed-investment-trust-heads-liquidation/"

        assert has_marker(_link(page=page)) is False

    def test_a_label_is_a_section_or_the_start_of_the_address(self) -> None:
        for page in (
            f"https://{DONOR}/sponsored/best-loans",
            f"https://{DONOR}/partner-content/best-loans",
            f"https://{DONOR}/guest-posts/best-loans",
            f"https://{DONOR}/sponsored-best-loans",
        ):
            assert has_marker(_link(page=page)) is True, page

    def test_a_word_inside_an_address_anchor_is_not_a_label(self) -> None:
        """Ссылка на регулятора: «/Advertising/» в анкоре-адресе дал +4."""
        link = _link(
            root="regulator.org", anchor="http://www.regulator.org/Industry/Advertising/p1"
        )

        assert has_marker(link) is False
        assert is_commercial_anchor(link.anchor) is False

    def test_a_signed_anchor_is_a_label(self) -> None:
        assert has_marker(_link(anchor="Sponsored by Acme Loans")) is True

    def test_an_address_anchor_is_not_text(self) -> None:
        for anchor in ("http://code.example.com/p/budget/", "www.example.com", "app.dealroom.co"):
            assert is_address(anchor) is True, anchor
        assert is_address("Betway") is False
        assert is_address("Sign up today") is False

    def test_a_short_address_anchor_is_not_a_call(self) -> None:
        """Ссылка на приложение в магазине: «play» внутри адреса — не призыв."""
        assert is_commercial_anchor("play.google.com") is False
        assert is_commercial_anchor("https://visit.example.com") is False

    def test_call_words_inside_an_editorial_phrase_sell_nothing(self) -> None:
        """«get», «code», «now» в редакционной фразе: книга на маркетплейсе,
        фото со стока, старая статья о сериале — все были «куплены»."""
        for anchor in (
            "The Millionaire Fastlane: Crack the Code to Wealth and Live Rich",
            "How to get started with real estate investing",
            "HBO Now",
            "(he's 15 now!)",
        ):
            assert is_commercial_anchor(anchor) is False, anchor

    def test_a_short_call_still_sells(self) -> None:
        for anchor in ("Play now", "Visit site", "Claim offer", "Use code"):
            assert is_commercial_anchor(anchor) is True, anchor

    def test_goods_that_buy_links_everywhere_count_at_any_length(self) -> None:
        """Казино, букмекер, CBD покупают ссылки на донорах любой ниши — их
        название коммерческое в анкоре любой длины. Слово сделки в длинной
        фразе — нет: «бонус» в редакционном тексте ничего не продаёт."""
        assert is_commercial_anchor("the best online casino of the season in one place") is True
        assert is_commercial_anchor("the best welcome bonus of the season in one place") is False

    def test_a_sitewide_button_on_every_article_is_not_bought(self) -> None:
        """Кнопка «Add as preferred source on Google» на 199 статьях одного донора
        и на втором доноре: подстрока «partner» в адресе одной статьи дала +4,
        повторяемость +3, второй донор +3 — десять баллов."""
        links = [
            _link(
                root="google.com",
                anchor="Add as preferred source on Google",
                page=f"https://{DONOR}/partners-group-story-{n}/",
            )
            for n in range(199)
        ]

        candidate = score_candidates(links, donor_root=DONOR)[0]
        boost_across_donors([candidate], {"google.com": 2})

        assert candidate.points == 0
        assert candidate.verdict is Verdict.SKIPPED


class TestDenyList:
    def test_requirement_list(self) -> None:
        assert denial_for("wikipedia.org").reason is DenyReason.REFERENCE  # type: ignore[union-attr]
        assert denial_for("facebook.com").reason is DenyReason.SOCIAL  # type: ignore[union-attr]
        assert denial_for("bbc.com").reason is DenyReason.MEDIA  # type: ignore[union-attr]
        assert denial_for("sars.gov.za").reason is DenyReason.ZONE  # type: ignore[union-attr]
        assert denial_for("mit.edu").reason is DenyReason.ZONE  # type: ignore[union-attr]

    def test_document_is_not_a_site_with_an_owner(self) -> None:
        denial = denial_for("example.com", "https://example.com/report.pdf")

        assert denial is not None
        assert denial.reason is DenyReason.DOCUMENT

    def test_infrastructure_from_the_measurement(self) -> None:
        """Эти два домена стояли в верхушке исходящих ссылок донора:
        триста ссылок на регистратора и двести на счётчик."""
        assert denial_for("regery.com") is not None
        assert denial_for("statcounter.com") is not None

    def test_shortener_hides_whoever_it_wants(self) -> None:
        assert denial_for("bit.ly") is not None

    def test_ordinary_advertiser_passes(self) -> None:
        assert denial_for("betikapartners.com") is None

    def test_subdomains_are_covered_by_the_root(self) -> None:
        """Сравнение по корню: держать в списке `en.wikipedia.org`
        отдельно значит однажды забыть третий поддомен."""
        assert denial_for("wikipedia.org") is not None


class TestVerdictBoundaries:
    def test_blocked_is_not_the_same_as_skipped(self) -> None:
        """Первое — решение списка, второе — решение баллов. Спорить
        с порогом там, где спорить не о чем, значит терять время
        на каждом разборе."""
        blocked = score_candidates([_link(root="facebook.com", anchor="Follow us")])[0]
        skipped = score_candidates([_link()])[0]

        assert blocked.verdict is Verdict.BLOCKED
        assert skipped.verdict is Verdict.SKIPPED

    def test_deny_list_beats_a_high_score(self) -> None:
        """Даже помеченная `sponsored` ссылка на соцсеть — не рекламодатель."""
        links = [
            _link(root="facebook.com", anchor="Bet now", sponsored=True, page=f"/p/{n}")
            for n in range(5)
        ]

        assert score_candidates(links)[0].verdict is Verdict.BLOCKED


class TestCrossDonorBoost:
    def test_two_donors_lift_a_borderline_candidate(self) -> None:
        """Усилитель требования: домен у двух наших доноров почти наверняка
        покупает ссылки."""
        candidates = score_candidates([_link(anchor="Play now", nofollow=True)])
        assert candidates[0].verdict is Verdict.PENDING

        boost_across_donors(candidates, {"advertiser.com": 2})

        assert candidates[0].verdict is Verdict.BOUGHT
        assert any("наших доноров" in reason for reason in candidates[0].reasons)

    def test_one_donor_changes_nothing(self) -> None:
        candidates = score_candidates([_link(anchor="Play now", nofollow=True)])
        before = candidates[0].points

        boost_across_donors(candidates, {"advertiser.com": 1})

        assert candidates[0].points == before

    def test_the_boost_never_revives_a_blocked_domain(self) -> None:
        candidates = score_candidates([_link(root="facebook.com", anchor="Bet now")])

        boost_across_donors(candidates, {"facebook.com": 5})

        assert candidates[0].verdict is Verdict.BLOCKED


class TestRepetitionNeedsACommercialSignal:
    """Найдено первым же прогоном по настоящим данным.

    Скоринг выдал «куплена» регулятору азартных игр: на него по закону
    ссылается каждая страница каждого донора ниши, и одной повторяемости
    хватило на четыре балла. Повторяемость отвечает на вопрос «насколько
    это похоже на схему», а не «реклама ли это вообще».
    """

    def test_mandatory_link_on_every_page_is_not_an_advertiser(self) -> None:
        links = [
            _link(
                root="responsiblegambling.org.za",
                anchor="responsiblegambling.org",
                page=f"https://{DONOR}/post/{n}",
            )
            for n in range(12)
        ]

        candidate = score_candidates(links)[0]

        assert candidate.pages == 12
        assert candidate.points == 0
        assert candidate.verdict is Verdict.SKIPPED
        assert any("сильных признаков рекламы нет" in reason for reason in candidate.reasons)

    def test_cross_donor_boost_does_not_revive_it_either(self) -> None:
        """Регулятор встречается у всех доноров ниши — усилитель про него
        верен и всё равно ничего не значит."""
        links = [
            _link(
                root="responsiblegambling.org.za",
                anchor="responsiblegambling.org",
                page=f"https://{DONOR}/post/{n}",
            )
            for n in range(12)
        ]
        candidates = score_candidates(links)

        boost_across_donors(candidates, {"responsiblegambling.org.za": 5})

        assert candidates[0].verdict is Verdict.SKIPPED

    def test_the_same_repetition_still_lifts_a_commercial_link(self) -> None:
        """Правка не должна была погасить то, ради чего надбавка заведена."""
        links = [
            _link(anchor="Bet now", nofollow=True, page=f"https://{DONOR}/post/{n}")
            for n in range(12)
        ]

        assert score_candidates(links)[0].verdict is Verdict.BOUGHT


class TestOwnBrand:
    """Найдено первым прогоном: донор ссылался на свой же бренд
    в другой зоне и попал в спорные. Корни разные, владелец один,
    и письмо ушло бы ему от его же имени."""

    def test_same_brand_other_zone_is_not_an_advertiser(self) -> None:
        links = [_link(root="sportsboom.com", anchor="Read more at Sportsboom")]

        candidate = score_candidates(links, donor_root="sportsboom.co.za")[0]

        assert candidate.verdict is Verdict.BLOCKED
        assert any("own_brand" in reason for reason in candidate.reasons)

    def test_a_different_brand_in_the_same_zone_passes(self) -> None:
        links = [_link(root="betway.co.za", anchor="Bet now", nofollow=True)]

        candidate = score_candidates(links, donor_root="sportsboom.co.za")[0]

        assert candidate.verdict is not Verdict.BLOCKED

    def test_without_a_donor_the_rule_does_not_fire(self) -> None:
        """Скоринг зовут и без донора — например, на свежих ссылках
        до сохранения. Правило должно молчать, а не угадывать."""
        candidate = score_candidates([_link(root="sportsboom.com")])[0]

        assert candidate.verdict is not Verdict.BLOCKED


class TestArticleLabel:
    """Пометка статьи — то, что сайт сказал о ней сам (`page_facts`):
    рубрика, раздел, фраза раскрытия. Достаётся только ссылкам из тела:
    рядом со статьёй лежит то, что стоит на каждой странице сайта."""

    @staticmethod
    def _labeled(*, in_body: bool = True) -> OutLink:
        return replace(_link(in_body=in_body), page_label="рубрика sponsored-content")

    def test_labeled_article_marks_its_body_links(self) -> None:
        score = score_link(self._labeled())

        assert score.points >= BOUGHT_AT
        assert any("рубрика sponsored-content" in reason for reason in score.reasons)

    def test_label_does_not_reach_links_beside_the_article(self) -> None:
        assert marker_reason(self._labeled(in_body=False)) is None

    def test_marked_address_does_not_reach_links_beside_the_article(self) -> None:
        page = f"https://{DONOR}/sponsored/betting-guide"

        assert marker_reason(_link(page=page, in_body=True)) == "раздел адреса статьи"
        assert marker_reason(_link(page=page, in_body=False)) is None

    def test_caption_in_the_anchor_counts_anywhere(self) -> None:
        """Подпись «Sponsored» — свойство самой ссылки, где бы она ни стояла."""
        assert (
            marker_reason(_link(anchor="Sponsored: Acme Loans", in_body=False))
            == "подпись в анкоре"
        )


TODAY = date(2026, 10, 6)


def _pages(count: int, *, published: date | None = None, **kwargs: object) -> list[OutLink]:
    return [
        replace(_link(page=f"https://{DONOR}/post/{n}", **kwargs), page_published=published)  # type: ignore[arg-type]
        for n in range(count)
    ]


class TestAnyNiche:
    """Оценка не под нишу (слово Anthony 06.10): агентство разбирает разные.

    Финансовый блог 06.10 дал «куплено» 4 из 4 — все ложные, — потому что
    коммерческий анкор узнавали по словам ставок и по «review» где угодно.
    Каждый класс той ошибки — проверкой, рядом — признаки других ниш."""

    @pytest.mark.parametrize(
        "anchor",
        [
            "his review",
            "our review",
            "my year-end review",
            "the odds of winning the Mega Millions lottery",
            "Savings Deposit Program",
            "Fixed term deposit account",
            "bet a $2,222,278",
            "Get Rich Slowly",
            "Visit Paris in spring",
            "Play Store",
            "shop the perimeter",  # совет финансового блога, 06.10
            "subscribe to a newspaper",
            "Buy This, Not That",  # название книги
            "Contributions vs. Returns Calculator",
        ],
    )
    def test_editorial_phrases_of_any_niche_sell_nothing(self, anchor: str) -> None:
        assert anchor_kind(anchor) is AnchorKind.PLAIN, anchor

    @pytest.mark.parametrize(
        "anchor",
        [
            "Start your free trial",  # софт
            "Book now",  # путешествия
            "Shop now",  # магазин
            "Apply now",  # финансы
            "Claim your bonus",  # ставки
            "Sign up today",
            "Buy now",
            "Use code SAVE20",
        ],
    )
    def test_a_call_to_act_is_strong_in_any_niche(self, anchor: str) -> None:
        assert anchor_kind(anchor) is AnchorKind.CALL, anchor

    @pytest.mark.parametrize(
        "anchor",
        ["Acme VPN review", "50% off coupon", "cheap flights to Lisbon", "Acme vs Globex pricing"],
    )
    def test_a_deal_word_is_weak_in_any_niche(self, anchor: str) -> None:
        assert anchor_kind(anchor) is AnchorKind.DEAL, anchor

    def test_the_founders_site_on_hundreds_of_pages_is_not_bought(self) -> None:
        """Сайт основателя блога со 431 страницы: повторяемость без сильного
        признака — автор, а не схема."""
        links = _pages(431, root="founder.com", anchor="my year-end review")

        candidate = score_candidates(links, donor_root=DONOR, today=TODAY)[0]

        assert candidate.verdict is Verdict.SKIPPED
        assert candidate.points == 0

    def test_weak_signals_alone_never_reach_bought(self) -> None:
        """Слово сделки, dofollow из тела, десять страниц — это «спорно»:
        решает человек, а не порог."""
        links = _pages(10, anchor="Acme VPN review")

        candidate = score_candidates(links, today=TODAY)[0]

        assert candidate.verdict is Verdict.PENDING
        assert candidate.strong is False

    def test_weak_signals_are_not_lifted_by_two_donors(self) -> None:
        candidates = score_candidates(_pages(10, anchor="Acme VPN review"), today=TODAY)

        boost_across_donors(candidates, {"advertiser.com": 3})

        assert candidates[0].verdict is Verdict.PENDING

    def test_a_call_repeated_across_pages_is_still_bought(self) -> None:
        """То, ради чего повторяемость заведена: партнёрка с десятков страниц."""
        links = _pages(12, anchor="Sign up today", nofollow=True)

        assert score_candidates(links, today=TODAY)[0].verdict is Verdict.BOUGHT


class TestAffiliateLinks:
    """Партнёрская метка одинакова в любой нише — сильный признак."""

    def test_affiliate_mark_under_nofollow_is_bought(self) -> None:
        link = _link(url="https://advertiser.com/signup?aff_id=77", nofollow=True)

        score = score_link(link)

        assert score.points >= BOUGHT_AT
        assert score.strong is True
        assert any("партнёрская ссылка" in reason for reason in score.reasons)

    def test_affiliate_mark_in_the_body_is_bought(self) -> None:
        link = _link(url="https://advertiser.com/?utm_medium=affiliate&utm_source=donor")

        assert score_link(link).points >= BOUGHT_AT

    def test_network_link_counts_for_the_advertiser_it_names(self) -> None:
        """Письмо уходит рекламодателю, которого сеть назвала, а не сети."""
        url = (
            "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"
            "&ued=https%3A%2F%2Fwww.acmeshop.com%2Fsale"
        )
        link = _link(root="awin1.com", url=url, anchor="Acme Store", nofollow=True)

        candidate = score_candidates([link], today=TODAY)[0]

        assert candidate.target_root == "acmeshop.com"
        assert candidate.verdict is Verdict.BOUGHT
        assert any("awin1.com" in reason for reason in candidate.reasons)

    def test_network_that_hides_the_advertiser_is_not_written_to(self) -> None:
        url = "https://click.linksynergy.com/fs-bin/click?id=abc&offerid=1"
        link = _link(root="linksynergy.com", url=url, anchor="Acme Store", nofollow=True)

        candidate = score_candidates([link], today=TODAY)[0]

        assert candidate.verdict is Verdict.BLOCKED
        assert candidate.denial is not None
        assert candidate.denial.reason is DenyReason.HIDDEN


class TestStalePlacement:
    """Требование: «переманить существующих рекламодателей». Бонус банка
    2007 года, которого давно нет, был «куплено» (финансы, 06.10)."""

    def test_all_old_articles_make_a_former_advertiser(self) -> None:
        links = _pages(4, anchor="Sign up today", nofollow=True, published=date(2007, 3, 21))

        candidate = score_candidates(links, today=TODAY)[0]

        assert candidate.verdict is Verdict.SKIPPED
        assert candidate.stale is True
        assert any("давнее размещение" in reason for reason in candidate.reasons)

    def test_one_fresh_article_keeps_it_current(self) -> None:
        links = _pages(3, anchor="Sign up today", nofollow=True, published=date(2007, 3, 21))
        fresh = _link(anchor="Sign up today", nofollow=True, page=f"https://{DONOR}/post/new")
        links.append(replace(fresh, page_published=date(2026, 5, 1)))

        assert score_candidates(links, today=TODAY)[0].verdict is Verdict.BOUGHT

    def test_unknown_date_is_not_old(self) -> None:
        """Незнание — не давность: статья без даты могла выйти вчера."""
        links = _pages(3, anchor="Sign up today", nofollow=True, published=date(2007, 3, 21))
        links.append(_link(anchor="Sign up today", nofollow=True, page=f"https://{DONOR}/x"))

        assert score_candidates(links, today=TODAY)[0].stale is False

    def test_two_donors_do_not_revive_a_former_advertiser(self) -> None:
        links = _pages(4, anchor="Sign up today", nofollow=True, published=date(2007, 3, 21))
        candidates = score_candidates(links, today=TODAY)

        boost_across_donors(candidates, {"advertiser.com": 3})

        assert candidates[0].verdict is Verdict.SKIPPED

    def test_leap_day_counts_back_without_failing(self) -> None:
        links = _pages(1, anchor="Sign up today", nofollow=True, published=date(2023, 3, 1))

        candidate = score_candidates(links, today=date(2028, 2, 29))[0]

        assert candidate.stale is True


class TestWholePaidArticle:
    """Площадка, продающая посты, метит `rel=sponsored` всю статью — вместе со
    ссылками-источниками. «Куплено» — тот, о ком статья (обход 06.10:
    было 71 «куплено», из них около 45 источников — ethereum.org, coingecko)."""

    @staticmethod
    def _article(slug: str, *targets: tuple[str, str]) -> list[OutLink]:
        page = f"https://{DONOR}/{slug}/"
        return [
            _link(root=root, anchor=anchor, page=page, sponsored=True) for root, anchor in targets
        ]

    def _verdicts(self, links: list[OutLink]) -> dict[str, Verdict]:
        return {c.target_root: c.verdict for c in score_candidates(links, today=TODAY)}

    def test_the_advertiser_named_in_the_address_is_bought_sources_are_not(self) -> None:
        links = self._article(
            "xrp-etf-news-zentrix-announces-final-presale-stage",
            ("zentrixpresale.example", "Zentrix"),
            ("coingecko.com", "market capitalization"),
            ("northfieldfunds.example", "XRP ETF filing"),
        )

        verdicts = self._verdicts(links)

        assert verdicts["zentrixpresale.example"] is Verdict.BOUGHT
        assert verdicts["coingecko.com"] is Verdict.PENDING
        assert verdicts["northfieldfunds.example"] is Verdict.PENDING

    def test_two_names_in_the_address_leave_both_to_a_human(self) -> None:
        """«cardano-price-prediction-…-while-zetafrog-…»: крючок и рекламодатель."""
        links = self._article(
            "cardano-price-prediction-breakout-while-zetafrog-buyers-chase",
            ("cardano.com", "Cardano"),
            ("zetafrog.example", "ZetaFrog"),
            ("cryptowire.example", "analysts"),
        )

        verdicts = self._verdicts(links)

        assert verdicts["cardano.com"] is Verdict.PENDING
        assert verdicts["zetafrog.example"] is Verdict.PENDING

    def test_a_possessive_anchor_is_a_source_not_the_subject(self) -> None:
        """«Solana’s Alpenglow upgrade work» — так ссылаются на чужое."""
        links = self._article(
            "solana-price-today-as-markets-wait",
            ("solana.com", "Solana’s Alpenglow upgrade work"),
            ("coingecko.com", "price data"),
            ("fundflows.example", "fund flows"),
        )

        assert self._verdicts(links)["solana.com"] is Verdict.PENDING

    def test_a_site_marking_whole_articles_is_read_on_short_articles_too(self) -> None:
        """Сайт метит статьи целиком — и статья с двумя ссылками тоже статья."""
        links = [
            *self._article(
                "quickforge-launches-tool",
                ("quickforge.example", "QuickForge"),
                ("news.com", "report"),
            ),
            *self._article(
                "weekly-market-wrap", ("tether.to", "Tether’s USDT"), ("cmc.com", "data")
            ),
            *self._article(
                "restaurant-opens-terrace", ("harbourbistro.example", "birthday dinner")
            ),
        ]

        verdicts = self._verdicts(links)

        assert verdicts["quickforge.example"] is Verdict.BOUGHT
        assert verdicts["harbourbistro.example"] is Verdict.BOUGHT
        assert verdicts["tether.to"] is Verdict.PENDING
        assert verdicts["news.com"] is Verdict.PENDING

    def test_a_single_sponsored_link_elsewhere_keeps_its_full_weight(self) -> None:
        """На сайте, который метит ссылку, а не статью, `rel=sponsored` — как
        в требовании: +5 и «куплено»."""
        links = [_link(sponsored=True), *_pages(6)]

        verdicts = self._verdicts(links)

        assert verdicts["advertiser.com"] is Verdict.BOUGHT


class TestNonprofitZone:
    def test_an_org_is_never_bought_without_a_human(self) -> None:
        candidate = score_candidates([_link(root="ethereum.org", sponsored=True)], today=TODAY)[0]

        assert candidate.verdict is Verdict.PENDING
        assert any("зона .org" in reason for reason in candidate.reasons)


class TestGreyNicheGoods:
    """Букмекер под nofollow — признак размещения, но слабый: новости о ставках
    выглядят так же («online betting surge»). Решает человек (ставки, 06.10)."""

    @pytest.mark.parametrize(
        "anchor",
        [
            "Betway | Soccer Betting & World Cup 2026 Betting",
            "Promotions Free Bet",
            "Online casino",
        ],
    )
    def test_grey_goods_under_nofollow_go_to_a_human(self, anchor: str) -> None:
        candidate = score_candidates([_link(anchor=anchor, nofollow=True)], today=TODAY)[0]

        assert anchor_kind(anchor) is AnchorKind.TRADE
        assert candidate.verdict is Verdict.PENDING

    def test_grey_goods_are_not_multiplied_by_repetition(self) -> None:
        links = _pages(48, anchor="National Betting Board warns the public", nofollow=True)

        assert score_candidates(links, today=TODAY)[0].verdict is Verdict.PENDING


class TestAdNetworks:
    def test_an_ad_network_hides_the_advertiser(self) -> None:
        url = "https://ad.doubleclick.net/ddm/clk/123;456;789"
        link = _link(root="doubleclick.net", url=url, anchor="at Experian.com", sponsored=True)

        candidate = score_candidates([link], today=TODAY)[0]

        assert candidate.verdict is Verdict.BLOCKED
