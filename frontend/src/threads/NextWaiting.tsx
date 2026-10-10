/**
 * «Следующий ждущий →» в шапке диалога (аудит экранов 09.10.2026, второй круг).
 *
 * **Ответы разбирают подряд**: решили цену — открывают следующий ждущий, а не
 * возвращаются к списку и не ищут его глазами (по модели KLM возврат к списку стоит
 * ≈5 с на каждое решение; Gmail после решения сам открывает следующее письмо). Сам
 * диалог после решения не перескакивает: следом часто пишут ответ донору.
 *
 * **Следующий — по порядку списка рядом** (`useSortedThreads`, ждущие сверху) и под
 * тем же фильтром из адреса, что J и K: ждущий ниже открытого; после последнего,
 * а также когда открытый уже решён, — верхний. Других ждущих нет — кнопки нет.
 * Только рядом со списком: на узком окне список — отдельной страницей, и запрос
 * списка ради одной кнопки шёл бы с каждой открытой перепиской.
 */

import { Button } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import type { ThreadCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { readThreadFilters, threadMatches } from './threadFilters';
import { useSortedThreads, waitsForPerson } from './useThreadList';

/** Ждущий человека после открытого — по кругу; `undefined`, если других ждущих нет. */
export function nextWaiting(threads: ThreadCard[], current: number): ThreadCard | undefined {
  const waiting = threads.filter((thread) => waitsForPerson(thread.state));
  const at = waiting.findIndex((thread) => thread.id === current);
  return waiting[at + 1] ?? waiting.find((thread) => thread.id !== current);
}

function NextWaitingLink({ current }: { current: number }) {
  const location = useLocation();
  const [params] = useSearchParams();
  const { can } = useSession();
  const { threads } = useSortedThreads();
  const filters = readThreadFilters(params, can('sales'));
  const next = nextWaiting(
    threads.filter((thread) => threadMatches(thread, filters)),
    current,
  );
  if (next === undefined) return null;
  return (
    <Button
      component={Link}
      to={`/threads/${next.id}${location.search}`}
      title={next.host}
      variant="light"
      // 26 px: шапка растёт на 5 px, а не на 9, и цель нажатия не меньше 24 px (WCAG 2.2).
      size="compact-sm"
      className="press"
      rightSection={<IconArrowRight size={14} />}
    >
      Следующий ждущий
    </Button>
  );
}

export function NextWaiting({ current, split }: { current: number; split: boolean }) {
  return split ? <NextWaitingLink current={current} /> : null;
}
