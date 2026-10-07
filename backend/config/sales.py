"""Модуль «Продажи»: выключатель, проверяльщик адресов и CRM для лидов.

**По умолчанию выключен.** Продажи пишут живым людям от имени компании,
и включение — решение человека для конкретного развёртывания, а не умолчание
кода. Сейчас выключать нечего: в модуле только данные (гипотезы и лиды)
и команды консоли. Выключатель читают срезы, которые приносят работу
продаж; первой — проверка ключей продаж на старте.

**Проверяльщик адресов по умолчанию выдуманный (`fixture`).** Живой стоит
денег с ключа, общего с соседней системой, и на него переключает человек:
`SALES_VERIFIER_PROVIDER=live`. Ключ — тот же `CONTACTS_HUNTER_API_KEY`,
что у поиска адресов: сервис один, второй переменной для него не заводим.

**Kommo по умолчанию тоже выдуманный (`fixture`).** Живой пишет сделки
в чужую CRM, и отозвать записанное нельзя: на `live` переключает человек,
а без поддомена, ключа, воронки, этапа или ответственного клиент не
собирается (`features/sales/kommo.build_kommo`).

**Окно отправки — рабочие часы получателя по его часам** (Ф4, 4.3): дни «1-5» или «1,3,5»
(1 — понедельник), часы «09:00-17:00»; разбор при старте — опечатка не ждёт первого письма.
"""

from __future__ import annotations

from datetime import time

from pydantic import Field

from backend.config._base import DomainSettings


class _Sales(DomainSettings):
    enabled: bool = Field(default=False, validation_alias="SALES_ENABLED")
    # `fixture` — вердикты по правилам из адреса, без сети и денег;
    # `live` — Hunter Email Verifier тем же ключом, что ступень 3 поиска.
    verifier_provider: str = Field(default="fixture", validation_alias="SALES_VERIFIER_PROVIDER")
    # `fixture` — сделки с выдуманными номерами, без сети; `live` — закрытая
    # интеграция аккаунта Kommo долгоживущим ключом (секрет: в `.env`).
    kommo_provider: str = Field(default="fixture", validation_alias="SALES_KOMMO_PROVIDER")
    kommo_subdomain: str = Field(default="", validation_alias="SALES_KOMMO_SUBDOMAIN")
    kommo_token: str = Field(default="", validation_alias="SALES_KOMMO_TOKEN")
    # Номера из Kommo — строкой: пустое значение образца не роняет чтение
    # настроек, а негодное называет фабрика клиента словами до первого запроса.
    kommo_pipeline_id: str = Field(default="", validation_alias="SALES_KOMMO_PIPELINE_ID")
    kommo_status_id: str = Field(default="", validation_alias="SALES_KOMMO_STATUS_ID")
    kommo_responsible_user_id: str = Field(
        default="", validation_alias="SALES_KOMMO_RESPONSIBLE_USER_ID"
    )
    send_days: str = Field(default="1-5", validation_alias="SALES_SEND_DAYS")
    send_hours: str = Field(default="09:00-17:00", validation_alias="SALES_SEND_HOURS")
    # На сколько минут от открытия окна расходятся письма, ждавшие его.
    send_spread_min: int = Field(default=30, validation_alias="SALES_SEND_SPREAD_MIN")
    # Доля жалоб в окне последних писем ящика, с которой он встаёт на паузу (Ф4, 4.5b), —
    # задолго до 0,3% почтовых сервисов: на 50 письмах одна жалоба. Решение владельца.
    complaint_pause: float = Field(default=0.001, validation_alias="SALES_COMPLAINT_PAUSE")

    # Уверенность вида ответа, ниже которой ответ ждёт человека (ручная очередь
    # продаж). Предложение до замера на наборе владельца — как у разбора цены.
    reply_confidence: float = Field(
        default=0.80, ge=0.0, le=1.0, validation_alias="SALES_REPLY_CONFIDENCE"
    )
    # Каталог внешнего набора ответов для eval (`scripts/eval_sales_reply.py
    # --golden`): обезличенная переписка лежит вне репозитория — он публичный.
    golden_dir: str = Field(default="", validation_alias="SALES_GOLDEN_DIR")
    # Автоответ в треде продаж цепочку не останавливает, а переносит следующий шаг:
    # до даты возвращения из текста или на столько дней от автоответа.
    ooo_delay_days: int = Field(default=7, ge=1, le=60, validation_alias="SALES_OOO_DELAY_DAYS")


def _days(text: str) -> frozenset[int]:
    """«1-5» или «1,3,5» → дни недели с нуля (0 — понедельник), как `date.weekday()`."""
    refusal = f"SALES_SEND_DAYS «{text}»: дни — числа 1…7, например «1-5»"
    found: set[int] = set()
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        try:
            found.update(range(int(first) - 1, int(last or first)))
        except ValueError as exc:
            raise ValueError(refusal) from exc
    if not found or not found <= set(range(7)):
        raise ValueError(refusal)
    return frozenset(found)


def _hours(text: str) -> tuple[time, time]:
    """«09:00-17:00» → открытие и закрытие окна; закрытие позже открытия."""
    start, _, end = text.partition("-")
    try:
        opens, closes = time.fromisoformat(start.strip()), time.fromisoformat(end.strip())
    except ValueError as exc:
        raise ValueError(f"SALES_SEND_HOURS «{text}»: часы — «09:00-17:00»") from exc
    if opens >= closes:
        raise ValueError(f"SALES_SEND_HOURS «{text}»: окно закрывается раньше, чем открывается")
    return opens, closes


_s = _Sales()

ENABLED: bool = _s.enabled
VERIFIER_PROVIDER: str = _s.verifier_provider

KOMMO_PROVIDER: str = _s.kommo_provider
KOMMO_SUBDOMAIN: str = _s.kommo_subdomain.strip().lower()
KOMMO_TOKEN: str = _s.kommo_token.strip()
KOMMO_PIPELINE_ID: str = _s.kommo_pipeline_id.strip()
KOMMO_STATUS_ID: str = _s.kommo_status_id.strip()
KOMMO_RESPONSIBLE_USER_ID: str = _s.kommo_responsible_user_id.strip()

SEND_DAYS: frozenset[int] = _days(_s.send_days)
SEND_OPENS, SEND_CLOSES = _hours(_s.send_hours)
SEND_SPREAD_MIN: int = _s.send_spread_min
COMPLAINT_PAUSE: float = _s.complaint_pause
#: Порог уверенности вида ответа лида (`features/sales/replies.py`).
REPLY_CONFIDENCE: float = _s.reply_confidence
#: Где лежит внешний набор ответов для eval. Пусто — не задан.
GOLDEN_DIR: str = _s.golden_dir.strip()
#: На сколько дней автоответ переносит следующий шаг продаж, если даты в нём нет.
OOO_DELAY_DAYS: int = _s.ooo_delay_days

#: Не больше стольких запросов в секунду. Предел Kommo из его документации —
#: семь в секунду с одного IP для любой интеграции; чаще — 429, а частые 429
#: закрывают доступ целиком (403).
KOMMO_RATE_PER_SEC = 7
#: Сколько ждать ответа Kommo на один запрос.
KOMMO_TIMEOUT_SEC = 20.0
