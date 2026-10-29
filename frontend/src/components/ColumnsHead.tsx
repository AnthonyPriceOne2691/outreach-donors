/**
 * Ширины и заголовки колонок таблицы с фиксированной раскладкой — из одного списка.
 *
 * Правило таблиц сервиса (`docs/UI_RULES.md`, «Таблицы»): ширина колонки задана по
 * самому длинному, что в ней бывает, а раскладка фиксированная — колонки не прыгают
 * от страницы к странице. `<colgroup>` и строка заголовков повторяли один и тот же
 * список колонок в каждой таблице, и копии расходились: ширину добавляли в одну,
 * заголовок — в другую. Поэтому шапка одна на все таблицы, и на простые тоже: до
 * 12.10.2026 у них была своя копия — та же пара без «i», флажков и фильтров.
 *
 * Колонка-флажок слева (`lead`) и колонка действий справа (`tail`) идут без
 * заголовка: их смысл виден по содержимому. Заголовок с «i» (`hint`) — слово
 * и значок одной группой по центру колонки. Заголовок, который не слово, — флажок
 * «все на этой странице» — подставляет сама таблица (`heads`). Строки под
 * заголовками — фильтры, каждый под своей колонкой (`children`).
 */

import { Group, Table } from '@mantine/core';
import type { ReactNode } from 'react';

export interface Column {
  /** Заголовок и ключ колонки: по нему `heads` подставляет флажок вместо слова. */
  title: string;
  /** Ширина фиксированной раскладки; без неё колонка берёт остаток. */
  width?: string;
  /** «i» у заголовка: что значит колонка, когда по ней самой этого не видно. */
  hint?: ReactNode;
}

interface Props {
  columns: readonly Column[];
  /** Ширина безымянной колонки слева (флажки отметки). */
  lead?: string | null;
  /** Ширина безымянной колонки справа (кнопки действий). */
  tail?: string | null;
  /** Заголовок вместо слова — по названию колонки. */
  heads?: Partial<Record<string, ReactNode>> | undefined;
  /** Строки шапки под заголовками — фильтры под своими колонками. */
  children?: ReactNode;
}

function Title({ column }: { column: Column }) {
  if (column.hint === undefined) return <>{column.title}</>;
  return (
    <Group gap={2} justify="center" wrap="nowrap">
      {column.title}
      {column.hint}
    </Group>
  );
}

export function ColumnsHead({ columns, lead = null, tail = null, heads, children }: Props) {
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
            <Table.Th key={column.title}>
              {heads?.[column.title] ?? <Title column={column} />}
            </Table.Th>
          ))}
          {tail ? <Table.Th /> : null}
        </Table.Tr>
        {children}
      </Table.Thead>
    </>
  );
}
