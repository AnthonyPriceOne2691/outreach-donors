/**
 * «Переписка» — ссылка с письма на его переписку: там вся история письма, решение по добивке
 * и ответу. Одна у блоков экрана писем («Исход неизвестен», «Доставка откладывается»).
 *
 * Чернилами и с подчёркиванием, как ссылки посреди текста на главной: бирюзовая на светлом месте
 * стекла намерилась 4,23 : 1 (07.10.2026).
 */

import { Anchor } from '@mantine/core';
import { Link } from 'react-router-dom';

/** Переписки у письма нет — ссылки нет. */
export function ThreadLink({ threadId }: { threadId: number | null }) {
  if (threadId === null) return null;
  return (
    <Anchor
      component={Link}
      to={`/threads/${threadId}`}
      size="sm"
      fw={500}
      c="var(--ink)"
      underline="always"
      className="inkLink"
    >
      Переписка
    </Anchor>
  );
}
