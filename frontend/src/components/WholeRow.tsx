/**
 * Строка на всю ширину таблицы — вместо строк, когда их нет: отказ сервера или «пусто
 * и почему». Шапка с фильтрами над ней остаётся: поправить условие можно тут же, не
 * возвращаясь.
 *
 * Жила копиями у доноров, отбора и списка рекламодателей — одна разметка с разными
 * словами, и копии уже начали расходиться.
 */

import { Alert, Button, Group, Stack, Table, Text } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** Почему пусто — словами, и что с этим делать. */
export interface Emptiness {
  title: string;
  detail: string;
  /** Есть ли что сбросить: без фильтров кнопка ничего бы не сделала. */
  resettable: boolean;
  /** Куда идти, чтобы строки появились. */
  path?: { label: string; to: string };
}

export function WholeRow({ span, children }: { span: number; children: ReactNode }) {
  return (
    <Table.Tr className="wholeRow">
      <Table.Td colSpan={span}>{children}</Table.Td>
    </Table.Tr>
  );
}

function EmptyNote({
  empty,
  onReset,
  wide,
}: {
  empty: Emptiness;
  onReset: () => void;
  wide: boolean;
}) {
  return (
    <Stack gap={6} align="flex-start" py="sm">
      <Text size="sm" fw={500}>
        {empty.title}
      </Text>
      <Text size="sm" c="dimmed" maw={wide ? undefined : 720}>
        {empty.detail}
      </Text>
      {empty.path !== undefined || empty.resettable ? (
        <Group gap="sm">
          {empty.path !== undefined ? (
            <Button component={Link} to={empty.path.to} size="compact-sm" className="press">
              {empty.path.label}
            </Button>
          ) : null}
          {empty.resettable ? (
            <Button variant="subtle" size="compact-sm" className="press" onClick={onReset}>
              Сбросить фильтры
            </Button>
          ) : null}
        </Group>
      ) : null}
    </Stack>
  );
}

interface Props {
  /** Колонок в таблице: строка встаёт на всю ширину. */
  span: number;
  /** Отказ сервера словами; при нём строк нет, даже прежних. */
  refusal: string | null;
  /** Заголовок отказа: «Доноры не загрузились». */
  failTitle: string;
  empty: Emptiness | null;
  onReset: () => void;
  /** Объяснение — во всю ширину таблицы, а не строкой до 720 px (так у отбора). */
  wide?: boolean;
}

/** Вместо строк: отказ сервера, без него — «пусто и почему»; строки есть — ничего. */
export function NoRows({ span, refusal, failTitle, empty, onReset, wide = false }: Props) {
  if (refusal !== null) {
    return (
      <WholeRow span={span}>
        <Alert color="red" title={failTitle}>
          {refusal}
        </Alert>
      </WholeRow>
    );
  }
  if (empty === null) return null;
  return (
    <WholeRow span={span}>
      <EmptyNote empty={empty} onReset={onReset} wide={wide} />
    </WholeRow>
  );
}
