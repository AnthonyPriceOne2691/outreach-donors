"""Загрузка базы лидов продаж: предпросмотр и загрузка — под правом `sales`.

Источник — файл (`file`) или ссылка на Google-таблицу (`link`), одно из двух,
формой `multipart/form-data`: сервер получает байты как есть, и общий читатель
отклоняет файл не в UTF-8 словами, а не кракозябрами. Сопоставление — JSON
`{"email": 0, "name": 2}` (поле → колонка с нуля); правка руками заменяет
угаданное целиком. Язык и страна или пояс по умолчанию (`default_language`,
`default_place`) ложатся только туда, где колонка пуста; негодное — 400 словами.

**Предпросмотр ничего не пишет**, загрузка пишет лидов и журнал одной транзакцией.
Состояния между шагами сервер не держит: мастер присылает источник оба раза,
таблица по ссылке читается заново.

**Списки раздела — под тем же правом** (срез 1.5): гипотезы со счётчиками и лиды
с фильтрами под колонками, по двадцать на странице, как у отбора. Фильтры и
страница — в адресе экрана (`?state=rejected&reason=duplicate&page=2`), имена
параметров — те же, что в адресе. Логика чтения — `features/sales/browse.py`.

**Период добавления лида** (`added_since`, `added_until`) — как у воронки: моменты с поясом,
полуинтервал `[начало, конец)`; дни человека в них переводит экран (в его адресе — дни,
`added_from` и `added_to`). Не момент или без пояса — 422 словами, что прислать; начало не
раньше конца — 422 теми же словами, что у воронки.

**Гипотеза заводится и здесь** (`POST /sales/hypotheses`) — тем же правилом, что команда
консоли (`features/sales/hypotheses.add`): негодное имя — 422 словами, занятое гипотезой или
рассылкой продаж — 409 словами; заведённая — в журнале действий с автором. Право — `sales`:
описание гипотезы в тексты писем не идёт, его читают экраны и судья сбора базы. **Удаляется
только пустая** (`DELETE /sales/hypotheses/{N}`, право и журнал — как у заведения): держат её
лиды, диалоги, прогоны сбора с сайтами и вердиктами, свой набор цепочки — 409 словами с числами;
что держит, окно гипотезы читает заранее (`GET /sales/hypotheses/{N}/holds`). Правка имени и
описания — `PATCH /sales/hypotheses/{N}` в модуле сбора базы (`segment.py`).

**База знаний и отправитель** (срез 3.1) — своим модулем `kb.py`, **цепочка писем**
(срез 4.6) — модулем `chain.py`, **очередь писем** (срез 4.6b) — модулем `queue.py`,
**воронка** (срез 5.4) — модулем `funnel.py`, **очистка лидов** — модулем `clean.py`,
**качество агента** (срез 6.4) — модулем `calibration.py`, **добивки лида** (какими уйдут) —
модулем `queue.py`, **правка лида** — модулем `leads.py`, **стоп-лист продаж** — модулем
`stoplist.py`, **сбор базы гипотезы** (срез 6.1) — модулем `segment.py`, **недельный отчёт**
(срез 6.3) — модулем `weekly.py`, **«Передать телемаркетологу»** — модулем `handoff.py`,
**рассылка** (адреса частями) — модулем `mailing.py`; их маршруты входят в этот же роутер: у
раздела один префикс.
Права — как в ядре: смотреть и заводить гипотезу — `sales`; очистка и сбор базы — `sales` и
`run` (платная проверка адресов, как поиск адресов); запись цепочки, базы знаний, отправителя и
стоп-листа, правка лида, сборка очереди и передача лида телемаркетологу — `sales` и `send`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    NonNegativeInt,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales import calibration as calibration_routes
from backend.api.sales import chain as chain_routes
from backend.api.sales import clean as clean_routes
from backend.api.sales import funnel as funnel_routes
from backend.api.sales import handoff as handoff_routes
from backend.api.sales import kb as kb_routes
from backend.api.sales import leads as lead_routes
from backend.api.sales import mailing as mailing_routes
from backend.api.sales import queue as queue_routes
from backend.api.sales import segment as segment_routes
from backend.api.sales import segment_again as segment_again_routes
from backend.api.sales import segment_helper as segment_helper_routes
from backend.api.sales import segment_sites as segment_sites_routes
from backend.api.sales import stoplist as stoplist_routes
from backend.api.sales import weekly as weekly_routes
from backend.api.sales.funnel import EMPTY_PERIOD
from backend.api.sales.schemas import (
    CountriesView,
    HypothesesView,
    HypothesisBody,
    HypothesisCard,
    HypothesisHolds,
    IntakeView,
    LeadsView,
)
from backend.config import sales as sales_cfg
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import browse, hypotheses, hypothesis_holds, intake, sheet
from backend.features.sales.columns import LeadField, Mapping
from backend.features.sales.funnel import Period
from backend.features.sales.hypothesis_countries import known_codes
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import LeadStatus, SalesHypothesisModel

#: Лидов в ответе предпросмотра — показать, что получится. Отчёт — целиком.
PREVIEW_LEADS = 50

router = APIRouter(prefix="/sales", tags=["продажи"])

_seller = Depends(needs(Permission.SALES))
_MAPPING = TypeAdapter(dict[LeadField, NonNegativeInt])


async def sheet_http() -> AsyncIterator[httpx.AsyncClient]:
    """Клиент для Google-таблицы. Зависимостью — тест подменяет транспорт."""
    async with sheet.client() as http:
        yield http


async def _found(
    file: UploadFile | None = File(None, description="CSV с базой"),
    link: str | None = Form(None, description="ссылка на Google-таблицу"),
    mapping: str | None = Form(None, description='JSON {"email": 0, …}: поле → колонка с нуля'),
    header: bool | None = Form(None, description="первая строка — заголовок; пусто — угадать"),
    delimiter: str | None = Form(None, description="разделитель, если угадан неверно"),
    default_language: str | None = Form(None, description="язык, где колонка языка пуста: en"),
    default_place: str | None = Form(
        None, description="страна или пояс, где обе колонки места пусты: de, Europe/Berlin"
    ),
    http: httpx.AsyncClient = Depends(sheet_http),
) -> intake.Preview:
    """Источник → предпросмотр. Разбор — в пуле потоков: 5000 строк не держат цикл."""
    given = intake.defaults(default_language, default_place)
    if file is not None and not link:
        data = await file.read(intake.MAX_BYTES + 1)
        name = file.filename or "база.csv"
        table = await asyncio.to_thread(intake.read_bytes, data, name, delimiter=delimiter)
    elif link and file is None:
        table = await intake.read_link(link, http, delimiter=delimiter)
    else:
        raise intake.IntakeError("дайте файл или ссылку на Google-таблицу — одно из двух")
    chosen = _mapping(mapping)
    return await asyncio.to_thread(intake.preview, table, chosen, header=header, given=given)


def _mapping(raw: str | None) -> Mapping | None:
    if not raw:
        return None
    try:
        return _MAPPING.validate_json(raw)
    except ValidationError as exc:
        raise intake.IntakeError(
            f"сопоставление не разобрано ({exc.error_count()} ош.): ждём JSON "
            '{"email": 0, "name": 1} — поле и номер колонки с нуля'
        ) from exc


@router.post("/import/preview", response_model=IntakeView, summary="Предпросмотр: без записи")
async def preview(_: UserModel = _seller, found: intake.Preview = Depends(_found)) -> IntakeView:
    return IntakeView.of(found, shown=PREVIEW_LEADS)


@router.post("/import", response_model=IntakeView, summary="Загрузить базу в гипотезу")
async def load(
    hypothesis_id: int = Form(..., description="гипотеза, в которую ложатся лиды"),
    author: UserModel = _seller,
    found: intake.Preview = Depends(_found),
    session: AsyncSession = Depends(db_session),
) -> IntakeView:
    loaded = await intake.load(session, found, hypothesis_id, author_id=author.id)
    await session.commit()
    return IntakeView.of(found, shown=PREVIEW_LEADS, loaded=loaded)


# --- списки раздела: гипотезы и лиды ------------------------------------------------


#: Границы периода добавления — словами отказа: экран шлёт их сам, руками их пишут в адрес API.
_ADDED_BOUNDS = {
    "added_since": "начало периода добавления",
    "added_until": "конец периода добавления",
}
#: Что прислать вместо негодной границы — с примером.
_MOMENT = "нужен момент с поясом, например 2026-10-01T00:00:00+03:00"


class LeadsQuery(BaseModel):
    """Фильтры под колонками экрана лидов — одной моделью, как у отбора.

    Состояние — тип базы: негодное значение отвергает схема. Причина — код
    строкой, как в базе (`RejectionReason`): незнакомый код из устаревшей
    ссылки ничего не находит, а не отказывает. Страница — номером, размер
    страницы знает сервер и называет его в ответе. Период добавления — моменты
    с поясом, как у воронки: негодная граница — отказ словами, что прислать, а не
    «значение не подходит» умолчания разбора."""

    state: LeadStatus | None = Field(
        default=None, description="new — загружен, ready — прошёл очистку, rejected — отсеян"
    )
    reason: str | None = Field(default=None, max_length=32, description="код причины отказа")
    hypothesis: int | None = Field(default=None, ge=1, description="номер гипотезы")
    search: str | None = Field(
        default=None,
        max_length=browse.SEARCH_LENGTH,
        description="по адресу, имени, компании, домену",
    )
    added_since: AwareDatetime | None = Field(
        default=None, description="добавлен не раньше — момент с поясом: полночь первого дня"
    )
    added_until: AwareDatetime | None = Field(
        default=None,
        description="добавлен раньше — момент с поясом, не включая: полночь после последнего дня",
    )
    page: int = Field(default=1, ge=1, le=1_000_000, description="страница, с единицы")
    limit: int = Field(
        default=browse.PAGE_SIZE, ge=1, le=browse.MAX_PAGE_SIZE, description="лидов на странице"
    )

    @field_validator("added_since", "added_until", mode="wrap")
    @classmethod
    def _moment(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> datetime | None:
        """Граница — момент с поясом; иначе — какая граница и что прислать."""
        try:
            moment: datetime | None = handler(value)
        except ValidationError as exc:
            kinds = {error["type"] for error in exc.errors()}
            said = "без пояса" if kinds == {"timezone_aware"} else "не дата"
            name = str(info.field_name)
            raise ValueError(f"{_ADDED_BOUNDS[name]} ({name}) — {said}: {_MOMENT}") from exc
        return moment

    @model_validator(mode="after")
    def _ordered(self) -> LeadsQuery:
        """Начало не раньше конца — период пуст: отказ словами воронки, а не пустая таблица."""
        since, until = self.added_since, self.added_until
        if since is not None and until is not None and since >= until:
            raise ValueError(EMPTY_PERIOD)
        return self

    def filters(self) -> browse.LeadFilters:
        return browse.LeadFilters(
            state=self.state,
            reason=self.reason,
            hypothesis_id=self.hypothesis,
            search=self.search,
            added=Period(self.added_since, self.added_until),
            page=self.page,
            size=self.limit,
        )


@router.get("/hypotheses", response_model=HypothesesView, summary="Гипотезы со счётчиками лидов")
async def list_hypotheses(
    _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> HypothesesView:
    rows = [HypothesisCard.of(row) for row in await browse.hypotheses(session)]
    return HypothesesView(rows=rows, total=len(rows), module_enabled=sales_cfg.ENABLED)


@router.get("/countries", response_model=CountriesView, summary="Коды стран для «не пишем»")
async def list_countries(_: UserModel = _seller) -> CountriesView:
    """Коды стран, которые примет поле гипотезы «Не пишем компаниям из» (`checked_codes`)."""
    return CountriesView(codes=sorted(known_codes()))


@router.post(
    "/hypotheses",
    response_model=HypothesisCard,
    status_code=status.HTTP_201_CREATED,
    summary="Завести гипотезу",
)
async def add_hypothesis(
    body: HypothesisBody, author: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> HypothesisCard:
    """Окно «Новая гипотеза»: правило и слова отказа — те же, что у команды консоли; заведение —
    в журнале действий с автором (`hypotheses.add`)."""
    try:
        made = await hypotheses.add(session, body.name, body.description, author_id=author.id)
    except hypotheses.BadNameError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except hypotheses.NameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    # Новая гипотеза — без лидов, но каждое состояние названо нулём, как в списке.
    return HypothesisCard.of(browse.HypothesisRow(made, {state.value: 0 for state in LeadStatus}))


@router.get(
    "/hypotheses/{hypothesis_id}/holds",
    response_model=HypothesisHolds,
    summary="Что держит гипотезу: удаляется только пустая",
)
async def read_holds(
    hypothesis_id: int, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> HypothesisHolds:
    """Окно гипотезы — до «Удалить»: открыта ли кнопка и что держит, словами сервера."""
    if await session.get(SalesHypothesisModel, hypothesis_id) is None:
        raise UnknownHypothesisError(f"гипотезы №{hypothesis_id} нет — обновите список гипотез")
    found = await hypothesis_holds.count(session, hypothesis_id)
    return HypothesisHolds(
        hypothesis_id=hypothesis_id, holds=found, held=hypotheses.held_line(found)
    )


@router.delete(
    "/hypotheses/{hypothesis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить пустую гипотезу",
)
async def remove_hypothesis(
    hypothesis_id: int, author: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> Response:
    """Только пустую — на случай, если завели по ошибке. Проверка и удаление — одной транзакцией
    под блокировкой строки (`hypotheses.remove`); держат — 409 словами с числами и подсказкой
    переименовать; строку держит другая операция — 409 словами; удалена — в журнале с автором."""
    try:
        await hypotheses.remove(session, hypothesis_id, author_id=author.id)
    except hypotheses.HypothesisRefusedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/leads", response_model=LeadsView, summary="Лиды с фильтрами под колонками")
async def list_leads(
    query: Annotated[LeadsQuery, Query()],
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> LeadsView:
    filters = query.filters()
    page = await browse.leads(session, filters)
    return LeadsView.of(page, page_number=filters.page, limit=filters.size)


router.include_router(kb_routes.router)
router.include_router(chain_routes.router)
router.include_router(queue_routes.router)
router.include_router(funnel_routes.router)
router.include_router(mailing_routes.router)
router.include_router(clean_routes.router)
router.include_router(calibration_routes.router)
router.include_router(lead_routes.router)
router.include_router(stoplist_routes.router)
router.include_router(segment_routes.router)
router.include_router(segment_again_routes.router)
router.include_router(segment_helper_routes.router)
router.include_router(segment_sites_routes.router)
router.include_router(weekly_routes.router)
router.include_router(handoff_routes.router)
