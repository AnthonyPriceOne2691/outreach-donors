/**
 * «Обойти всех по фильтру · N» — кнопкой и окном подтверждения (вкладка «Обход доноров»,
 * 29.10.2026: доноров будут тысячи, и отмечать их по полсотни нечестно).
 *
 * **Число на кнопке — кого можно поставить сейчас**: под условиями вкладки, со свежей ценой, без
 * идущего и ждущего обхода (`startable` сервера). В окне — сколько встанет в очередь и за сколько
 * пройдёт при нынешнем числе обходчиков: тысяча доноров — это сутки работы, и это надо видеть
 * до нажатия. Встанет не больше, чем было на кнопке: под фильтр могло попасть больше, пока
 * человек читал окно (`expected`). Предел за раз — числом сервера в подписи: «10 000 из 12 345»
 * (`UI_RULES`: действие над списком говорит, что именно получится).
 *
 * **Многих сервер ставит фоном** (больше полусотни — `routes.MAX_HOSTS`): тогда уведомление
 * говорит «ставим», а ход виден по счётчику «в очереди».
 */

import { Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { CrawlBulkResult, CrawlDonorQuery } from '../api/crawls';
import { startCrawlsByFilter } from '../api/crawls';
import { formatNumber, plural } from '../format';
import { notify } from '../notices';
import { crawlEta } from './crawlFilters';
import { refreshCrawls } from './crawlQueries';

function donors(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'донор', 'донора', 'доноров')}`;
}

/** Что вышло — одной строкой уведомления. */
export function bulkSummary(done: CrawlBulkResult): string {
  if (done.asked === 0)
    return 'Под фильтром обойти некого: обходы уже идут или ждут, или цены нет.';
  if (done.deferred) {
    return `Ставим в очередь ${donors(done.asked)} — фоном, ход виден по счётчику «в очереди».`;
  }
  const parts = [`Поставлено: ${formatNumber(done.queued ?? 0)} из ${formatNumber(done.asked)}.`];
  if ((done.busy ?? 0) > 0) parts.push(`Уже шли: ${formatNumber(done.busy ?? 0)}.`);
  if ((done.failed ?? 0) > 0) {
    parts.push(`Очередь не ответила: ${formatNumber(done.failed ?? 0)} — попробуйте ещё раз.`);
  }
  return parts.join(' ');
}

/** «12 345» или, сверх предела сервера, «10 000 из 12 345». */
export function bulkCount(startable: number, most: number | undefined): string {
  if (most === undefined || startable <= most) return formatNumber(startable);
  return `${formatNumber(most)} из ${formatNumber(startable)}`;
}

/** Сколько займёт — словами окна. */
export function bulkTiming(count: number, typicalSec: number, workers: number | null): string {
  const eta = crawlEta(count, typicalSec, workers);
  const each = `обход донора — около ${formatNumber(Math.max(1, Math.round(typicalSec / 60)))} мин`;
  if (eta === null) return `Обходчиков сейчас не видно — обходы подождут их в очереди; ${each}.`;
  // «при 1 обходчике», «при 8 обходчиках» — предложный падеж.
  const crawlers = `${formatNumber(workers ?? 0)} ${plural(workers ?? 0, 'обходчике', 'обходчиках', 'обходчиках')}`;
  return `При ${crawlers} пройдут ${eta}: ${each}, разом идёт столько, сколько обходчиков.`;
}

export function BulkCrawl({
  query,
  startable,
  most,
  workers,
  typicalSec,
  conditions,
}: {
  query: CrawlDonorQuery;
  startable: number;
  /** Предел за раз (`bulk_max` сервера); нет — без предела. */
  most: number | undefined;
  workers: number | null;
  typicalSec: number;
  /** Условия вкладки словами; пусто — все доноры. */
  conditions: string[];
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const asked = most === undefined ? startable : Math.min(startable, most);
  const run = useMutation({
    mutationFn: () => startCrawlsByFilter(query, asked),
    onSuccess: async (done) => {
      setOpen(false);
      await refreshCrawls(queryClient);
      notify({ message: bulkSummary(done), color: done.asked > 0 ? 'green' : 'yellow' });
    },
    onError: (failure) =>
      notify({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });
  return (
    <>
      <Button
        variant="light"
        className="press"
        disabled={startable === 0}
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        Обойти всех по фильтру · {bulkCount(startable, most)}
      </Button>
      <Modal opened={open} onClose={() => setOpen(false)} title="Обойти всех по фильтру?">
        <Stack gap="sm">
          <Text>
            Встанут в очередь: {donors(asked)}
            {conditions.length > 0 ? ` — ${conditions.join(', ')}` : ' — все, кого можно обойти'}.
          </Text>
          <Text size="sm" c="dimmed">
            {bulkTiming(asked, typicalSec, workers)}
          </Text>
          <Text size="sm" c="dimmed">
            Ставим только тех, у кого цена в сроке и обход не идёт и не ждёт. Очередь обходов общая:
            обход, поставленный кнопкой после них, подождёт, пока они пройдут. Новые результаты
            заменят прежние, решения по спорным сохранятся.
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button variant="default" className="press" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button className="press" loading={run.isPending} onClick={() => run.mutate()}>
              Поставить {formatNumber(asked)}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
