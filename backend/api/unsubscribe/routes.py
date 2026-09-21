"""Страница отписки — вторая и последняя ручка наружу без пропуска.

**Почему она обязана быть.** Адрес отписки стоит в юридическом блоке
каждого письма, и до этого среза он был строкой, за которой ничего нет.
Донор жмёт «отписаться», попадает в никуда и жмёт «спам» — а жалоба
бьёт не по одному письму, а по репутации всех доменов рассылки сразу.

**Переход ничего не меняет, меняет нажатие.** По ссылкам в письмах
ходят почтовые сканеры и предпросмотры ссылок; отписка на `GET`
сработала бы за донора, который её не нажимал, и выглядело бы это
в базе как его решение. Поэтому `GET` только показывает вопрос,
а пишет `POST`.

**Тот же `POST` — это и есть отписка в один клик** по RFC 8058: почтовый
клиент, увидев заголовки `List-Unsubscribe` и `List-Unsubscribe-Post`,
шлёт сюда `POST` сам, когда человек нажал кнопку в интерфейсе почты.
Там подтверждение уже случилось, и второе спрашивать негде.

**Секрета в ссылке нет и быть не может** — её открывает донор. Защита
здесь другая: подписанная метка (подобрать нельзя), запись только
по `POST`, потолок частоты и полное отсутствие разницы в ответах
на «метки нет», «подпись не сошлась» и «донора удалили».
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session
from backend.api.unsubscribe import page
from backend.features.core.models.domain import DomainModel
from backend.features.letters import unsubscribe
from backend.features.letters.optout import unsubscribe_domain
from backend.shared.sliding_window import SlidingWindow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/unsubscribe", tags=["отписка"])

#: Обращений в минуту с одного адреса. Живому человеку хватает двух:
#: открыть страницу и нажать кнопку. Остальное — перебор меток.
RATE_PER_MINUTE = 20

#: Как подписана строка стоп-листа, заведённая этой страницей. По ней
#: в базе видно, отписался донор сам или его отписал человек.
SOURCE = "страница отписки"

_throttle = SlidingWindow()


def _too_often(client: str) -> HTMLResponse | None:
    if _throttle.count(client) >= RATE_PER_MINUTE:
        logger.warning("отписка: слишком часто с %s", client)
        return HTMLResponse(
            page.too_often(),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(_throttle.retry_after(client))},
        )
    _throttle.record(client)
    return None


def _client(request: Request) -> str:
    return request.client.host if request.client else "неизвестно"


@router.get("/{label}", response_class=HTMLResponse, summary="Страница отписки")
async def show(
    label: str,
    request: Request,
    session: AsyncSession = Depends(db_session),
) -> HTMLResponse:
    """Спросить донора, отписывать ли его. Ничего не меняет."""
    refused = _too_often(_client(request))
    if refused is not None:
        return refused

    domain_id = unsubscribe.domain_id_from(label)
    host = await _host(session, domain_id)
    if host is None:
        return HTMLResponse(page.gone(), status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse(page.confirm(host, action=request.url.path))


@router.post("/{label}", response_class=HTMLResponse, summary="Отписать донора")
async def apply(
    label: str,
    request: Request,
    session: AsyncSession = Depends(db_session),
) -> HTMLResponse:
    """Нажатие кнопки на странице — или один клик из почтового клиента."""
    refused = _too_often(_client(request))
    if refused is not None:
        return refused

    domain_id = unsubscribe.domain_id_from(label)
    if domain_id is None:
        return HTMLResponse(page.gone(), status_code=status.HTTP_404_NOT_FOUND)

    outcome = await unsubscribe_domain(session, domain_id, source=SOURCE)
    if outcome is None:
        return HTMLResponse(page.gone(), status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()

    logger.info(
        "отписка: %s — %s, снято писем %s",
        outcome.host,
        "впервые" if outcome.first_time else "повторно",
        outcome.stopped,
    )
    return HTMLResponse(page.done(outcome.host))


async def _host(session: AsyncSession, domain_id: int | None) -> str | None:
    """Имя сайта для страницы. `None` — показывать нечего и некого отписывать."""
    if domain_id is None:
        return None
    domain = await session.get(DomainModel, domain_id)
    return domain.host if domain is not None else None
