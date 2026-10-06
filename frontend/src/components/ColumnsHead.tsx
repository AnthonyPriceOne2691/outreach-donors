/**
 * Ширины и заголовки колонок таблицы — из одного списка.
 *
 * `<colgroup>` и строка заголовков повторяли один и тот же список колонок
 * в каждой таблице, и копии расходились: ширину добавляли в одну, заголовок —
 * в другую. Колонка-флажок слева (`lead`) и колонка действий справа (`tail`)
 * идут без заголовка: их смысл виден по содержимому.
 */

import { Table } from '@mantine/core';

export interface Column {
  title: string;
  /** Ширина фиксированной раскладки; без неё колонка берёт остаток. */
  width?: string;
}

interface Props {
  columns: Column[];
  /** Ширина безымянной колонки слева (флажки отметки). */
  lead?: string | null;
  /** Ширина безымянной колонки справа (кнопки действий). */
  tail?: string | null;
}

export function ColumnsHead({ columns, lead = null, tail = null }: Props) {
  return (
    <>
      <colgroup>
        {lead ? <col style={{ width: lead }} /> : null}
        {columns.map((column) => (
          <col key={column.title} style={column.width ? { width: column.width } : undefined} />
        ))}
        {tail ? <col style={{ width: tail }} /> : null}
      </colgroup>
      <Table.Thead>
        <Table.Tr>
          {lead ? <Table.Th /> : null}
          {columns.map((column) => (
            <Table.Th key={column.title}>{column.title}</Table.Th>
          ))}
          {tail ? <Table.Th /> : null}
        </Table.Tr>
      </Table.Thead>
    </>
  );
}
