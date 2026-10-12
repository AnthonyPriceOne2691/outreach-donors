/**
 * Отсев по порогам в отчёте прогона: каждый порог — со значением этого прогона и числом
 * отсеянных им доменов (ТЗ: «сколько отсеяно и по какому порогу»).
 *
 * **Все пять порогов, и с нулём.** «Реф. домены не отсеяли никого» — тоже ответ: по нему
 * калибруют пороги. **Значения — этого прогона**, а не сегодняшние: пороги версионируются,
 * и DR, поднятый вчера, не должен задним числом объяснять отсев прошлой недели.
 *
 * Незнакомая причина (`other`) — своей строкой, как записана: значит, слова вердикта
 * разошлись с экраном, и это должно быть видно, а не спрятано в сумму.
 */

import { Card, Stack, Table, Text, Title } from '@mantine/core';

import { countryTitle } from '../api/labels';
import type { RejectionCard, RunThresholds } from '../api/runReport';
import { formatNumber, formatShare } from '../format';

/** Порог словами — подписями экрана «Пороги» — и значением этого прогона. */
export function thresholdWords(
  rejection: RejectionCard,
  thresholds: RunThresholds,
  country: string,
): string {
  switch (rejection.threshold) {
    case 'dr':
      return `DR не ниже ${formatNumber(thresholds.min_dr)}`;
    case 'org_traffic':
      return `Органический трафик не ниже ${formatNumber(thresholds.min_org_traffic)} в месяц`;
    case 'keywords':
      return `Ключей в органике не меньше ${formatNumber(thresholds.min_keywords)}`;
    case 'refdomains':
      return `Реф. доменов не меньше ${formatNumber(thresholds.min_refdomains)}`;
    case 'geo':
      return (
        `Регион: ${countryTitle(country)} — в топ-${thresholds.geo_top_n} стран по трафику ` +
        `или от ${formatShare(thresholds.geo_min_share)} трафика`
      );
    case 'other':
      return `Другая причина: «${rejection.said ?? 'без названия'}»`;
  }
}

interface Props {
  rejected: RejectionCard[];
  thresholds: RunThresholds;
  country: string;
}

export function RejectionsCard({ rejected, thresholds, country }: Props) {
  return (
    // Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что у шапки:
    // текст соседних карточек начинается с одной кромки.
    <Card className="glass" p="md">
      <Stack gap={4} px="md" pt="xs">
        <Title order={5}>Отсеяно по порогам</Title>
        <Text size="sm" c="dimmed">
          Пороги этого прогона — версия на момент запуска. Домен проходит, если не ниже каждого.
        </Text>
      </Stack>
      <Table className="dataTable" layout="fixed" verticalSpacing="sm" horizontalSpacing="md">
        <colgroup>
          <col />
          <col style={{ width: '9rem' }} />
        </colgroup>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Порог</Table.Th>
            <Table.Th>Отсеяно доменов</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rejected.map((rejection) => (
            <Table.Tr key={`${rejection.threshold}-${rejection.said ?? ''}`}>
              <Table.Td>{thresholdWords(rejection, thresholds, country)}</Table.Td>
              <Table.Td>{formatNumber(rejection.count)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Card>
  );
}
