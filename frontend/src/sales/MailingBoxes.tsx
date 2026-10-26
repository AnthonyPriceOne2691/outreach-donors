/**
 * «Рассылка»: ящики продаж — ступень разгона сейчас, сколько ушло сегодня, следующая ступень и пишет
 * ли ящик.
 *
 * **Ящики — из прогноза** (`senders[]`): включённые и выключенные, новые появляются сами. Ступень —
 * правилом разгона (день от первого письма ящика), потолок сегодня — с лимитом, сниженным на сутки
 * мягкими отказами; следующая ступень — день и потолок с него, у ящика, ещё не писавшего, — от
 * первого дня отправки. Почему ящик сегодня не берёт первых писем — словами фильтра лимитов.
 *
 * Отсюда — мощность плана: план выше складывает потолки этих ящиков, и ступень, которая растёт
 * завтра, растит и мощность завтрашнего дня. На телефоне ящик — карточкой.
 */

import { Stack, Table, Text } from '@mantine/core';

import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { Seams } from '../components/Seams';
import { formatNumber } from '../format';
import { Block, Cards } from './MailingBlock';
import { boxState, stepNext, stepNow } from './mailingData';
import type { ForecastSender, MailingForecastView } from './mailingTypes';

const COLUMNS: Column[] = [
  { title: 'Ящик' },
  { title: 'Ступень сейчас', width: '17rem' },
  { title: 'Ушло сегодня', width: '8.5rem' },
  { title: 'Следующая ступень', width: '13rem' },
  { title: 'Пишет ли', width: '14rem' },
];
/** Уже — прокрутка: адрес ящика не уже 12rem и четыре колонки. */
export const BOXES_MIN_WIDTH = 1040;

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
      {view.senders.length === 0 ? (
        <Text size="sm" c="dimmed" px="md" className="mailingNoBoxes">
          Ящиков продаж нет — первые письма не уйдут. Ящики заводят на экране «Домены продаж».
        </Text>
      ) : (
        <Cards>
          <DataTable columns={COLUMNS} minWidth={BOXES_MIN_WIDTH} className="mailingBoxes">
            {view.senders.map((box) => (
              <BoxRow key={box.id} box={box} />
            ))}
          </DataTable>
        </Cards>
      )}
    </Block>
  );
}
