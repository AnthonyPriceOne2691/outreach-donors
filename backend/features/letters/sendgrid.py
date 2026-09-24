"""Боевая отправка через SendGrid.

Транспорт, который действительно шлёт письма живым людям. Всё, что
здесь есть сверх одного запроса к API, — это предохранители.

**Список разрешённых получателей.** Первые дни отправка идёт на свои
адреса, а не на доноров: формат письма, заголовки цепочки и отписка
проверяются на себе. Пустой список означает «пиши кому угодно» и
пишется в лог отдельной строкой при каждой отправке — тихо перейти
из проверки в боевую рассылку нельзя.

**Номер письма уезжает в `custom_args`** и возвращается в событиях
доставки. Без него событие о недоставке относится к письму, которое
мы опознаём по адресу получателя, — а у донора адресов бывает
несколько, и один из них уже мог смениться.

**Заголовки не украшение.** `In-Reply-To` и `References` кладут добивку
в ту же ветку у донора; `List-Unsubscribe` и `List-Unsubscribe-Post`
дают кнопку отписки в интерфейсе почты — её нажимают вместо «спам»,
и крупные почты учитывают её наличие в репутации отправителя.

**Отказ платформы — это отказ, а не молчание.** Любой не-2xx поднимает
`TransportError`, и письмо возвращается в очередь: отправка, которая
выглядит успешной при отказе платформы, — худший исход из возможных.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from backend.config import outreach as cfg
from backend.features.letters.transport import Outgoing, TransportError

logger = logging.getLogger(__name__)

API_URL = "https://api.sendgrid.com/v3/mail/send"

#: Заголовок ответа с номером письма у платформы. По нему событие
#: доставки находит наше письмо.
MESSAGE_ID_HEADER = "X-Message-Id"

TIMEOUT_S = 30.0


def _allowed(address: str, allowlist: tuple[str, ...]) -> bool:
    """Можно ли писать на этот адрес.

    Сравнение по адресу целиком и по домену: в списке удобно держать
    и «мой ящик», и «весь наш домен», а разбирать это на два поля
    значит однажды заполнить не то.
    """
    if not allowlist:
        return True
    target = address.strip().lower()
    domain = target.rsplit("@", 1)[-1]
    return any(target == item or domain == item.lstrip("@") for item in allowlist)


class SendGridTransport:
    """Отправка через SendGrid."""

    name = "sendgrid"
    real = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        allowlist: tuple[str, ...] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._key = api_key if api_key is not None else cfg.SENDGRID_API_KEY
        self._allowlist = allowlist if allowlist is not None else cfg.ALLOWED_RECIPIENTS
        if not self._key:
            raise TransportError(
                "OUTREACH_SENDGRID_API_KEY не задан — боевой транспорт выбран, "
                "а ключа платформы нет. Письма никуда не уйдут"
            )
        self._http = http or httpx.AsyncClient(timeout=TIMEOUT_S)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, outgoing: Outgoing) -> str:
        """Отдать письмо платформе. Возвращает её номер письма."""
        if not _allowed(outgoing.to, self._allowlist):
            raise TransportError(
                f"Адрес {outgoing.to} не в списке разрешённых получателей "
                f"(OUTREACH_ALLOWED_RECIPIENTS). Пока список не пуст, боевая отправка "
                "идёт только на свои адреса — это предохранитель первых дней"
            )
        if not self._allowlist:
            # Отдельной строкой и на каждое письмо: переход из проверки
            # на себе в боевую рассылку не должен случиться молча.
            logger.warning(
                "письма: предохранитель снят — письмо №%s уходит на боевой адрес %s",
                outgoing.message_id,
                outgoing.to,
            )

        response = await self._post(_payload(outgoing))
        provider_id: str | None = response.headers.get(MESSAGE_ID_HEADER)
        if not provider_id:
            # Без номера письма события доставки не к чему привязать:
            # отправка формально прошла, а недоставку мы не увидим.
            raise TransportError(
                "Платформа приняла письмо, но не вернула его номер "
                f"({MESSAGE_ID_HEADER}) — привязывать события доставки будет не к чему"
            )
        logger.info(
            "письма: письмо №%s ушло через sendgrid (кому %s, номер платформы %s)",
            outgoing.message_id,
            outgoing.to,
            provider_id,
        )
        return provider_id

    async def _post(self, payload: dict[str, object]) -> httpx.Response:
        """Отдать письмо, повторив только то, что точно не ушло.

        **Повторяется «не приняла»:** 429 и 5xx — это ответ платформы, письмо
        не ушло; соединение, которое не установилось, — тоже. **Не повторяется
        обрыв после отправки** (таймаут ожидания ответа и подобное): платформа
        могла принять письмо до того, как ответ потерялся, ключа идемпотентности
        у SendGrid нет, и повтор вслепую отправил бы донору второе такое же
        письмо — это жалоба на спам, а не лишняя строка.
        """
        for attempt in range(1, SEND_ATTEMPTS + 1):
            response = await self._attempt(payload, last=attempt == SEND_ATTEMPTS)
            if response is not None and (
                response.status_code not in RETRY_STATUSES or attempt == SEND_ATTEMPTS
            ):
                break
            pause = _pause(response, attempt)
            logger.warning(
                "письма: платформа не приняла (%s) — повтор через %.0f с (попытка %s из %s)",
                response.status_code if response is not None else "нет соединения",
                pause,
                attempt + 1,
                SEND_ATTEMPTS,
            )
            await _sleep(pause)

        assert response is not None  # последняя попытка либо ответила, либо бросила
        if response.status_code >= 400:
            raise TransportError(
                f"Платформа отказала ({response.status_code}): {response.text[:300]}"
            )
        return response

    async def _attempt(self, payload: dict[str, object], *, last: bool) -> httpx.Response | None:
        """Одна попытка. `None` — соединение не установилось, можно повторить."""
        try:
            return await self._http.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if last:
                raise TransportError(
                    f"Почтовая платформа недоступна ({exc!r}) — письмо не ушло"
                ) from exc
            logger.warning("письма: платформа недоступна (%r) — письмо не ушло", exc)
            return None
        except httpx.HTTPError as exc:
            raise TransportError(
                f"Почтовая платформа не ответила ({exc!r}) — письмо могло уйти. "
                "Вслепую не повторяем: ключа идемпотентности у платформы нет. "
                "Проверить в её кабинете (Activity) и только потом отправлять снова"
            ) from exc


#: Явный отказ платформы «не приняла»: частота и её собственные сбои.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
SEND_ATTEMPTS = 3
#: Паузы между попытками, если платформа не назвала свою (`Retry-After`).
BACKOFF_SEC = (2.0, 5.0)
#: Потолок ожидания: `Retry-After` бывает в минутах, а человек ждёт у кнопки.
MAX_PAUSE_SEC = 30.0

#: Отдельным именем, чтобы тест гасил паузу здесь, а не у всего процесса
#: (урок `shared/net/retry.py`).
_sleep = asyncio.sleep


def _pause(response: httpx.Response | None, attempt: int) -> float:
    raw = response.headers.get("Retry-After", "") if response is not None else ""
    if raw.strip().isdigit():
        return min(float(raw), MAX_PAUSE_SEC)
    return BACKOFF_SEC[min(attempt - 1, len(BACKOFF_SEC) - 1)]


def _payload(outgoing: Outgoing) -> dict[str, object]:
    """Письмо в том виде, в каком его принимает платформа."""
    headers: dict[str, str] = {}
    if outgoing.in_reply_to:
        headers["In-Reply-To"] = outgoing.in_reply_to
        headers["References"] = outgoing.in_reply_to
    if outgoing.unsubscribe_url:
        headers["List-Unsubscribe"] = f"<{outgoing.unsubscribe_url}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    personalization: dict[str, object] = {"to": [{"email": outgoing.to}]}
    payload: dict[str, object] = {
        "personalizations": [personalization],
        "from": {"email": outgoing.from_email, "name": outgoing.from_name},
        "subject": outgoing.subject,
        "content": [{"type": "text/plain", "value": outgoing.body}],
        # Номер нашего письма возвращается в событиях доставки.
        "custom_args": {"message_id": str(outgoing.message_id)},
        # Отслеживание кликов ломает ссылку отписки и подменяет адреса
        # в тексте на свои — в холодном письме это и выглядит как спам,
        # и мешает донору увидеть, куда он идёт.
        "tracking_settings": {
            "click_tracking": {"enable": False},
            "open_tracking": {"enable": False},
        },
    }
    if outgoing.reply_to:
        payload["reply_to"] = {"email": outgoing.reply_to}
    if headers:
        payload["headers"] = headers
    return payload
