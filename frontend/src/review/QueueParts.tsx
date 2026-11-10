/**
 * Части очереди рассмотрения, вынесенные из экрана (`RunReviewPage`): разделитель
 * яруса и панель решения по отмеченным (аудит экранов 09.10.2026), место ряда
 * «Тематика» (разведка 10.11.2026).
 */

import { Badge, Button, Card, Group, Table, Text } from '@mantine/core';
import { useState } from 'react';

import { REVIEW_TIERS } from '../api/labels';
import type { ReviewDecision, ReviewTier } from '../api/types';
import { TopicFilter } from '../topics/TopicFilter';

/** Держать ли место ряда «Тематика», когда фильтровать нечего (пустая «Отклонены — 0»): ряд
 *  пропадал, и всё ниже поднималось на ~50 px. Место держится, как только у этого прогона хоть
 *  одна вкладка показала тематику, — до смены прогона. */
export function useTopicPlace(runId: number | null, filtering: boolean): boolean {
  const [topicRun, setTopicRun] = useState<number | null>(null);
  if (filtering && topicRun !== runId) setTopicRun(runId);
  return !filtering && topicRun !== null && topicRun === runId;
}

/** Место ряда «Тематика» — тем же полем, невидимым и недоступным: высота та же до пикселя. */
export function TopicPlace() {
  return (
    <div style={{ visibility: 'hidden' }} inert aria-hidden="true" data-topic-place>
      <TopicFilter value={null} items={[]} onChange={() => undefined} />
    </div>
  );
}

/** Разделитель яруса над его группой строк: «вероятно донор · 12». */
export function TierRow({ tier, count, span }: { tier: ReviewTier; count: number; span: number }) {
  const title = REVIEW_TIERS[tier];
  return (
    <Table.Tr className="tierRow">
      <Table.Td colSpan={span}>
        <Group gap="xs">
          <Badge variant="light" color={title.color}>
            {title.title}
          </Badge>
          <Text size="xs" c="dimmed">
            {count}
          </Text>
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

interface BarProps {
  count: number;
  /** Вкладка: на «Предложенных» — принять или отклонить, на остальных — вернуть. */
  status: ReviewDecision;
  busy: boolean;
  onDecide: (decision: ReviewDecision) => void;
  onClear: () => void;
}

/** Решение по отмеченным — панелью у нижнего края окна, пока есть отметки
 *  (`bulkBar` закреплена): над таблицей кнопки стояли в трёх тысячах пикселей
 *  от строк, отмеченных внизу очереди. */
export function BulkBar({ count, status, busy, onDecide, onClear }: BarProps) {
  return (
    <Card className="glassSolid bulkBar" p="sm" role="region" aria-label="Решение по отмеченным">
      <Group justify="space-between" gap="sm" wrap="wrap">
        <Text size="sm" fw={500}>
          Выбрано: {count}
        </Text>
        <Group gap="sm">
          {status === 'pending' ? (
            <>
              <Button color="green" loading={busy} onClick={() => onDecide('accepted')}>
                Принять выбранные
              </Button>
              <Button variant="default" loading={busy} onClick={() => onDecide('rejected')}>
                Отклонить выбранные
              </Button>
            </>
          ) : (
            <Button variant="default" loading={busy} onClick={() => onDecide('pending')}>
              Вернуть выбранные
            </Button>
          )}
          <Button variant="subtle" onClick={onClear}>
            Снять отметки
          </Button>
        </Group>
      </Group>
    </Card>
  );
}
