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
 * **Многих — в очередь загрузки** (больше `launch_max` сервера, полусотни — `routes.MAX_HOSTS`;
 * `crawl/backlog.py`): она отдаёт доноров свободным обходчикам понемногу, и обход, поставленный
 * кнопкой после, идёт первым, а не ждёт за тысячами. Окно говорит, куда встанут, до нажатия.
 */

import { Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import type { CrawlDonorQuery } from '../api/crawls';
import { formatNumber, plural } from '../format';
import { crawlEta } from './crawlFilters';
import { useStartByFilter } from './crawlQueries';

function donors(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'донор', 'донора', 'доноров')}`;
}

/** «12 345» или, сверх предела сервера, «10 000 из 12 345». */
export function bulkCount(startable: number, most: number | undefined): string {
  if (most === undefined || startable <= most) return formatNumber(startable);
  return `${formatNumber(most)} из ${formatNumber(startable)}`;
}

/** Куда встанут: до предела — сразу в очередь обходов, больше — в очередь загрузки. */
export function where(count: number, launchMax: number | undefined): string {
  if (launchMax === undefined) return '';
  if (count <= launchMax) return 'Встанут сразу в очередь обходов, как «Обойти отмеченных».';
  return (
    `Больше ${formatNumber(launchMax)} — через очередь загрузки: обходчикам их отдают по мере, ` +
    'как освобождаются, а обход, поставленный кнопкой, идёт первым; «Снять очередь» в её строке ' +
    'снимает и их.'
  );
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
  launchMax,
  workers,
  typicalSec,
  conditions,
}: {
  query: CrawlDonorQuery;
  startable: number;
  /** Предел за раз (`bulk_max` сервера); нет — без предела. */
  most: number | undefined;
  /** Сколько встаёт сразу в очередь обходов (`launch_max` сервера); больше — в очередь
   *  загрузки. Нет — сервер старше: не сказано. */
  launchMax: number | undefined;
  workers: number | null;
  typicalSec: number;
  /** Условия вкладки словами; пусто — все доноры. */
  conditions: string[];
}) {
  const [open, setOpen] = useState(false);
  const asked = most === undefined ? startable : Math.min(startable, most);
  const run = useStartByFilter(() => setOpen(false));
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
            Ставим только тех, у кого цена в сроке и обход не идёт и не ждёт.{' '}
            {where(asked, launchMax)} Новые результаты заменят прежние, решения по спорным
            сохранятся.
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button variant="default" className="press" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button
              className="press"
              loading={run.isPending}
              onClick={() => run.mutate({ query, expected: asked })}
            >
              Поставить {formatNumber(asked)}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
