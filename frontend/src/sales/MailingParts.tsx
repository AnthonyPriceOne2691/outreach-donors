/**
 * «Рассылка»: адреса продаж по частям — по гипотезам и все вместе, и почему сборка не напишет и
 * почему сняты — раскрытием под строкой.
 *
 * **Части складываются во «всего» — построением сервера** (`GET /sales/mailing`): каждый лид ровно
 * в одной части, по старшинству. Экран это показывает, а не считает: над таблицей — из чего
 * складывается «всего», в таблице — числа сервера; суммы на экране нет, и разойтись ей не с чем.
 *
 * **Одно число — одна функция.** «В очереди» — тот же счёт, что у пачки и прогноза (`queued_first`),
 * «напишет» — решение сборки, тот же счёт, что запас прогноза (`supply_extra`): числа вкладки и
 * «Очереди писем» не расходятся.
 *
 * **Причины — по стрелке у числа** «не напишет» и «сняты»: список под строкой своей гипотезы
 * (`aria-expanded`), а не столбцом слов в узкой колонке; на виду — пять частых, остальные — «ещё N
 * причин»: у снятых писем причина — слова платформы, и их бывает много. На телефоне строка — карточкой, числа — по
 * три в ряд с подписью (`DataTable`, `phoneCardsRow`), причины — под карточкой во всю ширину.
 *
 * Отказ ручки и крутилка — в блоке режима (`MailingPane`): он первый у этой ручки, и плашка одна.
 */

import { ActionIcon, Button, Group, List, Stack, Table, Text } from '@mantine/core';
import { IconChevronDown, IconChevronUp } from '@tabler/icons-react';
import { useState } from 'react';

import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { WholeRow } from '../components/WholeRow';
import { formatNumber, plural } from '../format';
import { Unfold } from '../runs/Unfold';
import { NameCell } from './FixedTable';
import { Block, Cards } from './MailingBlock';
import type { MailingParts as Parts, MailingWhy, SalesMailingView } from './mailingTypes';

type NumberKey =
  | 'total'
  | 'sent'
  | 'queued'
  | 'will_write'
  | 'wont_write'
  | 'not_ready'
  | 'removed';

/** Части в порядке пути лида: всего, ушло, в очереди, без письма (напишет и не напишет), не
 *  готовы, сняты. Подпись — и заголовок колонки, и подпись числа в карточке телефона. */
const PARTS: { key: NumberKey; title: string; width: string }[] = [
  { key: 'total', title: 'Всего', width: '6.5rem' },
  { key: 'sent', title: 'Ушло', width: '6.5rem' },
  { key: 'queued', title: 'В очереди', width: '7.5rem' },
  { key: 'will_write', title: 'Напишет', width: '7.5rem' },
  { key: 'wont_write', title: 'Не напишет', width: '8.5rem' },
  { key: 'not_ready', title: 'Не готовы', width: '7.5rem' },
  { key: 'removed', title: 'Сняты', width: '7rem' },
];

const COLUMNS: Column[] = [
  { title: 'Гипотеза' },
  ...PARTS.map((part) => ({ title: part.title, width: part.width })),
];

/** Уже — прокрутка: семь колонок чисел (51rem) и имя гипотезы не уже 10rem. */
export const PARTS_MIN_WIDTH = 976;

/** Причины — только у двух частей: у остальных причина одна и названа их подписью. */
type WhyKey = 'wont_write' | 'removed';
const WHY: Record<WhyKey, { list: 'wont_write_why' | 'removed_why'; title: string }> = {
  wont_write: { list: 'wont_write_why', title: 'Почему сборка не напишет' },
  removed: { list: 'removed_why', title: 'Почему сняты' },
};
const WHY_KEYS: WhyKey[] = ['wont_write', 'removed'];

function whyOf(key: NumberKey): WhyKey | null {
  return WHY_KEYS.find((part) => part === key) ?? null;
}

const PARTS_HINT = (
  <Stack gap={6}>
    <span>
      Ушло — первое письмо ушло: дошло, вернулось или уже в отправке; сколько из них ещё в полёте —
      под числом.
    </span>
    <span>В очереди — первое письмо ждёт отправки; тем же счётом, что пачка и срок очереди.</span>
    <span>Напишет — лид готов, письма нет, и сборка его напишет — своим же решением.</span>
    <span>Не напишет — лид готов, письма нет, а сборка ждёт; почему — по стрелке у числа.</span>
    <span>Не готовы — лид ещё на очистке.</span>
    <span>
      Сняты — лид отклонён или его первое письмо снято и заново не пишется; почему — по стрелке.
    </span>
  </Stack>
);

interface Row {
  key: string;
  name: string;
  parts: Parts;
  total: boolean;
}

function rowsOf(view: SalesMailingView): Row[] {
  return [
    ...view.rows.map((row) => ({
      key: String(row.hypothesis_id),
      name: row.name,
      parts: row.parts,
      total: false,
    })),
    { key: 'total', name: 'Все гипотезы', parts: view.total, total: true },
  ];
}

function whose(row: Row): string {
  return row.total ? 'все гипотезы' : `«${row.name}»`;
}

function whyId(row: Row, part: WhyKey): string {
  return `mailing-why-${row.key}-${part}`;
}

interface WhyCellProps {
  row: Row;
  part: WhyKey;
  label: string;
  open: boolean;
  onToggle: () => void;
}

/** Число части с причинами: рядом — стрелка, раскрывающая их под строкой. Число — текстом, как у
 *  соседних частей: на телефоне числа карточки стоят с одной кромки (кнопкой с полями число
 *  уезжало на 10 px, снимок стенда 390 px). */
function WhyCell({ row, part, label, open, onToggle }: WhyCellProps) {
  const count = formatNumber(row.parts[part]);
  if (row.parts[WHY[part].list].length === 0) {
    return <Table.Td data-label={label}>{count}</Table.Td>;
  }
  const Chevron = open ? IconChevronUp : IconChevronDown;
  return (
    <Table.Td data-label={label}>
      <Group justify="center" gap={2} wrap="nowrap">
        <span>{count}</span>
        <ActionIcon
          variant="subtle"
          color="gray"
          radius="xl"
          className="press mailingWhyToggle"
          aria-expanded={open}
          aria-controls={whyId(row, part)}
          aria-label={`${WHY[part].title}: ${whose(row)} — ${count}`}
          onClick={onToggle}
        >
          <Chevron size={16} />
        </ActionIcon>
      </Group>
    </Table.Td>
  );
}

/** Причин на виду — первые по числу; остальные — «ещё N причин». У снятых писем причина — слова
 *  платформы, у каждого письма свои, и список бывает длинным. */
export const WHY_SHOWN = 5;

function Reasons({ found }: { found: MailingWhy[] }) {
  return (
    <List size="sm" spacing={2}>
      {found.map((why) => (
        <List.Item key={why.words}>
          {why.words} — {formatNumber(why.count)}
        </List.Item>
      ))}
    </List>
  );
}

/** Причины части под строкой — словами сервера, частые первыми (порядок сервера); сверх первых
 *  пяти — раскрытием «ещё N причин» (`aria-expanded`), как «Показать ещё» у правок агента. */
function WhyList({ row, part }: { row: Row; part: WhyKey }) {
  const [all, setAll] = useState(false);
  const found = row.parts[WHY[part].list];
  const rest = found.slice(WHY_SHOWN);
  const restId = `${whyId(row, part)}-rest`;
  return (
    <Stack gap={2} id={whyId(row, part)} className="mailingWhy">
      <Text size="sm" fw={500}>
        {WHY[part].title} — {whose(row)}:
      </Text>
      <Reasons found={found.slice(0, WHY_SHOWN)} />
      {rest.length === 0 ? null : (
        <>
          <Unfold open={all}>
            <div id={restId}>
              <Reasons found={rest} />
            </div>
          </Unfold>
          <Group>
            <Button
              variant="subtle"
              size="compact-sm"
              className="press mailingWhyMore"
              aria-expanded={all}
              aria-controls={restId}
              onClick={() => setAll((was) => !was)}
            >
              {all ? 'Свернуть' : `ещё ${formatNumber(rest.length)} ${reasonsWord(rest.length)}`}
            </Button>
          </Group>
        </>
      )}
    </Stack>
  );
}

function reasonsWord(count: number): string {
  return plural(count, 'причина', 'причины', 'причин');
}

/** Под «ушло» — сколько из него ещё в полёте: отправка письмо взяла, исход неизвестен, и воронка
 *  таких пока не считает. Приглушённо и только когда они есть — без своего столбца. */
function InFlight({ parts }: { parts: Parts }) {
  if (parts.in_flight === 0) return null;
  return (
    <Text size="xs" c="dimmed" className="mailingInFlight">
      из них {formatNumber(parts.in_flight)} в полёте — исход ещё неизвестен
    </Text>
  );
}

function PartsRow({ row }: { row: Row }) {
  const [open, setOpen] = useState<ReadonlySet<WhyKey>>(() => new Set());
  const toggle = (part: WhyKey) =>
    setOpen((was) => {
      const next = new Set(was);
      if (!next.delete(part)) next.add(part);
      return next;
    });
  const shown = WHY_KEYS.filter((part) => open.has(part));
  return (
    <>
      <Table.Tr>
        <NameCell name={row.name} total={row.total} />
        {PARTS.map((part) => {
          const why = whyOf(part.key);
          return why === null ? (
            <Table.Td key={part.key} data-label={part.title}>
              {formatNumber(row.parts[part.key])}
              {part.key === 'sent' ? <InFlight parts={row.parts} /> : null}
            </Table.Td>
          ) : (
            <WhyCell
              key={part.key}
              row={row}
              part={why}
              label={part.title}
              open={open.has(why)}
              onToggle={() => toggle(why)}
            />
          );
        })}
      </Table.Tr>
      {shown.length === 0 ? null : (
        <WholeRow span={COLUMNS.length}>
          <Stack gap="xs">
            {shown.map((part) => (
              <WhyList key={part} row={row} part={part} />
            ))}
          </Stack>
        </WholeRow>
      )}
    </>
  );
}

/** Адреса по частям: из чего складывается «всего», таблица по гипотезам и итог. */
export function MailingParts({ view }: { view: SalesMailingView }) {
  return (
    <Block title="Адреса по гипотезам" hint={PARTS_HINT} className="mailingPartsBlock">
      <Text size="sm" px="md" className="mailingPartsSum">
        Всего = ушло + в очереди + без письма (напишет и не напишет) + не готовы + сняты: каждый
        адрес — ровно в одной части.
      </Text>
      <Cards>
        <DataTable columns={COLUMNS} minWidth={PARTS_MIN_WIDTH} className="mailingParts" cardRow>
          {rowsOf(view).map((row) => (
            <PartsRow key={row.key} row={row} />
          ))}
        </DataTable>
      </Cards>
    </Block>
  );
}
