"""Раздел «Продажи»: что уходит на экран — мастеру загрузки, вкладкам гипотез и лидов, —
и что приходит из окна «Новая гипотеза»; что держит гипотезу — окну гипотезы."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.api.sales.segment_schemas import SEGMENT_LENGTH
from backend.features.sales.browse import HypothesisRow, LeadRow, LeadsPage
from backend.features.sales.columns import LeadField
from backend.features.sales.intake import Lead, Preview, Problem
from backend.features.sales.lead_values import Defaults
from backend.features.sales.models import LeadSource, LeadStatus
from backend.features.sales.verifier import unproven


class IntakeDefaults(BaseModel):
    """Умолчания загрузки, как их понял сервер: язык кодом, страна кодом и пояс."""

    language: str | None
    country: str | None
    timezone: str | None

    @classmethod
    def of(cls, given: Defaults) -> IntakeDefaults:
        where = given.place
        country, zone = (where.country, where.timezone) if where else (None, None)
        return cls(language=given.language, country=country, timezone=zone)


class IntakeView(BaseModel):
    """Предпросмотр или итог загрузки. `loaded` пуст — в базу ничего не записано."""

    source: str
    header: bool
    #: Имена колонок или «колонка N», если заголовка нет.
    columns: list[str]
    #: Первые строки данных — по ним человек сопоставляет колонки руками.
    sample: list[list[str]]
    mapping: dict[LeadField, int]
    #: Колонка почты не найдена: лиды не считались, нужно сопоставить руками.
    needs_mapping: bool
    rows: int
    accepted: int
    rejected: int
    #: Первые лиды — что получится; сколько всего — `accepted`.
    leads: list[Lead]
    #: Отчёт целиком: каждая отклонённая строка и каждое замечание.
    problems: list[Problem]
    defaults: IntakeDefaults
    #: Сколько лидов получили язык и страну или пояс по умолчанию: колонка была пуста.
    defaulted_language: int
    defaulted_place: int
    #: Без чего письма не уйдут — словами, с числом лидов; до загрузки, а не молча.
    warnings: list[str]
    loaded: int | None = None

    @classmethod
    def of(cls, found: Preview, *, shown: int, loaded: int | None = None) -> IntakeView:
        return cls(
            source=found.source,
            header=found.header,
            columns=found.columns,
            sample=found.sample,
            mapping=found.mapping,
            needs_mapping=found.needs_mapping,
            rows=found.rows,
            accepted=len(found.leads),
            rejected=found.rejected,
            leads=found.leads[:shown],
            problems=found.problems,
            defaults=IntakeDefaults.of(found.defaults),
            defaulted_language=found.defaulted_language,
            defaulted_place=found.defaulted_place,
            warnings=found.warnings,
            loaded=loaded,
        )


class HypothesisBody(BaseModel):
    """Новая гипотеза из окна «Новая гипотеза»: имя и, если есть, описание словами.

    Пробелы и пустоту имени судит ядро (`hypotheses.add`) — его отказ словами, а не схема;
    лишнее поле — отказ схемы: опечатка в имени поля не должна молча ничего не менять."""

    model_config = ConfigDict(extra="forbid")

    name: str
    #: Предел — тот же, что у правки описания сегмента: поле одно, запись одна.
    description: str | None = Field(default=None, max_length=SEGMENT_LENGTH)


class HypothesisHolds(BaseModel):
    """Что держит гипотезу — окну гипотезы до нажатия «Удалить»: вид → сколько (только
    ненулевые) и строка под закрытой кнопкой словами сервера. `held` пуст — гипотеза пустая и
    удаляется; сервер всё равно считает заново под блокировкой и отвечает 409 словами."""

    hypothesis_id: int
    holds: dict[str, int]
    held: str | None


class HypothesisCard(BaseModel):
    """Гипотеза и сколько у неё лидов в каждом состоянии: по ней выбирают,
    куда грузить базу и откуда брать готовых к письмам."""

    id: int
    name: str
    description: str | None
    #: Поле «страны компаний» (`hypothesis_countries`): переключатель и коды «не пишем».
    other_countries: str
    excluded_countries: list[str]
    created_at: datetime
    #: Состояние → сколько. Все состояния названы, хоть и нулём.
    leads: dict[str, int]
    total: int
    #: Лидов «готов», которых очистка проверит заново: адрес проверял выдуманный проверяльщик,
    #: а задан живой. Их тоже ждёт кнопка «Очистить».
    recheck: int
    #: Сколько лидов возьмёт очистка — число у кнопки «Очистить», тем же условием, что у
    #: прохода: новый лид с первым ответом `unknown` ждёт дня перепроверки.
    waiting: int

    @classmethod
    def of(cls, row: HypothesisRow) -> HypothesisCard:
        found = row.hypothesis
        return cls(
            id=found.id,
            name=found.name,
            description=found.description,
            other_countries=found.other_countries,
            excluded_countries=list(found.excluded_countries),
            created_at=found.created_at,
            leads=row.states,
            total=row.total,
            recheck=row.recheck,
            waiting=row.waiting,
        )


class CountriesView(BaseModel):
    """Коды стран ISO 3166-1 alpha-2, которые годятся в «не пишем компаниям из» (таблица стран
    модуля, `geo.COUNTRIES`). Названия экран берёт у браузера (`Intl.DisplayNames`)."""

    codes: list[str]


class HypothesesView(BaseModel):
    rows: list[HypothesisCard]
    total: int
    #: Модуль продаж включён. Выключен — экран говорит это строкой под шапкой раздела: список
    #: гипотез раздел грузит на любой вкладке и в мастере загрузки, отдельного запроса нет.
    module_enabled: bool


class LeadCard(BaseModel):
    """Строка лида: кто он, где работает, куда попал после очистки и почему —
    кодом (`rejection_reason`, по нему фильтр) и словами (`cleaning_note`)."""

    id: int
    email: str
    name: str | None
    position: str | None
    company: str | None
    #: Домен компании — слово, а не `domain_id`: его читают в строке.
    host: str
    country: str | None
    timezone: str | None
    language: str | None
    hypothesis_id: int
    hypothesis: str
    source: LeadSource
    status: LeadStatus
    rejection_reason: str | None
    cleaning_note: str | None
    verification_status: str | None
    #: Почему адрес не проверен по-настоящему — словами: выдуманный вердикт при живом
    #: проверяльщике (`verifier.unproven`). Такой лид ждёт очистки, письма ему не идут.
    unproven: str | None
    #: Почему лид «готов» ждёт письма — словами: сайт в работе у другого направления
    #: (`elsewhere.waiting_of`); письмо соберётся, когда сайт освободится.
    elsewhere: str | None = None
    created_at: datetime

    @classmethod
    def of(cls, row: LeadRow) -> LeadCard:
        lead = row.lead
        return cls(
            id=lead.id,
            email=lead.email,
            name=lead.name,
            position=lead.position,
            company=lead.company,
            host=row.host,
            country=lead.country,
            timezone=lead.timezone,
            language=lead.language,
            hypothesis_id=lead.hypothesis_id,
            hypothesis=row.hypothesis,
            source=lead.source,
            status=lead.status,
            rejection_reason=lead.rejection_reason,
            cleaning_note=lead.cleaning_note,
            verification_status=lead.verification_status,
            unproven=unproven(lead),
            elsewhere=row.elsewhere,
            created_at=lead.created_at,
        )


class LeadsView(BaseModel):
    rows: list[LeadCard]
    total: int
    #: Какая это страница и сколько на ней лидов: размер страницы знает сервер.
    page: int
    limit: int
    #: Сводка по всем лидам, не по фильтру: состояние → сколько, причина → сколько.
    #: Ключи — полный перечень кодов: по ним экран строит плитки и фильтры.
    states: dict[str, int]
    reasons: dict[str, int]

    @classmethod
    def of(cls, page: LeadsPage, *, page_number: int, limit: int) -> LeadsView:
        return cls(
            rows=[LeadCard.of(row) for row in page.rows],
            total=page.total,
            page=page_number,
            limit=limit,
            states=page.states,
            reasons=page.reasons,
        )
