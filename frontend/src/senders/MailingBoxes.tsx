/**
 * «Ящики и разгон» этапа: ступень разгона сейчас, сколько ушло сегодня, следующая ступень и пишет ли
 * ящик.
 *
 * **Ящики — из прогноза почты** (`senders[]`): включённые и выключенные, новые появляются сами.
 * Ступень — правилом разгона (день от первого письма ящика), потолок сегодня — с лимитом, сниженным на
 * сутки мягкими отказами; следующая ступень — день и потолок с него, у ящика, ещё не писавшего, — от
 * первого дня отправки. Почему ящик сегодня не берёт первых писем — словами фильтра лимитов.
 *
 * Отсюда — мощность плана рассылки: план складывает потолки этих ящиков, и ступень, которая растёт
 * завтра, растит и мощность завтрашнего дня. На телефоне ящик — карточкой.
 */

import { Stack, Table, Text } from '@mantine/core';

import type { ForecastSender, MailingForecastView } from '../api/senders';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { Block, Cards } from '../components/MailingBlock';
import { Seams } from '../components/Seams';
import { dayLabel, formatNumber } from '../format';

const COLUMNS: Column[] = [
  { title: 'Ящик' },
  { title: 'Ступень сейчас', width: '17rem' },
  { title: 'Ушло сегодня', width: '8.5rem' },
  { title: 'Следующая ступень', width: '13rem' },
  { title: 'Пишет ли', width: '14rem' },
];
/** Уже — прокрутка: адрес ящика не уже 12rem и четыре колонки. */
export const BOXES_MIN_WIDTH = 1040;

/** Ступень разгона ящика сейчас — словами: день разгона и потолок дня из капа ящика. */
function stepNow(box: ForecastSender): string {
  const cap = formatNumber(box.daily_cap);
  if (box.warmup_day === 0) return `без разгона: до ${cap} в день`;
  if (box.warmup_finished) return `разгон окончен: до ${cap} в день`;
  const fresh = box.first_letter_at === null ? ' — ещё не писал, разгон с первого письма' : '';
  return `день ${box.warmup_day}: до ${formatNumber(box.allowance_today)} из ${cap} в день${fresh}`;
}

/** Следующая ступень: день и потолок с него; разгон кончился — так и сказано. */
function stepNext(box: ForecastSender): string {
  if (box.next_step_on !== null && box.next_step_allowance !== null) {
    return `${dayLabel(box.next_step_on)} — до ${formatNumber(box.next_step_allowance)} в день`;
  }
  return box.warmup_finished ? 'выше нет' : '—';
}

/** Пишет ли ящик сегодня — словами: выключен, отказ фильтра лимитов (его словами), сниженный на
 *  сутки лимит. Ничего из этого — «пишет». */
function boxState(box: ForecastSender): string[] {
  if (!box.enabled) return ['выключен — первых писем не берёт'];
  const said: string[] = [];
  if (box.refused !== null) said.push(box.refused);
  if (box.cut) said.push('лимит на сутки снижен мягкими отказами');
  return said.length === 0 ? ['пишет'] : said;
}

/** Сколько первых писем ушло сегодня из потолка дня. */
function sentLine(box: ForecastSender): string {
  return `${formatNumber(box.sent_today)} из ${formatNumber(box.allowance_today)}`;
}

function BoxRow({ box }: { box: ForecastSender }) {
  return (
    <Table.Tr>
      <Table.Td className="cellName">
        <Text size="sm" fw={500}>
          <Seams text={box.email} />
        </Text>
      </Table.Td>
      <Table.Td className="wrapCell" data-label="Ступень сейчас">
        {stepNow(box)}
      </Table.Td>
      <Table.Td data-label="Ушло сегодня">{sentLine(box)}</Table.Td>
      <Table.Td className="wrapCell" data-label="Следующая ступень">
        {stepNext(box)}
      </Table.Td>
      <Table.Td className="wrapCell" data-label="Пишет ли">
        <Stack gap={2}>
          {boxState(box).map((said) => (
            <Text key={said} size="sm">
              {said}
            </Text>
          ))}
        </Stack>
      </Table.Td>
    </Table.Tr>
  );
}

export function MailingBoxes({ view }: { view: MailingForecastView }) {
  return (
    <Block title="Ящики и разгон" className="mailingBoxesBlock">
      <Cards>
        <DataTable columns={COLUMNS} minWidth={BOXES_MIN_WIDTH} className="mailingBoxes">
          {view.senders.map((box) => (
            <BoxRow key={box.id} box={box} />
          ))}
        </DataTable>
      </Cards>
    </Block>
  );
}
