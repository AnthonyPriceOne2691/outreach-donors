/**
 * Панель лимитов — третья вкладка «Расхода», только у учётки с правом «менять потолки расхода».
 *
 * Требование другой стороны: одна панель на сервис, строки по задачам (Продажи, Аутрич); сервис и
 * действие, лимиты на сессию, день и месяц, стоимость, текущее ограничение, а новый лимит — с
 * просчётом до подтверждения (`LimitChangeModal.tsx`). Вкладки у учётки без права нет, поэтому и
 * запроса к панели нет: компонент монтируется, только когда вкладка открыта (`UsagePage.tsx`).
 *
 * **Экран называет, а не считает**: числа и слова — сервера, чего нет — прочерк, а не ноль: ноль
 * сказал бы «лимит ноль» и «бесплатно». Почему строка не меняется, говорит её текущее ограничение.
 *
 * **Десять колонок на широком окне** — своя плотность (`limitTable`): числа токенов держат колонку,
 * а текущее ограничение берёт остаток. На телефоне строка — карточкой, без прокрутки вбок: короткие
 * значения в ряд, длинные — во всю ширину (`limitWide`). Откуда значение — именем настройки,
 * моноширинным мелким: «меняет администратор сервера вот этим» (правило 12.10.2026).
 *
 * **В ячейке ограничения — первое предложение, целиком — «i» окошком по нажатию** (разведка
 * 10.11.2026: самая длинная простыня сервиса, до девяти строк в ячейке). Сервер пишет ограничение
 * предложениями, итогом вперёд («Своего потолка нет: каждый прогон идёт по смете…»), и соединяет их
 * точкой с пробелом. Первое предложение — законченная мысль: оговорка внутри него не теряется, как
 * потерялась бы, если резать по двоеточию («Потолка нет: платные уровни не зовутся»).
 *
 * **Задачи — свёрнутыми группами с числом** («Продажи · 8»): строка задачи — кнопка, строки под ней
 * — по нажатию. Задач несколько — все свёрнуты; одна — раскрыта: нажатие ради единственной группы
 * ничего бы не прятало. До ответа — заготовка тех же строк, а не крутилка над пустым местом:
 * таблица встаёт сразу, и ответ не сдвигает экран.
 */

import { Alert, Button, Card, Skeleton, Stack, Table, Text } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Fragment, useId, useState } from 'react';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import { fetchLimitPanel } from '../api/limitPanel';
import type { LimitField, LimitRow } from '../api/limitPanel';
import { DataTable } from '../components/DataTable';
import { InfoHint } from '../components/InfoHint';
import { formatNumber, formatUsd } from '../format';
import { LimitChangeModal } from './LimitChangeModal';

const COLUMNS = [
  { title: 'Сервис и действие', width: '10.5rem' },
  { title: 'Охват', width: '6.75rem' },
  { title: 'Сессия', width: '5rem' },
  { title: 'День', width: '6rem' },
  { title: 'Месяц', width: '5rem' },
  { title: 'Выбрано', width: '6.25rem' },
  { title: 'Стоимость', width: '6.25rem' },
  { title: 'Текущее ограничение' },
  { title: 'Откуда значение', width: '6.5rem' },
];
/** Колонка кнопки: «Изменить» целиком. */
const TAIL = '6.25rem';
/** Колонок в таблице, с кнопкой: строка задачи встаёт на всю ширину. */
const SPAN = COLUMNS.length + 1;
/** Уже — таблица уезжает в прокрутку: колонки держат самое длинное число токенов. */
const MIN_WIDTH = 1040;

const NOTHING = '—';
/** Строк заготовки до ответа: столько групп у учётки со всеми задачами, и все свёрнуты. */
const LOADING_ROWS = 2;

/** Лимит числом: деньги — деньгами, остальное — разрядами; чего нет — прочерк. */
function amount(value: number | null, unit: string): string {
  if (value === null) return NOTHING;
  return unit === '$' ? formatUsd(value) : formatNumber(value);
}

/** Лимит сессии: «4 за 1 ч»; сессия не часами (прогон, диалог) — только число, что это — в охвате. */
function perSession(row: LimitRow): string {
  const limit = amount(row.per_session, row.unit);
  if (row.per_session === null || row.session_hours === null) return limit;
  return `${limit} за ${formatNumber(row.session_hours)} ч`;
}

/** Строки «выбрано»: за день, за месяц и деньгами; ничего нет — прочерк. */
function usedLines(row: LimitRow): string[] {
  const lines = [
    row.used_day === null ? null : `день ${amount(row.used_day, row.unit)}`,
    row.used_month === null ? null : `месяц ${amount(row.used_month, row.unit)}`,
    row.spent_month_usd === null ? null : `месяц ${formatUsd(row.spent_month_usd)}`,
  ];
  return lines.filter((line) => line !== null);
}

function Used({ row }: { row: LimitRow }) {
  const lines = usedLines(row);
  if (lines.length === 0) return <Text size="sm">{NOTHING}</Text>;
  return (
    <Stack gap={0}>
      {lines.map((line) => (
        <Text key={line} size="xs">
          {line}
        </Text>
      ))}
    </Stack>
  );
}

/** Стоимость: число и «i» со словами; платят тарифом — прочерк и «i», а не ноль. */
function Cost({ row }: { row: LimitRow }) {
  return (
    <Text component="span" size="sm" className="limitCost">
      {row.cost_usd === null ? NOTHING : formatUsd(row.cost_usd)}
      {row.cost_note !== null && (
        <InfoHint name={`Стоимость: ${row.action}`} width={280}>
          {row.cost_note}
        </InfoHint>
      )}
    </Text>
  );
}

/** Первое предложение ограничения: предложения сервер соединяет точкой с пробелом. */
function headOf(now: string): string {
  return now.split('. ')[0] ?? now;
}

/** Текущее ограничение: первое предложение — в ячейке, целиком — «i» окошком по нажатию (длинное, а
 *  на телефоне наведения нет). Значок держится рядом с последним словом (`limitNow`). */
function Now({ row }: { row: LimitRow }) {
  if (row.now === null) return <Text size="xs">{NOTHING}</Text>;
  const head = headOf(row.now);
  return (
    <Text size="xs" className="limitNow">
      <span>{head}</span>
      {head !== row.now && (
        <InfoHint
          name={`Текущее ограничение: ${row.service}, ${row.action}`}
          width={300}
          interactive
        >
          <Text size="sm">{row.now}</Text>
        </InfoHint>
      )}
    </Text>
  );
}

/** Откуда значение: имя настройки длиннее колонки и переносится по своим частям, а не посреди
 *  слова. */
function Source({ text }: { text: string }) {
  return (
    <Text size="xs" c="dimmed" className="limitSource">
      {text.split('_').map((part, index) => (
        <Fragment key={`${part}-${index}`}>
          {index > 0 && (
            <>
              _<wbr />
            </>
          )}
          {part}
        </Fragment>
      ))}
    </Text>
  );
}

/** Первое поле, которое можно менять: у строк сейчас оно одно. */
function fieldOf(row: LimitRow): LimitField | undefined {
  return row.editable[0];
}

interface RowProps {
  row: LimitRow;
  /** Номер строки для кнопки её задачи (`aria-controls`). */
  id: string;
  onChange: (row: LimitRow) => void;
}

function Row({ row, id, onChange }: RowProps) {
  return (
    <Table.Tr id={id}>
      <Table.Td>
        <Text size="sm" fw={500}>
          {row.unit === '$' ? row.service : `${row.service}, ${row.unit}`}
        </Text>
        <Text size="xs" c="dimmed">
          {row.action}
        </Text>
      </Table.Td>
      <Table.Td data-label="Охват" className="wrapCell limitWide">
        <Text size="xs">{row.scope}</Text>
      </Table.Td>
      <Table.Td data-label="Сессия" className="wrapCell">
        {perSession(row)}
      </Table.Td>
      <Table.Td data-label="День">{amount(row.per_day, row.unit)}</Table.Td>
      <Table.Td data-label="Месяц">{amount(row.per_month, row.unit)}</Table.Td>
      <Table.Td data-label="Выбрано" className="wrapCell limitUsed">
        <Used row={row} />
      </Table.Td>
      <Table.Td data-label="Стоимость">
        <Cost row={row} />
      </Table.Td>
      <Table.Td data-label="Текущее ограничение" className="wrapCell limitWide">
        <Now row={row} />
      </Table.Td>
      <Table.Td data-label="Откуда значение" className="wrapCell limitWide">
        <Source text={row.source} />
      </Table.Td>
      <Table.Td className="cellDecide">
        {fieldOf(row) !== undefined && (
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            aria-haspopup="dialog"
            aria-label={`Изменить: ${row.service}, ${row.action}`}
            onClick={() => onChange(row)}
          >
            Изменить
          </Button>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

interface TaskProps {
  task: string;
  count: number;
  open: boolean;
  /** Номера строк задачи — что раскрывает кнопка. */
  controls: string;
  onToggle: () => void;
}

/** Строка задачи над её строками — кнопкой «Продажи · 8»: строки под ней — по нажатию. Число не
 *  отрывается от подписи (неразрывный пробел). */
function TaskRow({ task, count, open, controls, onToggle }: TaskProps) {
  return (
    <Table.Tr className="tierRow">
      <Table.Td colSpan={SPAN}>
        <Button
          variant="subtle"
          size="compact-sm"
          px={6}
          className="press"
          aria-expanded={open}
          aria-controls={controls}
          leftSection={
            <IconChevronDown
              size={16}
              style={{ transform: open ? 'rotate(180deg)' : 'none' }}
              aria-hidden
            />
          }
          onClick={onToggle}
        >
          {`${task} ·\u00a0${formatNumber(count)}`}
        </Button>
      </Table.Td>
    </Table.Tr>
  );
}

/** Задачи в порядке сервера: он отдаёт строки по задачам. */
function byTask(rows: LimitRow[]): [string, LimitRow[]][] {
  const groups = new Map<string, LimitRow[]>();
  for (const row of rows) groups.set(row.task, [...(groups.get(row.task) ?? []), row]);
  return [...groups];
}

/** Заготовка до ответа — строки задач: такими таблица и встанет, свёрнутой. Читалке — «занято»
 *  у тела таблицы (`stale`), сами полосы ей не нужны. */
function LoadingRows() {
  return Array.from({ length: LOADING_ROWS }, (_, line) => (
    <Table.Tr key={line} className="tierRow" aria-hidden="true">
      <Table.Td colSpan={SPAN}>
        <Skeleton height={26} width="9rem" radius="xl" />
      </Table.Td>
    </Table.Tr>
  ));
}

/** Каркас панели: карточка и таблица — одни у заготовки и у ответа. */
function PanelTable({ loading = false, children }: { loading?: boolean; children: ReactNode }) {
  return (
    <Card className="glass" p="md">
      <DataTable
        columns={COLUMNS}
        minWidth={MIN_WIDTH}
        className="allCentered headsWrap limitTable"
        cardRow
        sticky
        stale={loading}
        tail={TAIL}
        label="Панель лимитов"
      >
        {children}
      </DataTable>
    </Card>
  );
}

export function LimitPanel() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['limit-panel'],
    queryFn: fetchLimitPanel,
  });
  const [changing, setChanging] = useState<LimitRow | null>(null);
  // Раскрытые и свёрнутые нажатием — по задаче; не трогали — по умолчанию (`openAtFirst`).
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const rowId = useId();
  const field = changing === null ? undefined : fieldOf(changing);

  if (isLoading) {
    return (
      <PanelTable loading>
        <LoadingRows />
      </PanelTable>
    );
  }
  if (error) {
    return (
      <Alert color="red" title="Панель лимитов не загрузилась">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined) return null;

  const groups = byTask(data.rows);
  const openAtFirst = groups.length === 1;
  return (
    <>
      <PanelTable>
        {groups.flatMap(([task, rows], group) => {
          const open = toggled[task] ?? openAtFirst;
          const ids = rows.map((_, index) => `${rowId}-${group}-${index}`);
          return [
            <TaskRow
              key={task}
              task={task}
              count={rows.length}
              open={open}
              controls={ids.join(' ')}
              onToggle={() => setToggled((was) => ({ ...was, [task]: !open }))}
            />,
            ...(open
              ? rows.map((row, index) => (
                  <Row key={row.key} row={row} id={ids[index] ?? row.key} onChange={setChanging} />
                ))
              : []),
          ];
        })}
      </PanelTable>
      {changing !== null && field !== undefined && (
        <LimitChangeModal row={changing} field={field} onClose={() => setChanging(null)} />
      )}
    </>
  );
}
