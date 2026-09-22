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

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import needs
from backend.api.keywords.schemas import PoolRequestBody, PoolView
from backend.config import llm as llm_cfg
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.keywords.angles import PRESETS, UnknownPresetError
from backend.features.keywords.client import KeygenClient, LlmError
from backend.features.keywords.generator import PoolBuilder

router = APIRouter(prefix="/keywords", tags=["ключи"])

_runner = Depends(needs(Permission.RUN))


@router.get("/presets", response_model=list[str], summary="Обкатанные наборы углов")
async def presets(_: UserModel = _runner) -> list[str]:
    """Список пресетов отдаёт сервер, а не хранит фронт.

    Тот же довод, что у списка стран: второй список на фронте разъехался
    бы с этим при первом же добавленном наборе, и разошёлся бы молча.
    """
    return sorted(PRESETS)


@router.post("", response_model=PoolView, summary="Собрать пул ключей моделью")
async def build_pool(body: PoolRequestBody, _: UserModel = _runner) -> PoolView:
    """Собрать пул. Ничего платного не тратится — только модель."""
    client = KeygenClient()
    try:
        pool = await PoolBuilder(client, topic=body.topic).build(
            cap=body.cap,
            country=body.country,
            language=body.language,
            preset_name=body.preset,
        )
    except UnknownPresetError as exc:
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
    return PoolView(
        keywords=pool.keywords,
        asked=report.asked,
        received=report.received,
        rejected=len(report.rejected),
        near_duplicates=report.near_duplicates,
        refusals=list(report.refusals),
        tokens=report.tokens,
        model=llm_cfg.KEYGEN_MODEL,
    )
