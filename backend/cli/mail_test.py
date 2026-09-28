"""Пробное письмо себе: `outreach mail-test`.

Проверка домена отправки до первого донора. Письмо собирается тем же
кодом, что письмо донору (`features/letters/probe.py`), и уходит через
настроенный транспорт — при `OUTREACH_TRANSPORT=sendgrid` по-настоящему.

    outreach mail-test --to me@ours.example
    outreach mail-test --to me@ours.example --sender anna@mail-a.example --stage advertisers

**В базу не пишет ничего — и не может.** Транзакция открывается только
на чтение: попытка записи упала бы в самой базе, а не прошла бы молча.
Пробное письмо не заводит строку письма, не тратит дневной кап и не
двигает разгон ящика: оно не рассылка, и считать его рассылкой значит
отнять у ящика письмо донору.

**Предохранитель действует как обычно.** Пока `OUTREACH_ALLOWED_RECIPIENTS`
не пуст, письмо уйдёт только на адрес из списка. Проверку и надо делать
до снятия предохранителя — со своими ящиками в списке.

**Что проверить — печатается.** Заголовки полученного письма говорят
о настройке домена больше любого ответа платформы: подпись DKIM с доменом
отправителя, SPF, DMARC, наш `Message-ID` без подмены, адрес ответа
на поддомене ответов, заголовок отписки.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import outreach as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import SenderModel
from backend.features.letters import identity
from backend.features.letters.probe import Probe, compose_probe
from backend.features.letters.sending import SendError
from backend.features.letters.transport import Transport, TransportError
from backend.features.letters.transport_factory import build_transport

EXIT_OK = 0
#: Тот же код, что у `letters-send`: письмо не ушло.
EXIT_NOT_SENT = 7


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    probe = sub.add_parser(
        "mail-test", help="одно пробное письмо себе — проверить домен отправки, не тратя донора"
    )
    probe.add_argument(
        "--to",
        required=True,
        help="свой ящик; пока предохранитель включён, он должен быть в OUTREACH_ALLOWED_RECIPIENTS",
    )
    probe.add_argument(
        "--sender", help="с какого ящика рассылки; по умолчанию — первый включённый ящик этапа"
    )
    probe.add_argument(
        "--stage",
        choices=[stage.value for stage in Stage],
        default=Stage.DONORS.value,
        help="чей шаблон и чьи ящики: donors — письмо донору, advertisers — оффер рекламодателю",
    )


async def cmd_mail_test(args: argparse.Namespace) -> int:
    """Собрать и отправить одно пробное письмо."""
    check_storage()
    try:
        transport = build_transport()
    except TransportError as exc:
        print(f"Пробное письмо не отправлено: {exc}")
        return EXIT_NOT_SENT

    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await run_mail_test(
                session, transport, to=args.to, sender=args.sender, stage=Stage(args.stage)
            )
    finally:
        aclose = getattr(transport, "aclose", None)
        if aclose is not None:
            await aclose()
        await engine.dispose()


async def run_mail_test(
    session: AsyncSession,
    transport: Transport,
    *,
    to: str,
    sender: str | None,
    stage: Stage,
) -> int:
    """Пробное письмо на готовых сессии и транспорте.

    Отдельно от команды ради теста: база и платформа в нём поддельные,
    а путь — тот же.
    """
    # Только чтение: обещание «ни строки в базе» держит база, а не
    # внимательность следующей правки.
    await session.execute(text("SET TRANSACTION READ ONLY"))
    _announce(transport)

    sender_email = sender.strip().lower() if sender else await _first_sender(session, stage)
    if sender_email is None:
        print(
            f"Пробное письмо не отправлено: у этапа {stage.value} нет ни одного включённого "
            f"ящика. Завести — outreach sender-add --email … --stage {stage.value}, "
            "или включить выключенный на экране «Домены рассылки»"
        )
        return EXIT_NOT_SENT
    if sender:
        await _check_known(session, sender_email, stage)

    try:
        probe = compose_probe(to=to, sender_email=sender_email, stage=stage, real=transport.real)
        provider_id = await transport.send(probe.outgoing)
    except (SendError, TransportError) as exc:
        print(f"Пробное письмо не отправлено: {exc}")
        return EXIT_NOT_SENT

    _report(probe, provider_id, stage=stage, transport=transport)
    return EXIT_OK


def _announce(transport: Transport) -> None:
    """Куда вообще может уйти письмо — до того, как оно уйдёт."""
    if not transport.real:
        print(
            f"Транспорт «{transport.name}» ничего не отправляет: пробное письмо никуда не уйдёт, "
            "а боевые адреса он отвергает. Домен проверяется с OUTREACH_TRANSPORT=sendgrid"
        )
    elif cfg.ALLOWED_RECIPIENTS:
        print(
            "Предохранитель включён (OUTREACH_ALLOWED_RECIPIENTS): письмо уйдёт, только если "
            f"адрес в списке — {', '.join(cfg.ALLOWED_RECIPIENTS)}. Держите там свои ящики, "
            "пока идёт проверка"
        )
    else:
        print(
            "Предохранитель снят (OUTREACH_ALLOWED_RECIPIENTS пуст): отправка идёт на любой "
            "адрес. Пробное письмо — до снятия, со своими ящиками в списке"
        )


async def _first_sender(session: AsyncSession, stage: Stage) -> str | None:
    """Первый включённый ящик этапа — тот, с которого рассылка и начнёт."""
    found: str | None = await session.scalar(
        select(SenderModel.email)
        .where(SenderModel.stage == stage, SenderModel.enabled.is_(True))
        .order_by(SenderModel.id)
        .limit(1)
    )
    return found


async def _check_known(session: AsyncSession, email: str, stage: Stage) -> None:
    """Ящик, названный руками, сверяется с заведёнными — словами, не отказом.

    Письмо с незаведённого ящика уйдёт (не подтверждённый у платформы домен
    она отвергнет сама), но рассылка с него писать не будет, и проверка
    проверит не тот ящик. Ящик другого этапа — то же: домены у этапов
    разные, и оффер с домена доноров задевает их репутацию.
    """
    found = await session.scalar(select(SenderModel).where(SenderModel.email == email))
    if found is None:
        print(f"Внимание: ящик {email} не заведён (outreach senders) — рассылка с него не пишет")
    elif found.stage is not stage:
        print(
            f"Внимание: ящик {email} заведён для этапа {found.stage.value}, "
            f"а письмо — этапа {stage.value}"
        )


def _report(probe: Probe, provider_id: str, *, stage: Stage, transport: Transport) -> None:
    out = probe.outgoing
    fate = "ушло" if transport.real else "НЕ ушло (нулевой транспорт)"
    unsubscribe = (
        f"<{out.unsubscribe_url}>"
        if out.unsubscribe_url
        else f"нет — не задан {probe.missing_unsubscribe}"
    )
    print(
        f"\nПробное письмо {fate}: {out.to} ← {out.from_email} "
        f"(этап {stage.value}, транспорт {transport.name})."
    )
    print(f"  Номер у платформы:  {provider_id}")
    print(f"  Наш Message-ID:     {out.internet_message_id}")
    print(f"  Reply-To:           {out.reply_to or 'нет'}")
    print(f"  List-Unsubscribe:   {unsubscribe}")
    if not transport.real:
        # Нулевой транспорт никуда не пишет: смотреть заголовки не в чем.
        return
    print("\nЧто проверить в заголовках полученного письма («Показать оригинал»):")
    for number, line in enumerate(_checklist(probe), start=1):
        print(f"  {number}. {line}")
    print(
        f"\nИ ответить на него: ответ придёт на {_replies_domain(probe)} и появится на экране "
        "диалогов непривязанным — так проверяются MX поддомена ответов и Inbound Parse."
    )


def _replies_domain(probe: Probe) -> str:
    """Куда придёт ответ. У настоящей отправки адрес ответа есть всегда:
    без него `own_headers` отказал бы раньше."""
    return (probe.outgoing.reply_to or "").rpartition("@")[2]


def _checklist(probe: Probe) -> list[str]:
    """Что должно быть в заголовках — по пунктам, со своими значениями."""
    out = probe.outgoing
    domain = identity.sender_domain(out.from_email)
    unsubscribe = (
        "List-Unsubscribe на месте. Ссылка ведёт на страницу «This link no longer works»: "
        "у пробного письма нет донора, отписывать некого"
        if out.unsubscribe_url
        else f"List-Unsubscribe нет: не задан {probe.missing_unsubscribe} — у доноров его "
        "тоже не будет"
    )
    return [
        f"DKIM=pass, подпись с d={domain} — домен отправителя, а не домен платформы",
        "SPF=pass",
        "DMARC=pass",
        f"Message-ID ровно {out.internet_message_id}. Другой — платформа подменила его: "
        "добивки не лягут в ветку, ответ без метки не найдёт письма. До первого донора не слать",
        f"Reply-To на {_replies_domain(probe)}",
        unsubscribe,
    ]
