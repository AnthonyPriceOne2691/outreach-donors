/**
 * Вход в резерв прогона — из его отчёта и очереди: не прошедшие не пропали, из резерва их берут
 * на рассмотрение при нехватке (20.10.2026). Чернилами и подчёркнута всегда: бирюзовая ссылка в
 * шапке очереди на свету намерилась 4,29 : 1 при норме 4,5, чернилами — 7,59.
 */

import { Anchor } from '@mantine/core';
import { Link } from 'react-router-dom';

export function ReserveLink({ runId, children }: { runId: number; children: string }) {
  return (
    <Anchor
      component={Link}
      to={`/donors/reserve?run=${runId}`}
      size="sm"
      c="var(--ink)"
      underline="always"
    >
      {children}
    </Anchor>
  );
}
