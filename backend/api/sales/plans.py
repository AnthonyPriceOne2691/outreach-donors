"""План работ: превью книги, сохранение плана и планы для вкладки «Гипотезы».

`POST /sales/plans/preview` — книга (.xlsx) и выбор окна загрузки: режим, общая цель, потолки плана и
гипотезы. Ответ — листы черновика (поля карточки, ключи счётом и примерами, ошибки и предупреждения:
нет цепочки на языке писем, ключей мало для цели), смета диапазоном по гипотезе и на план, почему план
не сохранить (`refusals`: ошибки листов, нет цели, нет потолков, занятое имя) и отпечаток. Ничего не
пишет.

`POST /sales/plans` — та же книга и тот же выбор с отпечатком превью. Книга разбирается заново, смета
считается заново: черновик на сервере не хранится. Отпечаток не совпал — 409 «проверьте заново»;
правила сохранения нарушены — 400 словами, все сразу (`plan_store.save_plan`); иначе план заводится
одной транзакцией и ответ 201 — план с гипотезами. Право — `sales` и `run`: план — трата выдачи и
модели, как запуск прогона сбора (`segment.start_run`).

`GET /sales/plans` и `GET /sales/plans/{N}` — планы, новые сверху, с гипотезами по порядку листов.

Кнопки вкладки «Гипотезы» (`plan_actions.py`, `plan_keys.py`) — право `sales` и `run`: каждая пускает
или держит траты выдачи и модели, как запуск и продолжение прогона сбора. План — «Остановить» и
«Продолжить» (`/plans/{N}/stop`, `/resume`), ответ — план; гипотеза плана — «Остановить» и
«Продолжить» (`/plan-hypotheses/{N}/stop`, `/resume`), «Изменить качество сайтов» (`PATCH …/quality`),
«Собрать ещё N» (`…/more`) и «Добавить ключи» (`…/keywords`: текст или книга одним листом), ответ —
гипотеза плана, как в списке. Отказ по состоянию (уже остановлена, план окончен, строку держит другая
кнопка) — 409 словами, негодная просьба — 400 словами, нет плана или гипотезы плана — 404.

Файл — как у загрузки доноров списком (`api/donors/bulk.py`): читается не больше предела и байта
сверху, разбор — в пуле потоков (книга в 300 тысяч ключей — секунды работы процессора, цикл событий
их не ждёт), отказ книги — 400 словами разбора (`PlanFileError` — отказ загрузки продаж).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.plan_schemas import (
    PlanCard,
    PlanHypothesisView,
    PlanKeysAdded,
    PlanMoreBody,
    PlanPreview,
    PlanQualityBody,
    PlansView,
    SheetView,
)
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import hypotheses, plan_actions, plan_keys, plan_store
from backend.features.sales.plan_actions import NO_PLAN, NO_ROW, Quality
from backend.features.sales.plan_card import MAX_TARGET
from backend.features.sales.plan_estimate import PlanEstimate, plan_estimate
from backend.features.sales.plan_file import MAX_BYTES, PlanDraft, PlanKeys, read_plan
from backend.features.sales.plan_models import PlanMode, SalesPlanHypothesisModel
from backend.features.sales.plan_store import PlanAsk

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
_runner = Depends(needs(Permission.RUN))

STALE = (
    "книга или выбор окна изменились после проверки — проверьте план заново: сохраняется только "
    "то, что вы видели"
)
#: «Добавить ключи» — текстом или книгой, одно из двух.
ONE_SOURCE = "дайте ключи текстом или книгой .xlsx — одно из двух"
#: Имя плана без своего и без имени файла.
DEFAULT_NAME = "план работ"

#: Поля окна загрузки — одной формой у превью и сохранения.
Mode = Annotated[PlanMode, Form(description="режим: economy — экономный, fast — быстрый")]
Goal = Annotated[
    int | None,
    Form(ge=1, le=MAX_TARGET, description="общая цель «Найти сайтов» для листов без своей"),
]
Cap = Annotated[Decimal, Form(ge=0, max_digits=10, decimal_places=2, description="потолок, $")]
Name = Annotated[str | None, Form(max_length=255, description="имя плана; пусто — имя файла")]


@dataclass(frozen=True, slots=True)
class Upload:
    """Книга и выбор окна загрузки."""

    file: UploadFile
    ask: PlanAsk

    async def draft(self) -> PlanDraft:
        """Черновик книги; не прочитать целиком — 400 словами разбора."""
        data = await self.file.read(MAX_BYTES + 1)
        return await asyncio.to_thread(read_plan, data, self.file.filename or "")

    @property
    def name(self) -> str:
        return self.file.filename or ""


def _upload(
    file: UploadFile = File(..., description="книга .xlsx: лист — гипотеза"),
    mode: Mode = PlanMode.ECONOMY,
    goal: Goal = None,
    cap_usd: Cap = Decimal(0),
    hypothesis_cap_usd: Cap = Decimal(0),
    name: Name = None,
) -> Upload:
    """Форма окна загрузки; имя плана по умолчанию — имя файла без расширения."""
    plain = " ".join((name or "").split()) or PurePath(file.filename or "").stem or DEFAULT_NAME
    ask = PlanAsk(plain, mode, goal, cap_usd, hypothesis_cap_usd)
    return Upload(file, ask)


_form = Depends(_upload)


async def _estimate(session: AsyncSession, draft: PlanDraft, ask: PlanAsk) -> PlanEstimate:
    """Смета плана — выдачей и тарифом судьи его режима (`plan_store.PLAN_MODES`)."""
    pace = plan_store.PLAN_MODES[ask.mode]
    return await plan_estimate(
        session, draft, pace.serp_mode, goal=ask.goal, judge_tier=pace.judge_tier
    )


@router.post("/plans/preview", response_model=PlanPreview, summary="План работ: превью книги")
async def preview(
    upload: Upload = _form,
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> PlanPreview:
    """Листы, смета, почему не сохранить и отпечаток — без записи."""
    draft = await upload.draft()
    estimate = await _estimate(session, draft, upload.ask)
    spends = {row.sheet: row for row in estimate.hypotheses}
    chains = await plan_store.chain_gaps(session, draft)
    sheets = [
        SheetView.of(found, spends.get(found.sheet), chains.get(found.sheet))
        for found in draft.hypotheses
    ]
    return PlanPreview.of(
        upload.name,
        sheets,
        draft.skipped,
        estimate,
        await plan_store.problems(session, draft, upload.ask),
        plan_store.plan_digest(draft, upload.ask),
    )


@router.post(
    "/plans",
    response_model=PlanCard,
    status_code=status.HTTP_201_CREATED,
    summary="Сохранить план работ",
)
async def save(
    upload: Upload = _form,
    digest: str = Form(..., max_length=64, description="отпечаток превью"),
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanCard:
    """Разбор и смета — заново; отпечаток не тот — 409, правила — 400 словами; иначе план и 201."""
    draft = await upload.draft()
    if plan_store.plan_digest(draft, upload.ask) != digest:
        raise HTTPException(status.HTTP_409_CONFLICT, STALE)
    estimate = await _estimate(session, draft, upload.ask)
    made = await plan_store.save_plan(
        session, draft, upload.ask, estimate, author=author.email, author_id=author.id
    )
    await session.commit()
    return await _card(session, made.id)


async def _card(session: AsyncSession, plan_id: int) -> PlanCard:
    view = await plan_store.plan(session, plan_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_PLAN.format(plan_id=plan_id))
    return PlanCard.of(view)


@router.get("/plans", response_model=PlansView, summary="Планы работ")
async def list_plans(
    _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> PlansView:
    return PlansView(plans=[PlanCard.of(view) for view in await plan_store.plans(session)])


@router.get("/plans/{plan_id}", response_model=PlanCard, summary="План работ")
async def one_plan(
    plan_id: int, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> PlanCard:
    return await _card(session, plan_id)


@contextmanager
def _refusals() -> Iterator[None]:
    """Отказ кнопки — словами: состояние — 409, просьба — 400, нет строки — 404."""
    try:
        yield
    except plan_actions.UnknownPlanError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (plan_actions.PlanStateError, hypotheses.HypothesisRefusedError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (plan_actions.PlanActionError, hypotheses.HypothesisError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


async def _row_card(session: AsyncSession, row_id: int) -> PlanHypothesisView:
    """Гипотеза плана — как в списке планов."""
    row = await session.get(SalesPlanHypothesisModel, row_id)
    view = None if row is None else await plan_store.plan(session, row.plan_id)
    found = (
        next((item for item in view.hypotheses if item.row.id == row_id), None) if view else None
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_ROW.format(row_id=row_id))
    return PlanHypothesisView.of(found)


@router.post("/plans/{plan_id}/stop", response_model=PlanCard, summary="Остановить план работ")
async def stop_plan(
    plan_id: int,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanCard:
    with _refusals():
        await plan_actions.stop_plan(session, plan_id, author_id=author.id)
    await session.commit()
    return await _card(session, plan_id)


@router.post("/plans/{plan_id}/resume", response_model=PlanCard, summary="Продолжить план работ")
async def resume_plan(
    plan_id: int,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanCard:
    with _refusals():
        await plan_actions.resume_plan(session, plan_id, author_id=author.id)
    await session.commit()
    return await _card(session, plan_id)


@router.post(
    "/plan-hypotheses/{row_id}/stop",
    response_model=PlanHypothesisView,
    summary="Остановить гипотезу плана",
)
async def stop_hypothesis(
    row_id: int,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanHypothesisView:
    with _refusals():
        await plan_actions.stop_hypothesis(
            session, row_id, author_id=author.id, now=datetime.now(UTC)
        )
    await session.commit()
    return await _row_card(session, row_id)


@router.post(
    "/plan-hypotheses/{row_id}/resume",
    response_model=PlanHypothesisView,
    summary="Продолжить гипотезу плана",
)
async def resume_hypothesis(
    row_id: int,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanHypothesisView:
    with _refusals():
        await plan_actions.resume_hypothesis(
            session, row_id, author_id=author.id, now=datetime.now(UTC)
        )
    await session.commit()
    return await _row_card(session, row_id)


@router.patch(
    "/plan-hypotheses/{row_id}/quality",
    response_model=PlanHypothesisView,
    summary="Изменить качество сайтов гипотезы плана",
)
async def change_quality(
    row_id: int,
    body: PlanQualityBody,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanHypothesisView:
    """Глубина и цель — гипотезы плана, страны компаний — правкой гипотезы с её журналом."""
    ask = Quality(body.max_pages, body.target, body.other_countries, body.excluded_countries)
    with _refusals():
        await plan_actions.change_quality(
            session, row_id, ask, author_id=author.id, now=datetime.now(UTC)
        )
    await session.commit()
    return await _row_card(session, row_id)


@router.post(
    "/plan-hypotheses/{row_id}/more",
    response_model=PlanHypothesisView,
    summary="Собрать ещё N сайтов гипотезе плана",
)
async def collect_more(
    row_id: int,
    body: PlanMoreBody,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanHypothesisView:
    with _refusals():
        await plan_actions.collect_more(
            session, row_id, body.n, author_id=author.id, now=datetime.now(UTC)
        )
    await session.commit()
    return await _row_card(session, row_id)


def _one_source(file: UploadFile | None, text: str | None) -> str:
    """Ключи текстом или книгой — одно из двух; иначе 400 словами. Текст — без пробелов по краям."""
    written = (text or "").strip()
    if (file is None) == (not written):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, ONE_SOURCE)
    return written


async def _book_keys(file: UploadFile) -> PlanKeys:
    """Ключи книги одним листом — разбор в пуле потоков, как у книги плана."""
    data = await file.read(MAX_BYTES + 1)
    return await asyncio.to_thread(plan_keys.keys_of_book, data, file.filename or "")


@router.post(
    "/plan-hypotheses/{row_id}/keywords",
    response_model=PlanKeysAdded,
    summary="Добавить ключи гипотезе плана",
)
async def add_keywords(
    row_id: int,
    file: UploadFile | None = File(None, description="книга .xlsx одним листом: ключ в колонке A"),
    text: str | None = Form(None, description="ключи текстом — по одному в строке"),
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> PlanKeysAdded:
    """Ключи — текстом или книгой, одно из двух; книга разбирается в пуле потоков, как книга
    плана."""
    written = _one_source(file, text)
    with _refusals():
        keys = plan_keys.keys_of_text(written) if file is None else await _book_keys(file)
        added = await plan_keys.add_keys(
            session, row_id, keys, author_id=author.id, now=datetime.now(UTC)
        )
    await session.commit()
    return PlanKeysAdded.of(await _row_card(session, row_id), added)
