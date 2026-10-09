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
 * Что встаёт вместо строк (`filler`) — пусто или отказ — рисуется под таблицей, а не
 * строкой во всю её ширину: на телефоне таблица шире окна (лиды — 1 224 px), и такая
 * строка уезжала в прокрутку вместе с ней — объяснение читалось обрубком, а кнопка
 * под ним — за краем (аудит экранов 09.10.2026). Шапка с фильтрами при этом остаётся:
 * условие поправляют тут же.
 */

import { Box, Table } from '@mantine/core';
import type { ReactNode } from 'react';

export interface Column {
  title: string;
  /** Пусто — колонка берёт остаток ширины. */
  width?: string;
  /** Колонка с именем не первой — по левому краю, как имя в первой: имена читают по
   *  началу строки. Ячейкам то же выравнивание задаёт строка таблицы (`ta`). */
  start?: boolean;
}

interface Props {
  columns: Column[];
  /** Уже этого таблица не сжимается и уезжает в прокрутку. */
  minWidth: number;
  className: string;
  /** Вторая строка шапки — фильтры под колонками. */
  head?: ReactNode;
  rows: ReactNode;
  /** Вместо строк, под таблицей: пусто или отказ. */
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
    <>
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
              {columns.map(({ title, start }) => (
                <Table.Th key={title} ta={start === true ? 'left' : undefined}>
                  {title}
                </Table.Th>
              ))}
            </Table.Tr>
            {head}
          </Table.Thead>
          <Table.Tbody
            className="staleRows"
            data-stale={stale || undefined}
            aria-busy={stale || undefined}
          >
            {filler === undefined ? rows : null}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {/* Поле — как у ячеек: текст встаёт на кромку первой колонки. */}
      {filler !== undefined && <Box px={horizontalSpacing}>{filler}</Box>}
    </>
  );
}
