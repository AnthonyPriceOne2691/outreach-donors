/**
 * Гипотезы — кому и зачем пишем — и сколько у каждой лидов в каждом состоянии.
 *
 * Числа — ссылки на вкладку лидов с тем же фильтром: число на сводке — это
 * вход в список, где с ним работают, и искать рядом фильтр незачем. Ноль —
 * не ссылка: вести в пустую таблицу нечестно.
 *
 * Гипотеза заводится окном «Новая гипотеза» в строке вкладок (`HypothesisModal`) — тем же
 * правилом, что командой консоли, — и **правится тем же окном**: «Править» под именем в строке.
 * Там же — «Удалить гипотезу»: только пустую. Кнопка — в ячейке имени, слева: на телефоне
 * таблица уезжает вбок, а первая колонка видна без прокрутки.
 *
 * Описание — коммерческий текст: живёт данными в базе, а не в репозитории, и в письма не идёт;
 * его читает судья сегмента сбора базы (`SegmentPane`). **В строке — три строки и «показать
 * целиком»** (решение 15.10): описание до 4000 знаков растягивало строку на экран.
 */

import { Anchor, Button, Group, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { HypothesisCard } from '../api/salesTypes';
import { formatDate, formatNumber } from '../format';
import { ClampText } from './ClampText';
import { FixedTable } from './FixedTable';
import type { Column } from './FixedTable';
import { HypothesisModal } from './HypothesisModal';
import { LEAD_STATE_KEYS, leadsLink } from './leadFilters';

const COLUMNS: Column[] = [
  { title: 'Гипотеза', width: '20rem' },
  { title: 'Новые', width: '7rem' },
  { title: 'Готовы', width: '7rem' },
  { title: 'Отклонены', width: '8rem' },
  { title: 'Всего', width: '7rem' },
  { title: 'Заведена', width: '8rem' },
];

export const HYPOTHESES_MIN_WIDTH = 912;
/** Строк описания в свёрнутом виде. */
export const DESCRIPTION_LINES = 3;

function Count({ value, to }: { value: number; to: string }) {
  if (value === 0) {
    return (
      <Text size="sm" c="dimmed">
        0
      </Text>
    );
  }
  // Чернилами с подчёркиванием (`inkLink`), а не бирюзой: бирюзовое число на светлом стекле
  // намерилось 4,25 : 1 при норме 4,5 (замер 09.10.2026) — тот же урок, что у «Переписки»
  // на экране писем 07.10.
  return (
    <Anchor
      component={Link}
      to={to}
      size="sm"
      fw={500}
      c="var(--ink)"
      underline="always"
      className="inkLink"
    >
      {formatNumber(value)}
    </Anchor>
  );
}

function HypothesisRow({
  row,
  onEdit,
}: {
  row: HypothesisCard;
  onEdit: (row: HypothesisCard) => void;
}) {
  return (
    <Table.Tr>
      <Table.Td className="cellName wrapCell">
        <Group gap="xs" align="center" wrap="wrap">
          <Text size="sm" fw={500}>
            {row.name}
          </Text>
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            aria-label={`Править гипотезу «${row.name}»`}
            onClick={() => onEdit(row)}
          >
            Править
          </Button>
        </Group>
        {row.description !== null && (
          <ClampText
            text={row.description}
            lines={DESCRIPTION_LINES}
            className="hypothesisDescription"
            size="xs"
            dimmed
            toggle="link"
          />
        )}
      </Table.Td>
      {LEAD_STATE_KEYS.map((state) => (
        <Table.Td key={state}>
          <Count value={row.leads[state]} to={leadsLink(row.id, state)} />
        </Table.Td>
      ))}
      <Table.Td>
        <Count value={row.total} to={leadsLink(row.id, null)} />
      </Table.Td>
      <Table.Td>
        <Text size="sm">{formatDate(row.created_at)}</Text>
      </Table.Td>
    </Table.Tr>
  );
}

interface Props {
  rows: HypothesisCard[];
  /** Гипотеза удалена: снять её из адреса раздела, если она выбрана. */
  onRemoved?: (id: number) => void;
}

export function HypothesesTable({ rows, onRemoved }: Props) {
  const [editing, setEditing] = useState<HypothesisCard | null>(null);
  if (rows.length === 0) {
    return (
      <Stack gap={6} py="sm">
        <Text size="sm" fw={500}>
          Гипотез пока нет.
        </Text>
        {/* Зовёт к кнопке словами, а не второй кнопкой: «Новая гипотеза» уже стоит в строке
            вкладок — одно действие, одна кнопка. */}
        <Text size="sm" c="dimmed">
          Гипотеза — кому и зачем пишем. Заведите её кнопкой «Новая гипотеза» выше — базу под неё
          грузит мастер загрузки.
        </Text>
      </Stack>
    );
  }
  return (
    <>
      <FixedTable
        columns={COLUMNS}
        minWidth={HYPOTHESES_MIN_WIDTH}
        className="hypothesesTable"
        horizontalSpacing="md"
        tabularNums
        rows={rows.map((row) => (
          <HypothesisRow key={row.id} row={row} onEdit={setEditing} />
        ))}
      />
      {editing !== null && (
        <HypothesisModal
          key={editing.id}
          card={editing}
          onClose={() => setEditing(null)}
          onRemoved={(card) => onRemoved?.(card.id)}
        />
      )}
    </>
  );
}
