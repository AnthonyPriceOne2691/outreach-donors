from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from backend.config.judge import JudgeMode
from backend.features.runs.exclusions import NOT_WRITING_WORDS, by_name
from backend.features.sales.hypothesis_countries import CountryPolicy
from backend.features.sales.segment_client import (
    CUT_OFF,
    PROMPT_PATH,
    PROMPT_VERSION,
    REASONING_EFFORT,
    SEGMENT_CLOSE,
    SEGMENT_OPEN,
    SITE_CLOSE,
    SITE_OPEN,
    TOKENS_PLAIN,
    TOKENS_REASONING,
    Asked,
    Said,
    SegmentClient,
    Spent,
    build_payload,
    finish_of,
    load_prompt,
    spent_of,
    user_message,
)
from backend.features.sales.segment_facts import (
    KINDS,
    MADE_UP,
    NO_QUOTE,
    SHORT_QUOTE,
    SiteFacts,
    made_up,
)
from backend.features.sales.segment_models import Decider, Lane
from backend.features.sales.segment_quote import MIN_QUOTE_WORDS, quote_words
from backend.features.sales.segment_rules import BY_LANGUAGE, BY_RULE, RULES, Case, Rule, first
from backend.features.sales.segment_scope import Scope
from backend.features.sales.segment_text import (
    HOME_DESCRIPTION,
    HOME_MENU,
    HOME_TEXT,
    HOME_TITLE,
    LABELS,
    MAX_TEXT_CHARS,
    PAGE_DESCRIPTION,
    PAGE_TITLE,
    SiteText,
    site_language,
    site_text,
)

#: Что судья отдаёт наружу. Вызов модели — `segment_client.py`, текст сайта — `segment_text.py`,
#: факты — `segment_facts.py`, правила — `segment_rules.py`; окно, eval и тесты берут имена и отсюда.
__all__ = [
    "CUT_OFF",
    "HOME_DESCRIPTION",
    "HOME_MENU",
    "HOME_TEXT",
    "HOME_TITLE",
    "JUDGE_OFF",
    "KINDS",
    "LABELS",
    "MADE_UP",
    "MAX_TEXT_CHARS",
    "MIN_QUOTE_WORDS",
    "NO_QUOTE",
    "OPERATION",
    "PAGE_DESCRIPTION",
    "PAGE_TITLE",
    "PROMPT_PATH",
    "PROMPT_VERSION",
    "REASONING_EFFORT",
    "SEGMENT_CLOSE",
    "SEGMENT_OPEN",
    "SHORT_QUOTE",
    "SITE_CLOSE",
    "SITE_OPEN",
    "TOKENS_PLAIN",
    "TOKENS_REASONING",
    "Asked",
    "Said",
    "SegmentClient",
    "SegmentVerdict",
    "SiteFacts",
    "SiteText",
    "Spent",
    "before_model",
    "build_payload",
    "by_host",
    "by_language",
    "by_text",
    "decide",
    "effective",
    "finish_of",
    "load_prompt",
    "made_up",
    "quote_words",
    "segment_digest",
    "settle",
    "site_language",
    "site_text",
    "spent_of",
    "user_message",
]

#: Операция журнала расхода (`core/usage.OPERATION_PROVIDERS`); её считает потолок продаж.
OPERATION = "sales_segment_judge"


def segment_digest(description: str) -> str:
    """Отпечаток описания сегмента: правка описания — новый вопрос модели, и прежний
    вердикт на него не годится. Пробелы не в счёт — их правка вопрос не меняет."""
    return hashlib.sha256(" ".join(description.split()).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class SegmentVerdict:
    """Вердикт судьи и то, чем он подтверждён."""

    lane: Lane
    reason: str
    quote: str | None = None
    model: str | None = None
    tokens: int = 0
    decided_by: Decider = Decider.MODEL
    #: Модель не ответила: вердикта нет, на домен не ложится, кэшем не становится.
    unanswered: bool = False
    #: Токены раздельно, как их назвал провайдер (`Spent`): ввод и вывод стоят по-разному.
    spent: Spent | None = None
    #: Вид сайта, как его назвала модель (`KINDS`); пусто — не назвала или вердикт не модели.
    kind: str | None = None
    #: Ответ обрезан потолком вывода (`CUT_OFF`): вердикта нет, как у неответившей модели.
    cut_off: bool = False
    #: Факты модели (`segment_facts.SiteFacts`) — их хранит кэш, по ним пересуд ставит полосу.
    facts: SiteFacts | None = None
    #: Правило таблицы, решившее полосу (`segment_rules.RULES`): разбивка eval и отчёта.
    rule: str | None = None
    #: У ключа модели нет кредитов: вердикта нет, проход встаёт (`segment_window`).
    no_credits: bool = False
    #: Второй шанс цитате (`segment_client.SegmentClient.judge`): модель спросили второй раз — и
    #: цитата нового ответа нашлась. Итог прохода считает их рядом с токенами (`segment_judging`).
    retried: bool = False
    retry_held: bool = False

    @property
    def would_cut(self) -> bool:
        """Отрезал бы в `enforce`: в наблюдении считается именно это."""
        return self.lane is Lane.REJECT

    @property
    def proven(self) -> bool:
        """Вердикт, который ложится в кэш: правило или факты модели с цитатой из текста. Без
        фактов — не дала цитаты, цитата не нашлась в тексте и после второго вопроса, ответ не
        разобран, не ответила — это несработавшая проверка: следующий суд спросит снова."""
        return self.decided_by is Decider.RULE or self.facts is not None


def _ruled(lane: Lane, reason: str) -> SegmentVerdict:
    return SegmentVerdict(lane, reason, decided_by=Decider.RULE)


def by_host(host: str) -> SegmentVerdict | None:
    """«Кому не пишем» по одному имени домена — общим правилом прогона доноров
    (`runs/exclusions.by_name`) и его словами, до главной: сайт такому домену не открываем."""
    why = by_name([host]).get(host)
    return None if why is None else _ruled(Lane.REJECT, NOT_WRITING_WORDS[why])


def by_text(text: SiteText) -> SegmentVerdict | None:
    """Правила по тексту: пустое и страница-отказ — человеку. Заглушка только в строках главной —
    не отказ: модель судит выдачу (`SiteText.without_stub`)."""
    if text.empty:
        return _ruled(Lane.REVIEW, "выдача и главная не дали текста — судить не по чему")
    if text.without_stub().stubbed:
        return _ruled(Lane.REVIEW, "вместо страницы пришёл отказ доступа — судить не по чему")
    return None


def by_language(text: SiteText, letters: str | None) -> SegmentVerdict | None:
    """Язык сайта не тот, что у писем, — вердикт без модели: назван буквами — «отказал бы» словами
    («язык сайта — украинский, письма — русский»); не назван — «не понять». Не жёсткое правило
    (`Decider.LANGUAGE`): режим решает с ним, как с вердиктом модели (`effective`)."""
    found = site_language(text, letters)
    if found is None:
        return None
    lane = Lane.REJECT if found.code else Lane.REVIEW
    return SegmentVerdict(lane, found.words, decided_by=Decider.LANGUAGE)


def before_model(host: str, text: SiteText) -> SegmentVerdict | None:
    """Всё, что решается без модели и не стоит ни одного токена: «кому не пишем» по имени
    режется, пустое и отказ — человеку."""
    return by_host(host) or by_text(text)


@dataclass(frozen=True, slots=True)
class _Early:
    """Вердикты до фактов: правило по домену и тексту, язык сайта."""

    ruled: SegmentVerdict | None = None
    language: SegmentVerdict | None = None


def _verdict(rule: Rule, case: Case, said: Said, early: _Early) -> SegmentVerdict:
    """Правило таблицы → вердикт: до фактов — вердикт правила или языка как есть (с токенами
    вызова и фактами, если модель спрашивали); по фактам — слова правила и пояснение модели."""
    # Полоса — из таблицы и здесь: условие правила и вердикт до фактов совпадают по построению.
    if rule.name in BY_RULE and early.ruled is not None:
        return replace(early.ruled, lane=rule.lane, rule=rule.name)
    if rule.name in BY_LANGUAGE and early.language is not None:
        # Без модели у вердикта: по ней отмена решения узнаёт вердикт языка (`segment_undo`).
        return replace(
            early.language,
            lane=rule.lane,
            tokens=said.tokens,
            spent=said.spent,
            facts=said.facts,
            rule=rule.name,
            retried=said.retried,
            retry_held=said.retry_held,
        )
    facts = said.facts
    words = rule.words(case)
    # Пояснение модели — вторым предложением: с заглавной (у письменностей без регистра — как есть).
    why = facts.why if facts is not None else ""
    return SegmentVerdict(
        rule.lane,
        f"{words}. {why[:1].upper()}{why[1:]}" if why else words,
        facts.quote if facts is not None else None,
        said.model,
        said.tokens,
        Decider.MODEL,
        unanswered=said.unanswered or rule.name == "scope_failed",
        spent=said.spent,
        kind=facts.kind if facts is not None else None,
        cut_off=said.cut_off,
        facts=facts,
        rule=rule.name,
        no_credits=said.no_credits,
        retried=said.retried,
        retry_held=said.retry_held,
    )


def settle(
    *,
    host: str,
    policy: CountryPolicy,
    scope: Scope | None,
    said: Said | None = None,
    ruled: SegmentVerdict | None = None,
    language: SegmentVerdict | None = None,
    rules: tuple[Rule, ...] = RULES,
) -> SegmentVerdict:
    """Вердикт по таблице правил (`segment_rules.RULES`): правило до модели, язык, факты модели,
    разбор описания и поле гипотезы. Модели не зовёт: факты из кэша решаются здесь же."""
    said = said or Said(None)
    case = Case(host, policy, scope, said.facts, said.failure, ruled, language)
    return _verdict(first(case, rules), case, said, _Early(ruled, language))


async def decide(
    client: SegmentClient,
    *,
    host: str,
    segment: str,
    text: SiteText,
    scope: Scope | None,
    policy: CountryPolicy,
    system: str | None = None,
    letters: str | None = None,
) -> SegmentVerdict:
    """Правила до модели, язык сайта против языка писем `letters` (`by_language`), затем факты
    модели — по тексту без главной-заглушки (`SiteText.without_stub`) — и таблица правил. Язык назван
    буквами и не тот — модель не зовётся. Не бросает. `system` — промпт вместо файла: так eval гоняет
    испорченный промпт, не трогая файл."""
    ruled = before_model(host, text)
    if ruled is not None:
        return settle(host=host, policy=policy, scope=scope, ruled=ruled)
    judged = text.without_stub()
    language = by_language(judged, letters)
    if language is not None and language.lane is Lane.REJECT:
        return settle(host=host, policy=policy, scope=scope, language=language)
    said = await client.judge(host=host, segment=segment, text=judged, system=system)
    return settle(host=host, policy=policy, scope=scope, said=said, language=language)


#: Полоса кандидата при выключенном судье: модель не зовётся, решает человек.
JUDGE_OFF = "судья сегмента выключен — решает человек"


def effective(verdict: SegmentVerdict, mode: JudgeMode) -> tuple[Lane, str]:
    """Полоса кандидата по вердикту и режиму (`JudgeMode`, как у судьи площадки).

    Правило режет в любом режиме. В наблюдении (`shadow`) отказ модели или языка сайта не отсекает
    сайт, а кладёт его в свою полосу «судья отказал бы» (`Lane.WOULD_REJECT`; вариант (b), решение
    владельца 20.10): «на проверке» — только сомнительные, решать по отказам человеку не обязательно,
    лида без его «Принять» нет. Причина — слова судьи как есть: что это отказ, говорит полоса. Отмена
    решения человека возвращает сайт сюда же (`segment_undo`)."""
    if verdict.decided_by is Decider.RULE or mode is JudgeMode.ENFORCE:
        return verdict.lane, verdict.reason
    if verdict.lane is Lane.REJECT:
        return Lane.WOULD_REJECT, verdict.reason
    return verdict.lane, verdict.reason
