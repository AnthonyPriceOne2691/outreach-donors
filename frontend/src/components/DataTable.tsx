/**
 * Таблица с фиксированной раскладкой — один каркас: уже `minWidth` уезжает в
 * прокрутку, на телефоне строка — карточкой, ширины и заголовки — из списка колонок
 * (`ColumnsHead`), строки прежнего запроса приглушены, пока едут новые.
 *
 * Каркас повторялся дословно — очередь проверки рекламодателей, их список, приёмка
 * по донорам, очередь «Форм», — а копии одного и того же расходятся на первой правке.
 * Таблицы со своим полем ячейки или без цифр одной ширины — доноры, отбор — держат
 * каркас сами и берут из общего шапку (`ColumnsHead`) и строку на всю ширину
 * (`WholeRow`).
 */

import { Table } from '@mantine/core';
import type { ReactNode } from 'react';

import { ColumnsHead } from './ColumnsHead';
import type { Column } from './ColumnsHead';

interface Props {
  columns: Column[];
  /** Уже этого таблица не сжимается и уезжает в прокрутку. */
  minWidth: number;
  /** Классы таблицы сверх `dataTable fixedTable`: `filteredTable`, `allCentered`. */
  className?: string;
  /** На телефоне короткие значения карточки — в строку (`phoneCardsRow`). */
  cardRow?: boolean;
  /** Ширина безымянной колонки кнопок справа. */
  tail?: string | null;
  /** Строки шапки под заголовками — фильтры под своими колонками. */
  head?: ReactNode;
  /** Строки прежнего запроса ждут замены — приглушены. */
  stale?: boolean;
  children: ReactNode;
}

export function DataTable({
  columns,
  minWidth,
  className,
  cardRow = false,
  tail = null,
  head,
  stale = false,
  children,
}: Props) {
  return (
    <Table.ScrollContainer
      minWidth={minWidth}
      type="native"
      className={cardRow ? 'scrollSlim phoneCards phoneCardsRow' : 'scrollSlim phoneCards'}
    >
      <Table
        className={
          className === undefined ? 'dataTable fixedTable' : `dataTable fixedTable ${className}`
        }
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
        horizontalSpacing="md"
      >
        <ColumnsHead columns={columns} tail={tail}>
          {head}
        </ColumnsHead>
        <Table.Tbody
          className="staleRows"
          data-stale={stale || undefined}
          aria-busy={stale || undefined}
        >
          {children}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
