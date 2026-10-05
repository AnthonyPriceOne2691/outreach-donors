"""Рассылка: отправка, добивки, лимиты на отправителя."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import Field

from backend.config._base import DomainSettings


class _Outreach(DomainSettings):
    sendgrid_api_key: str = Field(default="", validation_alias="OUTREACH_SENDGRID_API_KEY")
    # Предохранитель первых дней: пока список не пуст, боевой транспорт
    # пишет только на эти адреса (или домены). Пустой список означает
    # «кому угодно» — и каждое письмо тогда говорит об этом в лог.
    allowed_recipients: str = Field(default="", validation_alias="OUTREACH_ALLOWED_RECIPIENTS")
    # Открытый ключ, которым платформа подписывает события доставки
    # (base64 из её кабинета). Пусто — вебхук отказывает всем:
    # непроверенное событие паркует наши домены и пишет в стоп-лист.
    events_public_key: str = Field(default="", validation_alias="OUTREACH_EVENTS_PUBLIC_KEY")
    inbound_secret: str = Field(default="", validation_alias="OUTREACH_INBOUND_SECRET")
    # Транспорт отправки: `null` ничего не шлёт и работает только
    # на выдуманных доменах, `sendgrid` шлёт по-настоящему.
    transport: str = Field(default="null", validation_alias="OUTREACH_TRANSPORT")
    # Приставка поддомена ответов у каждого домена отправки: письмо
    # с anna@mail-a.example просит отвечать на anna+метка@replies.mail-a.example
    # (docs/OUTREACH_THREADS.md). Приставка, а не домен: один общий домен
    # ответов связывал бы все домены рассылки между собой.
    reply_subdomain: str = Field(default="replies", validation_alias="OUTREACH_REPLY_SUBDOMAIN")
    # Юридический блок письма. Пусто — боевая отправка не разрешается:
    # без них рассылка нарушает законы почти во всех целевых странах.
    # Чьим именем подписаны письма. Имя человека, а не ящика: ящики
    # ротируются по остатку дневного лимита, подпись — нет.
    sender_name: str = Field(default="", validation_alias="OUTREACH_SENDER_NAME")
    postal_address: str = Field(default="", validation_alias="OUTREACH_POSTAL_ADDRESS")
    unsubscribe_url: str = Field(default="", validation_alias="OUTREACH_UNSUBSCRIBE_URL")
    # Передача лида дальше (`replies/lead_handoff.py`): адрес приёма у CRM и общий
    # секрет подписи. Пусто — вебхук не уходит, лид остаётся в диалогах и в CSV.
    lead_webhook_url: str = Field(default="", validation_alias="OUTREACH_LEAD_WEBHOOK_URL")
    lead_webhook_secret: str = Field(default="", validation_alias="OUTREACH_LEAD_WEBHOOK_SECRET")
    # Писем на отправителя в день. Держим низким: схема на 20 доменах
    # работает именно за счёт малого объёма на ящик — так в требованиях.
    daily_cap_per_sender: int = Field(default=20, validation_alias="OUTREACH_DAILY_CAP_PER_SENDER")
    # Разгон нового отправителя: ступени дневного капа по дням.
    warmup_daily_caps: str = Field(default="5,10,15,20", validation_alias="OUTREACH_WARMUP_CAPS")
    warmup_step_days: int = Field(default=2, validation_alias="OUTREACH_WARMUP_STEP_DAYS")
    # Сколько добивок в час уходит с одного ящика. Свой потолок, потому
    # что дневной кап добивки не считает (решение 21.09.2026): иначе
    # backlog первых писем голодит цепочки, а цепочки съедают квоту
    # новых доноров. Час, а не сутки, — чтобы сотня подошедших добивок
    # не ушла пачкой за минуту: почтовая платформа смотрит на скорость.
    followup_per_sender_per_hour: int = Field(
        default=10, validation_alias="OUTREACH_FOLLOWUP_PER_SENDER_PER_HOUR"
    )
    # Добивки: дни от первого письма. Стоп при любом ответе или отписке.
    followup_days: str = Field(default="7,14", validation_alias="OUTREACH_FOLLOWUP_DAYS")
    # Доля отказов, после которой отправитель уходит на паузу, и минимум
    # отправок, до которого доля не считается (иначе 2 отказа из 3 роняют домен).
    bounce_pause_threshold: float = Field(
        default=0.05, validation_alias="OUTREACH_BOUNCE_PAUSE_THRESHOLD"
    )
    bounce_pause_min_sent: int = Field(
        default=50, validation_alias="OUTREACH_BOUNCE_PAUSE_MIN_SENT"
    )
    # Уникализация письма: целевая доля изменённых слов.
    uniqueness_target_min: float = Field(default=0.15, validation_alias="OUTREACH_UNIQ_MIN")
    uniqueness_target_max: float = Field(default=0.25, validation_alias="OUTREACH_UNIQ_MAX")
    # Уверенность разбора ответа ниже порога — в ручную очередь, не в базу.
    price_confidence_threshold: float = Field(
        default=0.80, validation_alias="OUTREACH_PRICE_CONFIDENCE"
    )
    # Сколько донор, промолчавший на всю цепочку, не возвращается в отбор.
    # Требование про повторы говорит только сроками годности данных
    # (90 дней метрики, 150 цена), а молчание в ответ не покрывает вовсе:
    # по одной свежести домен вернулся бы на 91-й день, и мы заплатили бы
    # за метрики, чтобы написать четвёртое письмо тому, кто трижды промолчал.
    # Год взят у стоп-листа поставщиков — там требование называет 12 месяцев.
    silence_days: int = Field(default=365, validation_alias="OUTREACH_SILENCE_DAYS")
    # Сколько не спрашивать донора, ответившего «не продаём размещения».
    # Год, как у молчания: ответ «нет» меняется со сменой владельца или
    # политики, а переспрашивать раньше — жалоба на спам.
    decline_days: int = Field(default=365, validation_alias="OUTREACH_DECLINE_DAYS")


_s = _Outreach()

SENDGRID_API_KEY: str = _s.sendgrid_api_key.strip()  # хвостовой пробел из .env
EVENTS_PUBLIC_KEY: str = _s.events_public_key.strip()
ALLOWED_RECIPIENTS: tuple[str, ...] = tuple(
    item.strip().lower() for item in _s.allowed_recipients.split(",") if item.strip()
)
TRANSPORT: str = _s.transport
REPLY_SUBDOMAIN: str = _s.reply_subdomain
SENDER_NAME: str = _s.sender_name
POSTAL_ADDRESS: str = _s.postal_address
UNSUBSCRIBE_URL: str = _s.unsubscribe_url
LEAD_WEBHOOK_URL: str = _s.lead_webhook_url.strip()
LEAD_WEBHOOK_SECRET: str = _s.lead_webhook_secret
INBOUND_SECRET: str = _s.inbound_secret
DAILY_CAP_PER_SENDER: int = _s.daily_cap_per_sender
WARMUP_DAILY_CAPS: tuple[int, ...] = tuple(int(x) for x in _s.warmup_daily_caps.split(","))
WARMUP_STEP_DAYS: int = _s.warmup_step_days
FOLLOWUP_DAYS: tuple[int, ...] = tuple(int(x) for x in _s.followup_days.split(","))
FOLLOWUP_PER_SENDER_PER_HOUR: int = _s.followup_per_sender_per_hour
BOUNCE_PAUSE_THRESHOLD: float = _s.bounce_pause_threshold
BOUNCE_PAUSE_MIN_SENT: int = _s.bounce_pause_min_sent
UNIQUENESS_TARGET_MIN: float = _s.uniqueness_target_min
UNIQUENESS_TARGET_MAX: float = _s.uniqueness_target_max
PRICE_CONFIDENCE_THRESHOLD: float = _s.price_confidence_threshold
SILENCE_DAYS: int = _s.silence_days
DECLINE_DAYS: int = _s.decline_days


class _OwnAccount(DomainSettings):
    """Своя учётка почтовой платформы у направления — переменные с приставкой
    `OUTREACH_<ЭТАП>_`: `OUTREACH_SALES_SENDGRID_API_KEY`,
    `OUTREACH_SALES_EVENTS_PUBLIC_KEY`, `OUTREACH_SALES_ALLOWED_RECIPIENTS`.
    Не задано — `None`, и берётся общее."""

    sendgrid_api_key: str | None = None
    events_public_key: str | None = None
    allowed_recipients: str | None = None


@dataclass(frozen=True, slots=True)
class MailAccount:
    """Учётка почтовой платформы, через которую пишет направление (этап).

    У направления бывает своя: у «Продаж» — свой субаккаунт и свои домены,
    и письма продаж через наш ключ платформа не примет (домены отправителя
    подтверждены в их учётке). Чего своего нет — берётся общее: ключ, ключ
    подписи событий, предохранитель. Предохранитель так безопаснее всего:
    направление без своего списка пишет только туда же, куда и общее.
    """

    api_key: str
    events_public_key: str
    allowed_recipients: tuple[str, ...]
    #: Откуда взяты ключ и предохранитель — имена переменных для отказов:
    #: «не задан OUTREACH_SALES_SENDGRID_API_KEY» отправляет в нужную строку.
    key_setting: str = "OUTREACH_SENDGRID_API_KEY"
    allowlist_setting: str = "OUTREACH_ALLOWED_RECIPIENTS"
    #: Учётка собрана не до конца — транспорт с ней не собирается, и отказ
    #: говорит, какую строку дописать.
    refusal: str | None = None


def _recipients(raw: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def mail_account(stage: str | None = None) -> MailAccount:
    """Учётка направления. Без этапа или без своих настроек — общая.

    Читается при каждом вызове, а не один раз при запуске: общие значения
    берутся из констант модуля, и подмена их в тестах (и правка `.env` с
    перезапуском) видна сразу. Пробелы по краям ключей срезаются: хвостовой
    перевод строки из `.env` ломает заголовок запроса.
    """
    shared = MailAccount(
        api_key=SENDGRID_API_KEY,
        events_public_key=EVENTS_PUBLIC_KEY,
        allowed_recipients=ALLOWED_RECIPIENTS,
    )
    if not stage:
        return shared
    prefix = f"OUTREACH_{stage.upper()}_"
    own = _OwnAccount(_env_prefix=prefix)
    if own.sendgrid_api_key is None:
        return _on_shared_key(own, shared, prefix)
    return _own_account(own, shared, prefix)


def _on_shared_key(own: _OwnAccount, shared: MailAccount, prefix: str) -> MailAccount:
    """Своего ключа нет — письма этапа уходят общей учёткой и под общим списком."""
    return MailAccount(
        api_key=shared.api_key,
        events_public_key=_own_or(own.events_public_key, shared.events_public_key),
        allowed_recipients=(
            shared.allowed_recipients
            if own.allowed_recipients is None
            else _recipients(own.allowed_recipients)
        ),
        # Ключа нет ни своего, ни общего — назвать обе строки: этапу можно
        # дать свой ключ, а можно жить на общем.
        key_setting=f"{prefix}SENDGRID_API_KEY (или общий {shared.key_setting})",
        allowlist_setting=(
            shared.allowlist_setting
            if own.allowed_recipients is None
            else f"{prefix}ALLOWED_RECIPIENTS"
        ),
    )


def _own_account(own: _OwnAccount, shared: MailAccount, prefix: str) -> MailAccount:
    """Своя учётка — и предохранитель только свой (ревью «Продаж» 05.10.2026).

    Общий список снимают по своим причинам — когда доноры начнут писать по-
    настоящему, — и направление со своим ключом молча осталось бы без
    защиты. Не задан свой список — отказ собрать транспорт, а не общий.
    """
    allowlist = f"{prefix}ALLOWED_RECIPIENTS"
    return MailAccount(
        api_key=(own.sendgrid_api_key or "").strip(),
        events_public_key=_own_or(own.events_public_key, shared.events_public_key),
        allowed_recipients=_recipients(own.allowed_recipients or ""),
        key_setting=f"{prefix}SENDGRID_API_KEY",
        allowlist_setting=allowlist,
        refusal=(
            None
            if own.allowed_recipients is not None
            else (
                f"{allowlist} не задан: у направления своя учётка платформы, и "
                "предохранитель у неё свой — впишите свои адреса или оставьте "
                "строку пустой (без предохранителя)"
            )
        ),
    )


def _own_or(own: str | None, shared: str) -> str:
    return shared if own is None else own.strip()


def events_public_keys(stages: Iterable[str]) -> tuple[str, ...]:
    """Ключи подписи событий: общий и свои у направлений — без пустых и повторов.

    События каждой учётки подписаны её ключом, а адрес вебхука один: событие
    принимается, если сошлось с любым из них.
    """
    keys = [mail_account().events_public_key, *(mail_account(s).events_public_key for s in stages)]
    return tuple(dict.fromkeys(key for key in keys if key))
