"""Рассылка: отправка, добивки, лимиты на отправителя."""

from __future__ import annotations

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
    # Поддомен, принимающий ответы: адрес «куда отвечать» собирается
    # на нём с подписанной меткой (docs/OUTREACH_THREADS.md).
    reply_domain: str = Field(default="", validation_alias="OUTREACH_REPLY_DOMAIN")
    # Юридический блок письма. Пусто — боевая отправка не разрешается:
    # без них рассылка нарушает законы почти во всех целевых странах.
    # Чьим именем подписаны письма. Имя человека, а не ящика: ящики
    # ротируются по остатку дневного лимита, подпись — нет.
    sender_name: str = Field(default="", validation_alias="OUTREACH_SENDER_NAME")
    postal_address: str = Field(default="", validation_alias="OUTREACH_POSTAL_ADDRESS")
    unsubscribe_url: str = Field(default="", validation_alias="OUTREACH_UNSUBSCRIBE_URL")
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


_s = _Outreach()

SENDGRID_API_KEY: str = _s.sendgrid_api_key
EVENTS_PUBLIC_KEY: str = _s.events_public_key
ALLOWED_RECIPIENTS: tuple[str, ...] = tuple(
    item.strip().lower() for item in _s.allowed_recipients.split(",") if item.strip()
)
TRANSPORT: str = _s.transport
REPLY_DOMAIN: str = _s.reply_domain
SENDER_NAME: str = _s.sender_name
POSTAL_ADDRESS: str = _s.postal_address
UNSUBSCRIBE_URL: str = _s.unsubscribe_url
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
