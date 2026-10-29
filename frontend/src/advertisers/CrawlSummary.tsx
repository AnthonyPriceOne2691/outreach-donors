/**
 * Обход доноров в «Проверке» — одной строкой (Anthony 29.10.2026).
 *
 * До 29.10.2026 здесь стояла вся панель обхода — с таблицей доноров, раскрытой сама, пока обход
 * идёт. Доноров станет тысячи, и над спорными рекламодателями, ради которых «Проверка», выросла
 * бы простыня. Теперь таблица — на своей вкладке (`CrawlTab`), а здесь — что с обходом сейчас:
 * идёт и ждёт, сколько можно обойти, счёт по статусам — и переход на вкладку. «Цена протухла» —
 * строкой с «Перезапросить цену» и здесь (`StalePrices`): ТЗ велит сначала перезапросить цену,
 * и это действие — часть ежедневного захода, а не только вкладки.
 */

import { Badge, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';

import type { CrawlSummary as Summary } from '../api/crawls';
import { useSession } from '../auth/AuthProvider';
import { InfoHint } from '../components/InfoHint';
import { formatNumber } from '../format';
import { CRAWL_STATUSES, STATUS_KEYS } from './crawlFilters';
import { CRAWL_SUMMARY_KEY, useCrawlSummary } from './crawlQueries';
import { StalePrices } from './StalePrices';

const ABOUT =
  'Этап 2 начинается с обхода доноров: обходим доноров с известной и свежей ценой и ищем, кому ' +
  'они ставят ссылки; кандидаты в рекламодатели считаются сами, когда обход закончится. Доноры, ' +
  'фильтры по статусу и цене, «Обойти отмеченных» и «Обойти всех по фильтру» — на вкладке ' +
  '«Обход доноров».';

/** «не обходили 9 800 · обойдён 40 · не пустил 6» — только ненулевые. */
export function statusLine(summary: Summary): string {
  return STATUS_KEYS.filter((key) => summary.counts.status[key] > 0)
    .map((key) => `${CRAWL_STATUSES[key].title} ${formatNumber(summary.counts.status[key])}`)
    .join(' · ');
}

function Facts({ summary }: { summary: Summary }) {
  const running = summary.counts.status.running;
  const waiting = summary.counts.status.queued;
  return (
    <Group gap="xs" align="center">
      {running > 0 ? (
        <Badge variant="light" color="blue">
          идёт: {formatNumber(running)}
        </Badge>
      ) : null}
      {waiting > 0 ? (
        <Badge variant="light" color="gray">
          в очереди: {formatNumber(waiting)}
        </Badge>
      ) : null}
      <Text size="sm" c="dimmed">
        {summary.startable > 0
          ? `можно обойти: ${formatNumber(summary.startable)}`
          : 'обходить сейчас некого'}
      </Text>
    </Group>
  );
}

export function CrawlSummary({ onOpen }: { onOpen: () => void }) {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const summary = useCrawlSummary();
  const data = summary.data;
  return (
    <Card className="glassPanel crawlPanel" p="xl">
      <Stack gap="sm">
        <Group justify="space-between" align="center" gap="sm">
          <Group gap="sm" align="center">
            <Title order={3}>Обход доноров</Title>
            <InfoHint name="Как устроен обход доноров" width={360}>
              {ABOUT}
            </InfoHint>
            {data ? <Facts summary={data} /> : null}
          </Group>
          <Button variant="light" className="press" onClick={onOpen}>
            Открыть обход доноров
          </Button>
        </Group>
        {data && statusLine(data) !== '' ? (
          <Text size="sm" c="dimmed">
            {statusLine(data)}
          </Text>
        ) : null}
        {data ? (
          <StalePrices
            count={data.counts.price.stale}
            maySend={can('send')}
            onAsked={() => void queryClient.invalidateQueries({ queryKey: CRAWL_SUMMARY_KEY })}
          />
        ) : null}
      </Stack>
    </Card>
  );
}
