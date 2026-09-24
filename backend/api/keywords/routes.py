"""Сборка пула ключей моделью — второй способ задать ключи прогона.

**Ключи по требованиям приносит оператор**, и текстовое поле на экране
прогона остаётся главным. Генерация — второй режим того же поля, а не
замена: собранные фразы падают в ту же область, человек их видит и правит
до сметы. Смета и кап остаются последним рубежом перед тратой.

**Этот маршрут не тратит ничего платного.** Модель стоит доли цента,
выдача не трогается вовсе: пул собран — ещё не значит, что прогон
запущен.

Право `run`, а не `view`: пул ведёт к трате, пусть и не сразу.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.keywords.schemas import PoolRequestBody, PoolView
from backend.api.review.schemas import KeywordYieldView
from backend.config import llm as llm_cfg
from backend.features.core import usage
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.keywords.angles import PRESETS, UnknownPresetError
from backend.features.keywords.client import KeygenClient, LlmError
from backend.features.keywords.generator import PoolBuilder
from backend.features.review.keyword_yield import country_yield
from backend.features.serp import markets

router = APIRouter(prefix="/keywords", tags=["ключи"])

_runner = Depends(needs(Permission.RUN))


@router.get(
    "/yield", response_model=list[KeywordYieldView], summary="Ключи страны, дававшие доноров"
)
async def keyword_yield(
    country: str = Query(min_length=2, max_length=8, description="страна прогонов"),
    _: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> list[KeywordYieldView]:
    """Ключи прошлых прогонов страны, по которым нашлись принятые доноры.

    Для формы запуска: следующий прогон собирается из ключей, которые
    уже давали доноров, а не из всех подряд. Право то же, что у сборки
    ключей, — это часть запуска прогона.
    """
    return [KeywordYieldView.of(row) for row in await country_yield(session, country)]


@router.get("/presets", response_model=list[str], summary="Обкатанные наборы углов")
async def presets(_: UserModel = _runner) -> list[str]:
    """Список пресетов отдаёт сервер, а не хранит фронт.

    Тот же довод, что у списка стран: второй список на фронте разъехался
    бы с этим при первом же добавленном наборе, и разошёлся бы молча.
    """
    return sorted(PRESETS)


def _languages_or_refuse(country: str) -> tuple[str, ...]:
    """Языки рынка. Незнакомая страна — отказ, а не тихий английский:
    он увёл бы прогон в другой веб, и отчёт показал бы успех."""
    try:
        return markets.keygen_languages(country)
    except markets.UnknownMarketError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/languages", response_model=list[str], summary="Языки рынка")
async def languages(country: str, _: UserModel = _runner) -> list[str]:
    """На каких языках соберутся ключи для этой страны.

    Оператор выбирает только страну — языки выводятся. Эндпоинт нужен,
    чтобы он видел их до сборки: пул на двух языках стоит вдвое дороже,
    и узнавать об этом по счёту неправильно.
    """
    return list(_languages_or_refuse(country))


@router.post("", response_model=PoolView, summary="Собрать пул ключей моделью")
async def build_pool(
    body: PoolRequestBody,
    session: AsyncSession = Depends(db_session),
    _: UserModel = _runner,
) -> PoolView:
    """Собрать пул. Юниты и выдача не трогаются — только модель."""
    languages = _languages_or_refuse(body.country)
    client = KeygenClient()
    try:
        pool = await PoolBuilder(client, topics=body.topics).build(
            cap=body.cap,
            country=body.country,
            languages=languages,
            preset_name=body.preset,
        )
    except (UnknownPresetError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except LlmError as exc:
        # Пустой пул из-за отказов модели — ошибка, а не результат: отдать
        # пустой список значит предложить человеку запустить прогон ни за чем.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    finally:
        await client.aclose()

    # Поля берём у отчёта напрямую, а не через `as_dict()`: тот отдаёт
    # словарь для журнала, и типы там уже не видны.
    report = pool.report
    if report.tokens:
        # Расход пишется даже когда пул пустой по другой причине: строка
        # журнала отвечает на вопрос «во что обошлось», а не «что вышло».
        usage.record(session, operation="keywords", units=report.tokens)
        await session.commit()
    return PoolView(
        keywords=pool.keywords,
        languages=list(languages),
        asked=report.asked,
        received=report.received,
        rejected=len(report.rejected),
        near_duplicates=report.near_duplicates,
        refusals=list(report.refusals),
        tokens=report.tokens,
        model=llm_cfg.KEYGEN_MODEL,
    )
