"""Что уходит и приходит по маршруту сборки пула ключей."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.config import serp as serp_cfg


class PoolRequestBody(BaseModel):
    """Чего хотим от пула.

    Угол здесь назвать нельзя, только пресет — решение Anthony: неудачная
    формулировка даёт пул, который выглядит нормально и ведёт не к тем
    сайтам, а вскрывается это после оплаченной выдачи.
    """

    preset: str = Field(min_length=1, max_length=40)
    country: str = Field(min_length=2, max_length=60)
    language: str = Field(default="English", min_length=2, max_length=40)
    #: Про что ключи. Пусто — законный исход: широкий пул иногда и нужен.
    #: Без темы пул выходит «обзоры в стране X», а не «обзоры про Y».
    topic: str = Field(default="", max_length=120)
    cap: int = Field(default=30, ge=1, le=serp_cfg.MAX_KEYWORDS_PER_RUN)


class PoolView(BaseModel):
    """Собранный пул и то, как он собрался.

    Отчёт отдаётся целиком, а не одним числом: пул, собранный наполовину
    из-за отказов модели, внешне неотличим от пула, который модель честно
    не смогла набрать.
    """

    keywords: list[str]
    asked: int
    received: int
    rejected: int
    near_duplicates: int
    refusals: list[str]
    tokens: int
    model: str
