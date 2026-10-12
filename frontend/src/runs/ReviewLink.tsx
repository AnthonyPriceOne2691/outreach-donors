/**
 * Вход в рассмотрение прогона — из строки истории и из отчёта прогона одной кнопкой-ссылкой.
 *
 * Ждут решения — кнопка залита: там работа; не ждут — тихая: рассмотрение открыть можно,
 * решать там нечего. Ссылка несёт страницу истории (`from`): «К прогонам» в рассмотрении
 * ведёт на ту же страницу, а не на первую.
 */

import { Button } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import type { RunCard } from '../api/types';

interface Props {
  run: RunCard;
  from: string;
  /** Подпись: у строки истории и у отчёта — своя. */
  children: ReactNode;
}

export function ReviewLink({ run, from, children }: Props) {
  return (
    <Button
      component={Link}
      to={`/runs/${run.id}/review`}
      state={{ from }}
      size="compact-sm"
      variant={(run.queue.pending ?? 0) > 0 ? 'filled' : 'default'}
    >
      {children}
    </Button>
  );
}
