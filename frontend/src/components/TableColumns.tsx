/**
 * Ширины и заголовки колонок таблицы с фиксированной раскладкой — одним местом.
 *
 * Правило таблиц сервиса (`docs/UI_RULES.md`, «Таблицы»): ширина колонки задана
 * по самому длинному, что в ней бывает, а раскладка фиксированная — колонки не
 * прыгают от страницы к странице. Пара «`colgroup` с ширинами + строка заголовков»
 * повторялась в каждой такой таблице дословно; копии расходятся при первой правке.
 */

import { Table } from '@mantine/core';

/** Колонка: заголовок и ширина. Без ширины — колонке остаток таблицы. */
export interface TableColumn {
  title: string;
  width?: string;
}

/** `colgroup` с ширинами и строка заголовков — прямыми детьми `Table`. */
export function TableColumns({ columns }: { columns: readonly TableColumn[] }) {
  return (
    <>
      <colgroup>
        {columns.map((column) => (
          <col key={column.title} style={column.width ? { width: column.width } : undefined} />
        ))}
      </colgroup>
      <Table.Thead>
        <Table.Tr>
          {columns.map((column) => (
            <Table.Th key={column.title}>{column.title}</Table.Th>
          ))}
        </Table.Tr>
      </Table.Thead>
    </>
  );
}
