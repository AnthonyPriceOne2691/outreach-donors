/**
 * Части очереди рассмотрения, вынесенные из экрана (`RunReviewPage`): разделитель
 * яруса и панель решения по отмеченным (аудит экранов 09.10.2026).
 */

import { Badge, Button, Card, Group, Table, Text } from '@mantine/core';

import { REVIEW_TIERS } from '../api/labels';
import type { ReviewDecision, ReviewTier } from '../api/types';

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
