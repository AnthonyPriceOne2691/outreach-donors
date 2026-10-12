"""добивки: сроки [7, 14] становятся [7, 7] — на 7-й и 14-й день от первого письма, как в ТЗ

Revision ID: ac052c21d38b
Revises: 5b1e7c9a3d20
Create Date: 2026-10-12 00:30:00.000000

Сверка ТЗ 12.10.2026: «2 штуки: на 7-й и 14-й день». Срок добивки считается
от предыдущего письма (`letters/chain.py`), а умолчание стояло «7,14» с подписью «дни
от первого письма»: вторая добивка уходила на 21-й день. Умолчание теперь «7,7»
(`config/outreach.py`), а эта ревизия правит то, что прежнее умолчание успело записать.

- Рассылки донорам и рекламодателям со сроками ровно [7, 14] — так их сохранял экран
  сборки, подставляя умолчание, — получают [7, 7]. Пустые сроки остаются пустыми:
  пустое берёт умолчание настроек, то есть уже новое. Продажи не трогаются: свои сроки
  им задаёт модуль продаж, и [7, 14] там было бы выбором, а не прежним умолчанием.
- У первой добивки этих рассылок, уже ушедшей, срок второй посчитан как «отправка
  + 14 суток» — он становится «+ 7 суток». Только там, где срок равен этой формуле
  ровно: перенесённый проходом добивок, погашенный ответом или захватом срок ставило
  не прежнее умолчание, и он не трогается.
- То же со сроком второй добивки у рассылок донорам и рекламодателям без своих сроков
  (решение 12.10.2026): они брали то же умолчание «7,14», и вторая добивка
  у них тоже ушла бы на 21-й день. Пустые сроки лежат тремя видами, и все три значат
  «умолчание» (`letters/chain.cadence`): SQL NULL — у рассылок, заведённых до колонки
  сроков, JSON `null` — так ORM пишет `None` у сборки без сроков, и пустой список.

Сутки — часами, а не днями: срок пишет Python ровными сутками, а `interval '14 days'`
у `timestamptz` считается в поясе сессии, и на переходе времени сутки в нём — 23 или
25 часов; равенство с формулой тогда не сошлось бы.

Переставленный срок может оказаться в прошлом — первая добивка ушла больше недели
назад. Вторую тогда возьмёт ближайший проход добивок, с часовым потолком ящика, как
любую подошедшую (`letters/followups.send_due`).

Откат — обратная правка тех же видов строк: [7, 7] → [7, 14], «+ 7 суток» → «+ 14»,
у рассылок без своих сроков — тоже. Рассылку, заведённую с [7, 7] уже после ревизии,
и срок «+ 7», назначенный рассылке без сроков уже новым умолчанием, откат отличить
не может и тоже возвращает: прежний код это и подставлял умолчанием.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ac052c21d38b"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "5b1e7c9a3d20"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Прежнее и новое умолчание сроков — сутки до первой и до второй добивки.
OLD_DAYS, NEW_DAYS = "[7, 14]", "[7, 7]"
#: Сутки до второй добивки при прежнем и при новом умолчании — в часах (см. выше).
OLD_HOURS, NEW_HOURS = 14 * 24, 7 * 24

#: Срок второй добивки — у ушедшей первой (шаг 1), там, где он равен формуле ровно.
#: Рассылки донорам и рекламодателям — со сроками, которые записало умолчание сборки,
#: и без своих сроков вовсе: SQL NULL, JSON `null` и пустой список (см. выше).
SECOND_DUE = sa.text(
    """
UPDATE messages
SET next_action_at = sent_at + make_interval(hours => :to_hours),
    updated_at = now()
WHERE step = 1
  AND next_action_at = sent_at + make_interval(hours => :from_hours)
  AND campaign_id IN (
      SELECT id FROM campaigns
      WHERE stage IN ('donors', 'advertisers')
        AND (
            followup_days = CAST(:days AS jsonb)
            OR followup_days IS NULL
            OR followup_days IN (CAST('null' AS jsonb), CAST('[]' AS jsonb))
        )
  )
"""
)

#: Сроки самих рассылок — только записанные умолчанием: пустые остаются пустыми.
CADENCE = sa.text(
    """
UPDATE campaigns
SET followup_days = CAST(:to_days AS jsonb),
    updated_at = now()
WHERE followup_days = CAST(:days AS jsonb) AND stage IN ('donors', 'advertisers')
"""
)


def _move(*, days: str, to_days: str, from_hours: int, to_hours: int) -> None:
    """Сроки рассылок `days` → `to_days`, срок второй добивки — у них и у рассылок без
    сроков. Сначала письма: их рассылки узнаются по прежним срокам, и после правки
    рассылок узнать их было бы не по чему."""
    op.execute(SECOND_DUE.bindparams(days=days, from_hours=from_hours, to_hours=to_hours))
    op.execute(CADENCE.bindparams(days=days, to_days=to_days))


def upgrade() -> None:
    """[7, 14] → [7, 7]; срок второй добивки «+ 14 суток» → «+ 7» — и без сроков."""
    _move(days=OLD_DAYS, to_days=NEW_DAYS, from_hours=OLD_HOURS, to_hours=NEW_HOURS)


def downgrade() -> None:
    """Обратно: [7, 7] → [7, 14] и «+ 7 суток» → «+ 14» (см. выше, чего откат не различит)."""
    _move(days=NEW_DAYS, to_days=OLD_DAYS, from_hours=NEW_HOURS, to_hours=OLD_HOURS)
