"""CrawlRunModel и OutLinkModel — обход донора и то, что он нашёл.

**Сырой HTML не хранится** — так требует требование. Страницы живут
в памяти ровно столько, сколько нужно снять с них ссылки; в базу едут
адрес, анкор, пометки `rel` и домен-получатель.

**Запись обхода нужна не для истории, а для честности.** Без неё нельзя
отличить «у донора нет исходящих ссылок» от «мы не смогли его обойти»:
в обоих случаях строк со ссылками ноль. Исход, причина остановки и то,
какой уровень каскада не поднялся, лежат здесь — и это то, чего срез
обхода был должен и не отдал.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models._mixins import SetByMixin, TimestampedMixin
from backend.features.core.models._mixins import sql_enum as _enum
from backend.shared.database.base import Base


class CrawlRunModel(TimestampedMixin, Base):
    """Один обход одного донора: чем кончился и что успел."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)

    # Обход идёт до часа, поэтому у него есть жизнь между нажатием и исходом:
    # строка заводится постановкой в очередь, а исход и причина остановки
    # появляются только у законченного (до того — `NULL`, а не выдуманный исход).
    status: Mapped[CrawlStatus] = mapped_column(
        _enum(CrawlStatus), nullable=False, default=CrawlStatus.DONE
    )
    outcome: Mapped[CrawlOutcome | None] = mapped_column(_enum(CrawlOutcome), nullable=True)
    stop_reason: Mapped[StopReason | None] = mapped_column(_enum(StopReason), nullable=True)

    # Задача очереди, которая ведёт обход: по ней разбор мёртвых узнаёт,
    # жив ли воркер. Кто поставил — для журнала и экрана.
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Свой потолок страниц глубокого обхода (`crawl/deep.py`); пусто — обычный, из настроек.
    # В строке, а не в задаче: продолжение после выкатки и разбор мёртвых читают его отсюда.
    max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Номер части обхода 24/7 (`crawl/always.py`); пусто — обход не из цепочки 24/7. У части
    # потолок страниц и страницы — всей цепочки донора: она продолжает открытое прежними частями.
    depth_part: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Сколько раз обход продолжали после смерти воркера. Остановка выкаткой
    # сюда не считается — она штатная и сама ставит продолжение.
    resumes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # С чего продолжить: открытые страницы, очередь адресов, счётчики
    # (`crawl/progress.py`). Пишется пачками по ходу вместе со ссылками.
    # `none_as_null`: стёртый чекпоинт — пустое поле, а не JSON `null` (иначе
    # `checkpoint IS NULL` врёт про законченный обход; замечено живым обходом).
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

    pages_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    articles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Числа прохода: запросы, отказы, доли, время, источник списка страниц.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Чего каскаду не хватило. В записи, а не в логе: иначе обход,
    # у которого не поднялся браузер, выглядит зелёным и врёт, что
    # закрытые страницы проверены.
    degradation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_crawl_runs_host", "host"),
        Index("idx_crawl_runs_created_at", "created_at"),
        # Донор — один обход за раз. Замок держит база, а не код: две кнопки,
        # нажатые разом, или кнопка поверх идущего обхода иначе завели бы
        # два обхода одного сайта с двойной нагрузкой на него и двойными ссылками.
        Index(
            "uq_crawl_runs_active_host",
            "host",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    links: Mapped[list[OutLinkModel]] = relationship(
        "OutLinkModel", back_populates="run", cascade="all, delete-orphan"
    )


class OutLinkModel(TimestampedMixin, Base):
    """Исходящая ссылка донора: откуда, куда, каким анкором и с какими пометками."""

    __tablename__ = "outlinks"

    id: Mapped[int] = mapped_column(primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )

    # Адреса — `Text`: путь со списком параметров в 255 символов
    # не укладывается, а обрезанный адрес нельзя ни открыть, ни сверить.
    page_url: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    target_host: Mapped[str] = mapped_column(String(255), nullable=False)
    target_root: Mapped[str] = mapped_column(String(255), nullable=False)

    # Две формы анкора: показываемая человеку и ключ сравнения без
    # регистра и диакритики. Одной не хватает — см. okf/crawl-access.md
    # и грабли в `features/crawl/links.py`.
    anchor: Mapped[str] = mapped_column(Text, nullable=False, default="")
    anchor_key: Mapped[str] = mapped_column(Text, nullable=False, default="")

    nofollow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sponsored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ugc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Ссылка стояла в теле статьи или рядом с ним. Не фильтр, а признак:
    # размещения боевой ниши живут в витринах офферов, и выбросить
    # их значит не найти никого.
    in_body: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Корень домена угадан: суффикс неизвестен вшитому снимку списка.
    # Ноль — норма, рост — повод обновить список, а не тихая потеря.
    root_guessed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Про статью, а не про ссылку: чем она помечена сама («рубрика
    # sponsored-post», «This post is sponsored by…») и когда вышла.
    # У обходов до 06.10.2026 — `NULL`: не смотрели, а не «пометки нет».
    page_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_published: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        Index("idx_outlinks_run", "crawl_run_id"),
        Index("idx_outlinks_target_root", "target_root"),
    )

    run: Mapped[CrawlRunModel] = relationship("CrawlRunModel", back_populates="links")


class CrawlBacklogModel(TimestampedMixin, Base):
    """Донор, загруженный списком и ждущий своего обхода, — строка «очереди загрузки».

    Обход загруженных ставится не сразу весь (`features/crawl/backlog.py`): десять тысяч строк
    «в очереди» закрыли бы последние обходы, а обход, запущенный человеком, ждал бы месяц за ними.
    Проход разбора отдаёт отсюда доноров обходчикам по мере того, как те освобождаются; ушедший
    в обход, уже идущий или ставший негодным — строку теряет. Домен — один раз: загруженный
    дважды обходится раз.
    """

    __tablename__ = "crawl_backlog"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    #: Кто велел обойти — почта; от его имени и ставится обход (`requested_by` строки обхода).
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Потолок страниц глубокого обхода (`crawl/deep.py`); пусто — обычный обход.
    max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CrawlNightModel(TimestampedMixin, Base):
    """Ночной круг обхода (`features/crawl/night.py`): одна строка на дату.

    Строка — и замок, и след: уникальная дата держит «один раз за ночь» при рестарте и двух
    процессах, а расчёт и числа остаются, чтобы утром было видно, что считали и что вышло.
    """

    __tablename__ = "crawl_nights"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Дата начала круга по Москве.
    night: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    #: Значение `night.NightStatus`.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: После этого часа новые ночные обходы не выдаются.
    issue_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Когда снят остаток.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Расчёт: из чего вышло, сколько доноров, по очередям. Пусто — расчёта ещё нет.
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    #: Сколько поставлено, сколько ушло обходчикам и сколько снято по сроку.
    taken: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    given: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dropped: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Почему круг не шёл — словами.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class DonorLoadModel(TimestampedMixin, Base):
    """Загрузка доноров: партия файлом или донор, заведённый вручную, — строка на загрузку.

    Очередь загрузки теряет строку, как только донор ушёл в обход (`crawl/backlog.py`), и вопрос
    «что дала эта партия» было не задать: от неё не оставалось ничего. Панель рекламодателей
    считает по этой записи выборку «партия» и качество загрузок (`crawl/panel.py`,
    `crawl/load_quality.py`). Загрузки до неё не записаны: их обходы панель показывает без
    партии, а не выдумывает её.
    """

    __tablename__ = "donor_loads"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: `bulk` — файлом, `manual` — одним доменом вручную (`crawl/loads.py`).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Имя файла партии, как его назвал браузер; у ручной загрузки пусто.
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Кто загрузил — почта: учётку могут удалить, а строка истории останется.
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Поставили ли записанных в обход (галочка загрузки): без неё партия обхода не ждёт.
    crawl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("kind IN ('bulk', 'manual')", name="ck_donor_loads_kind"),
        Index("idx_donor_loads_created_at", "created_at"),
    )


class DonorLoadHostModel(Base):
    """Домен загрузки — как записан в доноры: корень, тем же словом, что у обхода (`host`).

    Именем, а не номером донора: обход и его кандидаты держат донора именем, и партия читается
    с ними одним условием. Пара уникальна: повтор домена в файле отсеивает загрузка.
    """

    __tablename__ = "donor_load_hosts"

    load_id: Mapped[int] = mapped_column(
        ForeignKey("donor_loads.id", ondelete="CASCADE"), primary_key=True
    )
    host: Mapped[str] = mapped_column(String(255), primary_key=True)

    __table_args__ = (Index("idx_donor_load_hosts_host", "host"),)


class CrawlAlwaysSwitchModel(SetByMixin, Base):
    """Обход 24/7 (`features/crawl/always.py`) — идёт ли, порог «мало рекламодателей» и
    «переобход слабых»: строка на каждую смену.

    Действует последняя строка; строк нет — обход 24/7 на паузе, порог и переобход — умолчания
    (`crawl/always_switch.py`). Строка не правится и не удаляется: по строкам видно, кто и когда
    пустил обходчики по сайтам без остановки, кто остановил и кто двигал порог, — как у автокруга.
    """

    __tablename__ = "crawl_always_switches"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Порог «мало рекламодателей», % страниц с платной ссылкой на первых 10 000; шаг — сотая.
    threshold_pct: Mapped[float] = mapped_column(Numeric(5, 2, asdecimal=False), nullable=False)
    #: Переобход даже слабых: помеченные «мало рекламодателей» идут дальше — после сильных.
    recrawl_weak: Mapped[bool] = mapped_column(Boolean, nullable=False)


class CrawlDepthModel(TimestampedMixin, Base):
    """Сколько прошла по донору цепочка обхода 24/7 и откуда её продолжить — строка на донора.

    Часть цепочки — обычная строка обхода (`CrawlRunModel.depth_part`), а здесь — то, что живёт
    дольше частей: открытые цепочкой страницы и очередь ссылок (`checkpoint`, сжато), доля
    продающих страниц после первых 10 000 и пометка «мало рекламодателей», поставленная руками.
    Хост — как его пишет обход (`launch.crawl_host`).
    """

    __tablename__ = "crawl_depths"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    #: Страниц, открытых цепочкой, и законченных частей.
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    parts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: С чего продолжить: чекпоинт обхода (`progress.Checkpoint`) в JSON, сжатый zlib
    #: (`always_depth.pack`); пусто — цепочка ещё ничего не открыла. Отложенная колонка: это
    #: мегабайты, а читает её только начало части (`always_depth.row_of`), не выдача и не экран.
    checkpoint: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    #: Страниц цепочки со ссылкой на «куплено» и их доля от открытых, % — после первых 10 000
    #: или когда сайт кончился раньше; до того доли нет (`gate.judge_run`).
    selling_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yield_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Когда цепочка прошла первые 10 000 страниц (или сайт кончился раньше) и когда кончился сайт.
    first_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: «Мало рекламодателей» руками: `True` — пометил, `False` — снял (идёт при любом пороге);
    #: пусто — решает порог. Кто и когда — рядом: учётку удалили — пометка остаётся без неё.
    weak_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    weak_set_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    weak_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Последняя законченная часть цепочки.
    last_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )


class CrawlAlwaysSignalModel(Base):
    """«Пройдено всё» обхода 24/7 — сказано в Telegram (`crawl/always.py`): строка на эпоху.

    Эпоха — от сигнала до новой работы: уникальный номер держит «один раз» при двух процессах, а
    `reopened_at` — что после сигнала появилась новая работа и следующий сигнал будет уже о ней.
    """

    __tablename__ = "crawl_always_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Что было сказано: сколько сайтов и почему каждый больше не идёт.
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
