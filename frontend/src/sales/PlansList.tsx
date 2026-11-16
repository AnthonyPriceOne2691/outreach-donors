/**
 * «Планы работ» — список сохранённых планов: имя, режим, гипотезы по порядку листов, потолок и
 * состояние. Новые сверху.
 *
 * Читается, когда окно открыто, а не вместе с вкладкой «Гипотезы»: вкладка и так читает список
 * гипотез, а гипотезы плана видны в нём сразу после сохранения. Колонка «План работ» в таблице
 * гипотез и кнопки гипотезы плана — следующий срез.
 */

import { Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import type { PlanCard } from '../api/salesPlans';
import { listPlans } from '../api/salesPlans';
import { formatDateTime, formatNumber } from '../format';
import { Pending } from './Pending';
import { dollars, topWords } from './PlanPreviewView';

export const PLANS_QUERY_KEY = ['sales', 'plans'] as const;

const MODES: Record<string, string> = { economy: 'экономный', fast: 'быстрый' };
const STATUSES: Record<string, string> = {
  running: 'идёт',
  paused: 'на паузе',
  done: 'кончен',
};

function PlanBlock({ plan }: { plan: PlanCard }) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        {plan.name} · {MODES[plan.mode] ?? plan.mode} · {STATUSES[plan.status] ?? plan.status} ·
        потолок {dollars(plan.cap_usd)}
      </Text>
      <Text size="xs" c="dimmed">
        Сохранён {formatDateTime(plan.created_at)}
        {plan.created_by ? ` · ${plan.created_by}` : ''}
      </Text>
      {plan.problem ? (
        <Text size="sm" c="red">
          {plan.problem}
        </Text>
      ) : null}
      <Table.ScrollContainer minWidth={560}>
        <Table
          className="dataTable"
          verticalSpacing={4}
          horizontalSpacing="sm"
          fz="sm"
          aria-label={`Гипотезы плана «${plan.name}»`}
        >
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Гипотеза</Table.Th>
              <Table.Th>Цель</Table.Th>
              <Table.Th>Глубина</Table.Th>
              <Table.Th>Ключей</Table.Th>
              <Table.Th>Потолок</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {plan.hypotheses.map((row) => (
              <Table.Tr key={row.id}>
                <Table.Td>{row.name}</Table.Td>
                <Table.Td>{formatNumber(row.target)}</Table.Td>
                <Table.Td>{topWords(row.max_pages)}</Table.Td>
                <Table.Td>{formatNumber(row.keywords)}</Table.Td>
                <Table.Td>{dollars(row.cap_usd)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}

export function PlansList() {
  const plans = useQuery({ queryKey: PLANS_QUERY_KEY, queryFn: listPlans });
  if (plans.data === undefined) return <Pending error={plans.error} />;
  if (plans.data.plans.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Планов ещё нет — загрузите книгу кнопкой «Загрузить план работ».
      </Text>
    );
  }
  return (
    <Stack gap="md">
      {plans.data.plans.map((plan) => (
        <PlanBlock key={plan.id} plan={plan} />
      ))}
    </Stack>
  );
}
