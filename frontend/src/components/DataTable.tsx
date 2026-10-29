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
  /** Заголовок вместо слова — по названию колонки: флажок «все на этой странице». */
  heads?: Partial<Record<string, ReactNode>> | undefined;
  /** Шапка закреплена, строки прокручиваются под ней в высоту окна (`stickyTable`): у длинной
   *  страницы заголовки не уезжают за край (вкладка «Обход доноров», 29.10.2026). */
  sticky?: boolean;
  /** Имя таблицы для читалки экрана. */
  label?: string | undefined;
  children: ReactNode;
}

/** Классы прокрутки: карточки на телефоне, короткие значения в строку, закреплённая шапка. */
function scrollClass(cardRow: boolean, sticky: boolean): string {
  return ['scrollSlim phoneCards', cardRow ? 'phoneCardsRow' : '', sticky ? 'stickyTable' : '']
    .filter(Boolean)
    .join(' ');
}

export function DataTable({
  columns,
  minWidth,
  className,
  cardRow = false,
  tail = null,
  head,
  stale = false,
  heads,
  sticky = false,
  label,
  children,
}: Props) {
  return (
    <Table.ScrollContainer
      minWidth={minWidth}
      type="native"
      className={scrollClass(cardRow, sticky)}
    >
      <Table
        className={`dataTable fixedTable ${className ?? ''}`.trim()}
        layout="fixed"
        tabularNums
        verticalSpacing={sticky ? 'xs' : 'sm'}
        horizontalSpacing="md"
        stickyHeader={sticky}
        aria-label={label}
      >
        <ColumnsHead columns={columns} tail={tail} heads={heads}>
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
