/**
 * «Ждут адреса: N · найти» — общий поиск контактов в шапке таблицы доноров.
 *
 * Раньше это был блок «Контакты» над таблицей: объяснение лестницы, число
 * и кнопка. По замечанию 25.09.2026 адреса живут в карточке донора, а общий
 * поиск сжат до одной строки шапки. Пропасть бесследно он не может: это
 * единственная кнопка, которая ставит поиск всем принятым сразу.
 *
 * **Ждущих нет — строки нет.** Она не отрисовывается, а не прячется
 * атрибутом (`hidden` у компонентов Mantine не прячет, урок 21.09.2026).
 *
 * **Исход задачи — словами.** Поставленную здесь задачу ведёт `JobLine`;
 * упавшую или ждущую повтора прошлую сервер отдаёт сам, и она видна
 * с причиной, а не тишиной.
 */

import { Alert, Button, Group, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import { fetchContactsState, searchContacts } from '../api/contacts';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { JobLine, JobOutcome } from '../jobs/JobLine';

export const CONTACTS_QUERY_KEY = ['contacts-state'] as const;

/** Отчёт прошлого прохода по ступеням: отношение «вошло» к «нашли» на каждой
 *  и есть проверка порядка ступеней, ради которого лестница так устроена. */
function passLine(last: Record<string, unknown> | null): string | null {
  if (last === null) return null;
  const counters = (last.counters ?? {}) as Record<string, number>;
  const walked = formatNumber(Number(last.walked ?? 0));
  const saved = formatNumber(Number(last.saved ?? 0));
  const paid = formatNumber(Number(counters.provider_entered ?? 0));
  const forms = formatNumber(Number(counters.form_only ?? 0));
  return `Прошлый проход: доменов ${walked}, адресов ${saved}, платных запросов ${paid}, форм ${forms}.`;
}

interface PendingContacts {
  /** Строка шапки — `null`, когда ждущих нет. */
  control: ReactNode;
  /** Исход поиска и предупреждения — под шапкой, во всю ширину. */
  outcome: ReactNode;
}

export function usePendingContacts(): PendingContacts {
  const { can } = useSession();
  const queryClient = useQueryClient();
  // Задача, поставленная с этого экрана. Её ведёт `JobLine`; до неё — та,
  // что сервер помнит сам.
  const [jobId, setJobId] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey: CONTACTS_QUERY_KEY,
    queryFn: fetchContactsState,
    // Пока поиск идёт, спрашиваем чаще: человек смотрит на экран именно сейчас.
    refetchInterval: (query) => (query.state.data?.running ? 5_000 : false),
  });

  const start = useMutation({
    mutationFn: () => searchContacts({ limit: 100 }),
    onSuccess: async (queued) => {
      setJobId(queued.job_id);
      await queryClient.invalidateQueries({ queryKey: CONTACTS_QUERY_KEY });
      notifications.show({
        message: `Поиск поставлен в очередь. Ждут адреса: ${formatNumber(queued.pending)}.`,
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });

  // Кончилась задача — перечитать и число ждущих, и таблицу: у доноров
  // появились адреса.
  const finished = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: CONTACTS_QUERY_KEY });
    void queryClient.invalidateQueries({ queryKey: ['donors'] });
  }, [queryClient]);

  if (data === undefined) return { control: null, outcome: null };

  const running = data.running;
  const control =
    data.pending > 0 ? (
      <Group gap={6} wrap="nowrap" className="pendingContacts">
        <Text size="sm">
          ждут адреса: <b>{formatNumber(data.pending)}</b>
        </Text>
        {running && (
          <Text size="sm" c="dimmed">
            · идёт поиск
          </Text>
        )}
        {!running && can('run') && (
          <>
            <Text size="sm" c="dimmed" aria-hidden>
              ·
            </Text>
            <Button
              variant="subtle"
              size="compact-sm"
              className="press"
              aria-label="найти адреса всем ждущим"
              loading={start.isPending}
              onClick={() => start.mutate()}
            >
              найти
            </Button>
          </>
        )}
      </Group>
    ) : null;

  const lines: ReactNode[] = [];
  if (jobId !== null) {
    lines.push(<JobLine key="job" jobId={jobId} onFinished={finished} />);
  } else if (data.job && !SILENT.has(data.job.state)) {
    lines.push(<JobOutcome key="job" job={data.job} />);
  }
  if (running && data.workers === 0) {
    lines.push(
      <Alert key="workers" color="yellow" title="Задачу некому взять">
        Поиск поставлен, но воркеров на очереди нет — он не начнётся, пока их не поднимут.
      </Alert>,
    );
  }
  const last = running ? null : passLine(data.last);
  if (last !== null) {
    lines.push(
      <Text key="last" size="sm" c="dimmed">
        {last}
      </Text>,
    );
  }
  return { control, outcome: lines.length > 0 ? lines : null };
}

/** Исходы прошлой задачи, о которых под шапкой говорить нечего: удачный
 *  рассказан отчётом прохода, идущий — словами «идёт поиск» в самой строке. */
const SILENT: ReadonlySet<string> = new Set(['done', 'running']);
