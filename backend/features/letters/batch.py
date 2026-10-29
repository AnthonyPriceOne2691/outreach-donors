"""Отправка очереди этапа одной кнопкой: «Отправить очередь · N».

До 06.10.2026 письма уходили только по одному, и это было решение экрана:
спорное решение видит человек (`docs/WEB_LAYER.md`). Для боевого запуска
Anthony выбрал пачку — «вся очередь» (06.10.2026): письма переписаны моделью
с проверкой отличия от шаблона, а отправка по одному на десятках писем
в день стала узким местом запуска.

**Каждое письмо пачки идёт тем же путём, что одно** (`Sending.send`):
стоп-листы, решение по донору или рекламодателю, предохранитель, дневной
лимит разгона ящика. Пачка — цикл поверх одной отправки, а не своя: две
отправки разошлись бы на первой правке. Этап — параметр: продажи встают
сюда же своим этапом. В пачке только первые письма (`LetterRepository.queued`):
добивка и ответ уходят своим путём и с ящика своей переписки (`mailbox.py`).

**Когда пачка останавливается.** Ящики выбрали дневной лимит — остальное
ждёт в очереди: итог пачки говорит, уйдёт ли оно само, и если нет — когда нажать
пачку снова (`_stop_words`). Без этого «завтра лимит откроется» читалось как
«завтра уйдут сами» (замечание тест-группы 23.10.2026). Само остальное уходит,
только если очередь отправляется сама (`autosend.py`, замечание тест-группы
29.10.2026): её проход — эта же пачка, с окном получателя и паузами ящиков (`Paced`).

Связь с почтой оборвалась — ушло ли письмо,
неизвестно, и слать дальше вслепую нельзя: письмо остаётся «отправляется»
(`Sending._hand_over`), и его исход решает событие платформы или человек
в блоке «Исход неизвестен» (`unknown_outcome.py`). Почта отказала не письму,
а любому (`PlatformRefusalError`: ключ не принят, учётке отказано, частота или
сбой после повторов, платформа недоступна) — следующему ответ был бы тем же:
пачка встаёт, остальное ждёт в очереди. До 13.10.2026 пачка перебирала письма
дальше — по минуте с повторами на каждое, и пачка из двухсот висела до часа
(решение Anthony, ревью стыков с продажами). Отказ по одному письму
(стоп-лист, предохранитель, донор отклонён после сборки, сайт в работе у другого
направления) пачку не останавливает: он считается и называется в итоге.

**Две пачки разом не отправят письмо дважды**: захват письма в `Sending`
отдаёт второй отказ «уже не в очереди», и он попадает в итог словами.

**Пачка — одной аудитории этапа** (`campaigns.audience`). У Этапа 2 их две:
рекламодатели по найденной ссылке и бизнесы ниши из выдачи — у каждой своя
вкладка, своя очередь и своя кнопка. Нажатие на одной вкладке письма другой
не отправляет и в «осталось» их не считает. Без аудитории — по ссылке: так
шла пачка до бизнесов ниши, и у доноров и продаж других аудиторий нет.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import SalesNotConnectedError
from backend.features.crawl.niche import LINKS
from backend.features.letters import mailbox
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import (
    NoSenderError,
    NotQueuedError,
    NotReadyError,
    NotWritingError,
    OtherRoleError,
    OutsideWindowError,
    RejectedDonorError,
    RemovedAdvertiserError,
    SendError,
    Sending,
    StalePriceError,
    SuppressedError,
    UndecidedDonorError,
    UnknownZoneError,
    WithdrawnLinkError,
)
from backend.features.letters.transport import Mail, PlatformRefusalError
from backend.features.letters.unknown_outcome import STUCK_MINUTES

logger = logging.getLogger(__name__)

#: Потолок одной пачки — столько же, сколько очередь показывает на экране
#: (`LetterRepository.queued`): уходит ровно то, что человек мог увидеть.
BATCH_MAX = 200

#: Отказ — словами, одинаковыми для похожих: текст отказа называет адрес
#: или донора, и по нему итог развалился бы на строку на каждое письмо.
#: Подклассы — раньше родителей: донор, отклонённый после сборки, — тоже
#: «стоп-лист» по классу, но человеку важнее первое.
_WHY: tuple[tuple[type[SendError], str], ...] = (
    (RejectedDonorError, "донор отклонён после сборки"),
    # И бизнес ниши, о котором после сборки сказали «не пишем» (`offer_standing.py`).
    (RemovedAdvertiserError, "рекламодатель снят после сборки"),
    (WithdrawnLinkError, "ссылку письма сняли"),
    # Оффер «мы дешевле» без свежей цены донора — обещание, которого не сдержать.
    (StalePriceError, "цена донора устарела или снята"),
    # Донор или площадка оффера — «кому не пишем» ТЗ (`runs.exclusions`): письмо
    # остановлено с причиной (`donor_standing.stop_for_good`).
    (NotWritingError, "кому не пишем"),
    # Сайту уже пишет другое направление: первое письмо снято (`site_role.py`) — одна роль на сайт.
    (OtherRoleError, "сайт в работе у другого направления"),
    (SuppressedError, "стоп-лист"),
    # Донор вернулся в «предложен» или бизнес ниши снова ждёт решения: письмо ждёт в очереди.
    # До 28.10.2026 — «донор не принят человеком»: письмо бизнесу ниши — не донору.
    (UndecidedDonorError, "ждёт решения человека"),
    (NotReadyError, "письмо не готово к отправке"),
    (NotQueuedError, "уже не в очереди"),
    # Окно получателя (`core/window.py`): письмо ждёт в очереди его рабочих часов.
    (OutsideWindowError, "вне окна получателя"),
    (UnknownZoneError, "пояс получателя неизвестен"),
)
#: Отказ предохранителя (`sendgrid.py`): пока список своих адресов не пуст,
#: письма уходят только на них.
_ALLOWLIST_MARK = "не в списке разрешённых получателей"
#: Пачка встала на лимите ящиков: письма остаются в очереди, но сами не уйдут. Первые письма
#: доноров и рекламодателей уходят пачкой, по одному или сами, пока их очередь отправляется сама
#: (`autosend.py`); у продаж — пачкой, пока автоотправка выключена (включённая закрывает пачку
#: вовсе, `sales/mail.py`).
BY_HAND = "Сами письма не уйдут — очередь уходит только по нажатию"
_AGAIN_TOMORROW = "завтра нажмите «Отправить очередь» ещё раз"
_AGAIN_LATER = "нажмите «Отправить очередь» снова, когда ящики смогут писать"
#: Начало остановки, когда почта отказала не письму, а любому (`PlatformRefusalError`).
EVERYONE_REFUSED = "почта отказала любому письму"
#: Та же остановка, когда очередь отправляется сама: остальное уйдёт без нажатия.
BY_ITSELF = "Остальное уйдёт само — очередь отправляется сама"
_ITSELF_TOMORROW = "продолжит завтра, когда лимит откроется"
_ITSELF_LATER = "продолжит, когда ящики смогут писать"


class Paced(Protocol):
    """Проход автоотправки (`autosend.Rest`): какие письма очереди брать — у чьих получателей сейчас
    рабочее время (`only`), — и паузы ящиков: выбор ящика первого письма их не берёт
    (`mailbox.resting`), а ящик, отдавший письмо, встаёт на паузу до конца прохода."""

    @property
    def only(self) -> Collection[int]:
        """Письма очереди, которые проход может слать сейчас."""

    def boxes(self) -> Collection[int]:
        """Ящики на паузе сейчас."""

    def took(self, box_id: int, letter_id: int) -> None:
        """Ящик `box_id` отдал почте письмо `letter_id`."""


@dataclass(slots=True)
class BatchReport:
    """Итог пачки — числами и словами, для строки задачи на экране."""

    sent: int = 0
    refused: Counter[str] = field(default_factory=Counter)
    #: Почему пачка остановилась раньше конца очереди; `None` — дошла до конца.
    stopped: str | None = None
    #: Сколько писем этапа осталось в очереди — всех, без потолка пачки.
    left: int = 0
    #: Письмо, на котором оборвалась связь с почтой: ушло ли оно, неизвестно (`autosend.py`
    #: стоит на нём, пока оно «отправляется»). В итог задачи не идёт — его слова в `stopped`.
    unsure: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "refused": dict(self.refused),
            "stopped": self.stopped,
            "left": self.left,
        }


def why(exc: SendError) -> str:
    """Почему письмо не ушло — словами, одинаковыми для похожих отказов."""
    for kind, words in _WHY:
        if isinstance(exc, kind):
            return words
    if _ALLOWLIST_MARK in str(exc):
        return "предохранитель: адрес не из своих"
    return "почта отказала"


async def send_queue(
    session: AsyncSession,
    transports: Mail,
    *,
    stage: Stage,
    author_id: int | None = None,
    limit: int = BATCH_MAX,
    audience: str = LINKS,
    automatic: bool = False,
    paced: Paced | None = None,
) -> BatchReport:
    """Отправить очередь этапа и аудитории по одному письму, пока у ящиков есть лимит.

    `automatic` — очередь отправляется сама (`autosend.py`): остановка на лимите говорит, что
    остальное уйдёт само. `paced` — проход автоотправки: только письма в окне получателя, по
    порядку очереди, и с ящика — письмо за проход (`Paced`)."""
    repository = LetterRepository(session)
    queued = await repository.queued(stage=stage, audience=audience, limit=limit)
    ids = [row.message.id for row in queued if paced is None or row.message.id in paced.only]
    sending = Sending(session, transports)
    report = BatchReport()
    for letter_id in ids:
        sent = report.sent
        with mailbox.resting(() if paced is None else paced.boxes()):
            stop = await _send_one(
                sending, letter_id, author_id, report, automatic or paced is not None
            )
        if stop is not None:
            report.stopped = stop
            break
        if paced is not None and report.sent > sent:
            letter = await session.get(MessageModel, letter_id)
            if letter is not None and letter.sender_id is not None:
                paced.took(letter.sender_id, letter_id)
    # Вся очередь этапа и аудитории, а не её первые `limit`: остальное возьмёт следующая
    # пачка той же вкладки.
    report.left = await repository.queued_count(stage=stage, audience=audience)
    logger.info(
        "письма: пачка этапа %s (%s) — ушло %s, не ушло %s, осталось %s",
        stage.value,
        audience,
        report.sent,
        sum(report.refused.values()),
        report.left,
    )
    return report


async def _send_one(
    sending: Sending,
    letter_id: int,
    author_id: int | None,
    report: BatchReport,
    automatic: bool = False,
) -> str | None:
    """Одно письмо пачки. Возвращает причину остановки пачки или `None`."""
    try:
        await sending.send(letter_id, author_id=author_id)
    except (NoSenderError, SalesNotConnectedError) as exc:
        # Лимит ящиков или продажи, не подключённые к почте (ответ моста `core/stages`):
        # следующее письмо упрётся в то же — пачка встаёт с причиной словами, а не
        # «связь с почтой оборвалась».
        logger.info("письма: пачка встала на письме №%s — %s", letter_id, exc)
        return _stop_words(exc, automatic)
    except SendError as exc:
        logger.info("письма: письмо №%s пачки не ушло — %s", letter_id, exc)
        report.refused[why(exc)] += 1
        if isinstance(exc.__cause__, PlatformRefusalError):
            return _refused_all(letter_id, exc.__cause__)
        return None
    except Exception:
        logger.exception("письма: пачка остановлена на письме №%s — связь с почтой", letter_id)
        report.unsure = letter_id
        return (
            f"связь с почтой оборвалась на письме №{letter_id}: ушло ли оно, неизвестно, "
            "и дальше пачка не шла. Сообщит о нём платформа — исход решится сам; нет — "
            f"через {STUCK_MINUTES} минут письмо встанет в блок «Исход неизвестен» "
            "на экране писем: там его отмечают ушедшим или возвращают в очередь"
        )
    report.sent += 1
    return None


def _stop_words(exc: NoSenderError | SalesNotConnectedError, automatic: bool = False) -> str:
    """Почему пачка встала, словами отказа, — а когда писать некому, ещё и уйдёт ли остальное
    само: у очереди, которая отправляется сама (`automatic`), — да, иначе — нет, и когда нажать
    пачку снова: завтра, если ящики выбрали дневной лимит (`mailbox.TOMORROW`), иначе — когда
    ящики смогут писать (все выключены или отсеяны лимитами)."""
    said = str(exc)
    if not isinstance(exc, NoSenderError):
        return said
    return nobody_words(said, automatic)


def nobody_words(said: str, automatic: bool = False) -> str:
    """Отказ выбора ящика «писать некому» — и уйдёт ли остальное само (`_stop_words`)."""
    tomorrow = said.endswith(mailbox.TOMORROW)
    if automatic:
        return f"{said}. {BY_ITSELF}: {_ITSELF_TOMORROW if tomorrow else _ITSELF_LATER}"
    return f"{said}. {BY_HAND}: {_AGAIN_TOMORROW if tomorrow else _AGAIN_LATER}"


def _refused_all(letter_id: int, refusal: PlatformRefusalError) -> str:
    """Почта отказала любому письму: причина остановки пачки словами, без ключа и адреса."""
    logger.warning("письма: пачка встала на письме №%s — %s", letter_id, refusal.reason)
    return (
        f"{EVERYONE_REFUSED} — {refusal.reason}: письмо №{letter_id} и остальные "
        "остались в очереди. Следующему письму ответ был бы тем же; пачку — заново, когда "
        "причина уйдёт"
    )
