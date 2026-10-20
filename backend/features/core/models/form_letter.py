"""FormLetterModel — текст письма для контактной формы сайта.

У сайта с «только формой» письма не уходят: адреса нет. Пишет человек — через форму
на сайте, а текст берёт тот же, что ушёл бы письмом (`letters/form_letter.py`):
шаблон рассылки доноров, подстановки и переписывание моделью под сайт. Вызов модели
стоит денег, поэтому текст хранится: открыть его второй раз — бесплатно, собрать
заново — по просьбе человека.

Ключ — домен и этап: адрес и форма принадлежат сайту, а письмо — роли, в которой мы
ему пишем (донору — вопрос о цене, рекламодателю — оффер).
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


class FormLetterModel(TimestampedMixin, Base):
    """Письмо для формы одного сайта на одном этапе."""

    __tablename__ = "form_letters"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    #: Этап строкой (`Stage.value`): своего типа перечисления в базе таблице не нужно.
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Доля отличия от шаблона, как у письма в очереди (`letters/uniqueness.py`).
    uniqueness_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Токенов модели на этот текст: расход записан и в журнал расхода.
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Чей текст взят за основу — словами для экрана: рассылка или умолчание.
    based_on: Mapped[str] = mapped_column(String(300), nullable=False)
    #: Почему части текста остались шаблонными (отказ модели и т. п.) — словами.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Кто попросил текст (почта): собрать его стоило вызова модели.
    built_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (UniqueConstraint("domain_id", "stage", name="uq_form_letters_domain_stage"),)
