/**
 * «Переписка» — ссылка с письма на его переписку: там вся история письма, решение по добивке
 * и ответу. Одна у блоков экрана писем («Исход неизвестен», «Доставка откладывается»); у «Ждём
 * ответа» ссылка — домен строки (`children`).
 *
 * Чернилами и с подчёркиванием, как ссылки посреди текста на главной: бирюзовая на светлом месте
 * стекла намерилась 4,23 : 1 (07.10.2026).
 */

import { Anchor } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** Переписки у письма нет — ссылки нет. `children` — слова ссылки; нет — «Переписка». */
export function ThreadLink({
  threadId,
  children = 'Переписка',
}: {
  threadId: number | null;
  children?: ReactNode;
}) {
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
      {children}
    </Anchor>
  );
}
