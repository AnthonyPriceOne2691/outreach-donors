/**
 * Таблица раздела «Продажи» с фиксированной раскладкой — один каркас у таблиц
 * лидов и гипотез. Три таблицы мастера загрузки (колонки файла, первые лиды,
 * отчёт по строкам) пока держат разметку сами.
 *
 * Ширины колонок заданы по самому длинному содержимому (`colgroup`), строка
 * не переносится, узкому окну — прокрутка: правило отбора и доноров
 * (`glass.css`, «письма, диалоги, списки»). Каркас повторялся в таблицах
 * раздела дословно — копии одного и того же расходятся на первой правке.
 *
 * Строка во всю ширину (`filler`) — пусто или отказ — встаёт вместо строк:
 * фильтры в шапке при этом остаются, условие поправляют тут же.
 */

import { Table } from '@mantine/core';
import type { ReactNode } from 'react';

export interface Column {
  title: string;
  /** Пусто — колонка берёт остаток ширины. */
  width?: string;
}

interface Props {
  columns: Column[];
  /** Уже этого таблица не сжимается и уезжает в прокрутку. */
  minWidth: number;
  className: string;
  /** Вторая строка шапки — фильтры под колонками. */
  head?: ReactNode;
  rows: ReactNode;
  /** Одна строка вместо всех: пусто или отказ. */
  filler?: ReactNode;
  /** Строки прежнего фильтра ждут замены — приглушены. */
  stale?: boolean;
  /** Подпись таблицы для программы чтения с экрана. */
  label?: string;
  verticalSpacing?: 'xs' | 'sm';
  horizontalSpacing?: 'xs' | 'sm' | 'md';
  tabularNums?: boolean;
}

/** Ширины колонок: раскладка `fixed` берёт их из `colgroup`, а не из ячеек. */
function Widths({ columns }: { columns: Column[] }) {
  return (
    <colgroup>
      {columns.map(({ title, width }) => (
        <col key={title} style={width === undefined ? {} : { width }} />
      ))}
    </colgroup>
  );
}

export function FixedTable({
  columns,
  minWidth,
  className,
  head,
  rows,
  filler,
  stale = false,
  label,
  verticalSpacing = 'sm',
  horizontalSpacing = 'xs',
  tabularNums = false,
}: Props) {
  return (
    <Table.ScrollContainer minWidth={minWidth} type="native" className="scrollSlim">
      <Table
        aria-label={label}
        className={`dataTable fixedTable ${className}`}
        layout="fixed"
        verticalSpacing={verticalSpacing}
        horizontalSpacing={horizontalSpacing}
        tabularNums={tabularNums}
      >
        <Widths columns={columns} />
        <Table.Thead>
          <Table.Tr>
            {columns.map(({ title }) => (
              <Table.Th key={title}>{title}</Table.Th>
            ))}
          </Table.Tr>
          {head}
        </Table.Thead>
        <Table.Tbody
          className="staleRows"
          data-stale={stale || undefined}
          aria-busy={stale || undefined}
        >
          {filler === undefined ? (
            rows
          ) : (
            <Table.Tr className="wholeRow">
              <Table.Td colSpan={columns.length}>{filler}</Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
