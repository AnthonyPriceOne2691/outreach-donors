"""Проверка конфига на старте.

Смысл один: сервис, которому не хватает ключа, должен падать сразу и называть,
чего не хватает, — а не стартовать и уронить первый же прогон на середине,
потратив часть юнитов.

Проверки разделены по назначению: прогон доноров требует одного набора
переменных, рассылка — другого. Собирать базу можно, не настроив почту.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.config import access, ahrefs, outreach, serp, storage

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Не хватает обязательных настроек."""


@dataclass(frozen=True, slots=True)
class Requirement:
    """Одна обязательная настройка: имя переменной и текущее значение."""

    env_name: str
    value: object
    purpose: str


def _missing(requirements: list[Requirement]) -> list[Requirement]:
    return [r for r in requirements if not r.value]


def _raise_if_missing(what: str, requirements: list[Requirement]) -> None:
    missing = _missing(requirements)
    if not missing:
        return
    lines = "\n".join(f"  {r.env_name} — {r.purpose}" for r in missing)
    raise ConfigError(f"Не задано для «{what}»:\n{lines}")


def check_storage() -> None:
    _raise_if_missing(
        "хранилище",
        [
            Requirement("STORAGE_DSN", storage.DSN, "подключение к базе"),
            Requirement("STORAGE_REDIS_URL", storage.REDIS_URL, "очередь задач"),
        ],
    )


def check_collect() -> None:
    """Что нужно, чтобы собрать базу доноров."""
    if serp.SANDBOX:
        # Проверяется ПЕРВЫМ, до требований к ключам. Раньше было наоборот,
        # и получался тупик: с ключом отказ говорил «уберите ключ», а без
        # ключа — «нужен ключ». Совет вёл в другую ошибку, и пройти по нему
        # было нельзя. Ни один тест этого не видел: все они ставили ключ
        # на место, то есть проверяли ровно то состояние, в котором совет
        # не исполняется.
        #
        # Стоило 670 юнитов 19.09.2026: задача из очереди прошла с песочницей
        # выдачи и живым ключом Ahrefs. Домены в песочнице выдуманные,
        # а метрики по ним — обычные платные запросы, и заметить это можно
        # было только по счёту.
        raise ConfigError(
            "Песочница выдачи (SERP_SANDBOX=true) прогон не запускает.\n"
            "  Домены в ней выдуманы, а метрики по ним платные: прогон целиком "
            "стоил бы юниты за данные, которые заведомо никому не нужны.\n"
            "  Песочница проверяет слой выдачи, а не прогон целиком — так её "
            "и зовут тесты.\n"
            "  Нужен прогон — выключите SERP_SANDBOX и считайте его платным."
        )

    _raise_if_missing(
        "сбор доноров",
        [
            Requirement("AHREFS_API_KEY", ahrefs.API_KEY, "метрики доменов"),
            Requirement("SERP_PROVIDER", serp.PROVIDER, "источник выдачи"),
        ],
    )


def check_outreach() -> None:
    """Что нужно, чтобы писать письма. Для сбора базы не требуется."""
    _raise_if_missing(
        "рассылка",
        [
            Requirement("OUTREACH_SENDGRID_API_KEY", outreach.SENDGRID_API_KEY, "отправка"),
            Requirement("OUTREACH_INBOUND_SECRET", outreach.INBOUND_SECRET, "приём ответов"),
        ],
    )
    check_inbound_secret()


def check_inbound_secret() -> None:
    """Секрет приёма обязан быть из латиницы и цифр.

    Он едет в заголовке HTTP, а заголовки — ASCII. Секрет с кириллицей
    не отправится вовсе, и выглядеть это будет не как поломка настройки,
    а как «секрет не совпал» на каждом письме: платформа получит отказ,
    начнёт повторять, и искать причину будут в платформе.

    Найдено живым прогоном: тесты писали секрет латиницей и этого
    не показывали.
    """
    secret = outreach.INBOUND_SECRET
    if secret and not secret.isascii():
        raise ConfigError(
            "OUTREACH_INBOUND_SECRET содержит символы вне латиницы. "
            "Секрет едет в заголовке HTTP, а заголовки бывают только ASCII: "
            "с таким секретом приём ответов не заработает никогда.\n"
            "  Сгенерировать годный: openssl rand -hex 32"
        )


def check_access() -> None:
    """Что нужно, чтобы кто-то мог войти.

    Проверяется на старте сервера, а не при первом входе: сервис без
    секрета подписи не пускает никого, и узнать об этом лучше в момент
    развёртывания, чем от сотрудника, который не может войти.

    Короткий секрет — тот же отказ здесь (аудит 19.11.2026): до этого длину сверял только
    вход (`access/tokens.py`), и сервис стартовал, чтобы отказать первому сотруднику.
    Длина — в байтах, как её считает подпись.
    """
    _raise_if_missing(
        "вход в сервис",
        [
            Requirement(
                "ACCESS_JWT_SECRET",
                access.JWT_SECRET,
                "подпись пропусков; сгенерировать: openssl rand -hex 32",
            )
        ],
    )
    if len(access.JWT_SECRET.encode("utf-8")) < access.MIN_SECRET_BYTES:
        raise ConfigError(
            f"ACCESS_JWT_SECRET короче {access.MIN_SECRET_BYTES} байт — такую подпись "
            "подбирают, и пропуск администратора выписал бы себе кто угодно.\n"
            "  Сгенерировать: openssl rand -hex 32"
        )


def say_inbound_secret() -> None:
    """Секрет приёма почты короче `MIN_SECRET_BYTES` — громкая строка при старте, не отказ.

    Им подписаны метки отписки и адресов ответа, им же вебхук приёма узнаёт платформу
    (`letters/unsubscribe.py`, `letters/reply_to.py`, `api/inbound/routes.py`): короткий
    подбирают — и подделывают метки. Отказом он не стал (аудит 19.11.2026): длину секрета
    на бою никто не сверял, а смена секрета гасит ссылки отписки и адреса ответа во всех
    ушедших письмах — её планируют, а не делают по падению сервиса. Значения в строке нет.
    """
    if 0 < len(outreach.INBOUND_SECRET.encode("utf-8")) < access.MIN_SECRET_BYTES:
        logger.error(
            "OUTREACH_INBOUND_SECRET короче %s байт: метки отписки и адресов ответа подписаны "
            "ключом, который подбирают. Сменить на openssl rand -hex 32 — с планом: ссылки "
            "отписки и адреса ответа в ушедших письмах со сменой перестанут сходиться",
            access.MIN_SECRET_BYTES,
            extra={"setting": "OUTREACH_INBOUND_SECRET", "min_bytes": access.MIN_SECRET_BYTES},
        )


def check_crawl() -> None:
    """Платные уровни обхода, если они заданы, — годны: адреса прокси и шаблон сервиса.

    Проверяется на старте обходчика, а не на первой закрытой странице: битый адрес
    прокси выдаёт куски пароля в отказе транспорта и в журнале, а шаблон без хоста —
    ключ сервиса целиком (`config/crawl.proxy_problem`, `render_template_problem`).
    В отказе — номер адреса и что с ним, но не сам адрес.

    Конфиг обхода грузится здесь, а не при импорте модуля: проверки старта зовут все
    процессы, и опечатка в числе `CRAWL_*` не должна ронять воркеры писем и продаж.
    """
    from backend.config import crawl  # noqa: PLC0415 — нужен только обходчику, см. выше

    lines = [
        f"  CRAWL_PROXY_URLS: адрес №{number} — {problem}"
        for number, url in enumerate(crawl.PROXY_URLS, start=1)
        if (problem := crawl.proxy_problem(url)) is not None
    ]
    if crawl.RENDER_API_KEY:
        problem = crawl.render_template_problem(crawl.RENDER_API_URL)
        if problem is not None:
            lines.append(f"  CRAWL_RENDER_API_URL: {problem}")
    if lines:
        raise ConfigError("Платные уровни обхода настроены с ошибкой:\n" + "\n".join(lines))
