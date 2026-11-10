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
 *
 * **Ячейка — коротко** (разведка 10.11.2026: до шести строк в ячейке, и таблица продаж выходила на
 * треть выше донорской). Адрес рвётся по швам — после «@» и перед точкой, — а не посреди слова.
 * «Пишет ли» — значком в два-три слова (`refused_short` сервера), слова фильтра целиком — по нажатию
 * «i»: адрес и счёт в них повторяли первую колонку и «Ушло сегодня». Сниженный на сутки лимит — у
 * счёта: «11 (лимит снижен до 10)», а не «11 из 10», читавшееся перерасходом. Ступень — «день 1 · до
 * 5 из 20» и значок «ещё не писал»; как идёт разгон — в «i» у заголовка.
 *
 * **Колонка, одинаковая во всех строках, — строкой над таблицей**, колонки нет: «разгон окончен · до
 * 20 в день» и «выше нет» в каждой строке читались таблицей ни о чём.
 */

import { Badge, Group, Stack, Table, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { StageBox, StageBoxesView } from '../api/senders';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { InfoHint } from '../components/InfoHint';
import { Seams } from '../components/Seams';
import { dayLabel, formatNumber } from '../format';

/** Неразрывный пробел: число не отрывается от своего слова. */
const NB = '\u00a0';
/** Как идёт разгон — у заголовка «Ступень сейчас» (или у строки над таблицей, если колонки нет). */
const WARMUP_RULE = 'Ступень растёт с днями разгона; первый день — день первого письма ящика';

/** Ящик на разгоне: ступень у него — день и потолок дня; «ещё не писал» — значком рядом. */
function warming(box: StageBox): boolean {
  return box.warmup_day > 0 && !box.warmup_finished;
}

function fresh(box: StageBox): boolean {
  return warming(box) && box.first_letter_at === null;
}

/** Ступень разгона ящика сейчас — коротко: день разгона и потолок дня из капа ящика. */
function stepNow(box: StageBox): string {
  const cap = formatNumber(box.daily_cap);
  if (box.warmup_day === 0) return `без разгона · до${NB}${cap} в${NB}день`;
  if (!warming(box)) return `разгон окончен · до${NB}${cap} в${NB}день`;
  return `день${NB}${box.warmup_day} · до${NB}${formatNumber(box.allowance_today)} из${NB}${cap}`;
}

/** Следующая ступень: день и потолок с него; разгон кончился — так и сказано. */
function stepNext(box: StageBox): string {
  if (box.next_step_on !== null && box.next_step_allowance !== null) {
    return `${dayLabel(box.next_step_on)} — до${NB}${formatNumber(box.next_step_allowance)} в${NB}день`;
  }
  return box.warmup_finished ? 'выше нет' : '—';
}

/** Сколько первых писем ушло сегодня из потолка дня. Сниженный на сутки лимит — словами: «11 из 10»
 *  читалось перерасходом, а это письма, ушедшие до снижения. */
function sentLine(box: StageBox): string {
  const sent = formatNumber(box.sent_today);
  const allowance = formatNumber(box.allowance_today);
  return box.cut ? `${sent} (лимит снижен до${NB}${allowance})` : `${sent} из${NB}${allowance}`;
}

/** Пишет ли — ключ одинаковости колонки: у всех «пишет» — колонки нет. */
function stateKey(box: StageBox): string {
  if (!box.enabled) return 'выключен';
  return box.refused === null ? 'пишет' : (box.refused_short ?? 'не пишет');
}

/** Пишет ли ящик сегодня: выключен, отказ фильтра лимитов — значком, слова фильтра целиком — в «i»;
 *  иначе «пишет». Сниженный лимит здесь не повторяется: он у счёта. */
function BoxState({ box }: { box: StageBox }) {
  if (!box.enabled) {
    return (
      <Badge variant="light" color="gray">
        выключен
      </Badge>
    );
  }
  if (box.refused === null) return <>пишет</>;
  return (
    <Group gap={2} justify="center" wrap="nowrap">
      <Badge variant="light" color="yellow">
        {stateKey(box)}
      </Badge>
      <InfoHint name={`Почему не пишет ${box.email}`} interactive width={320}>
        <Text size="sm">{box.refused}</Text>
      </InfoHint>
    </Group>
  );
}

function StepNow({ box }: { box: StageBox }) {
  return (
    <Group gap={6} justify="center">
      <span>{stepNow(box)}</span>
      {fresh(box) ? (
        <Badge variant="light" color="lagoon" size="sm">
          ещё не писал
        </Badge>
      ) : null}
    </Group>
  );
}

/** Какие колонки рисуются: одинаковые во всех строках уходят строкой над таблицей. */
interface Shown {
  now: boolean;
  next: boolean;
  state: boolean;
}

/** Одинаково ли во всех строках — по ключу; у одной строки одинаковости нет, это просто строка. */
function same(boxes: StageBox[], key: (box: StageBox) => string): boolean {
  const first = boxes[0];
  return boxes.length > 1 && first !== undefined && boxes.every((box) => key(box) === key(first));
}

/** Строка над таблицей вместо одинаковых колонок: ступень, следующая ступень, «пишут все». */
function sameWords(box: StageBox, shown: Shown): string {
  const said: string[] = [];
  if (!shown.now) {
    said.push(`Ступень у всех ящиков: ${stepNow(box)}${fresh(box) ? ', ещё не писали' : ''}.`);
  }
  if (!shown.next) {
    said.push(
      box.next_step_on === null ? 'Следующей ступени нет.' : `Следующая: ${stepNext(box)}.`,
    );
  }
  if (!shown.state) said.push('Пишут все.');
  return said.join(' ');
}

/** Строка — с кромки текста таблицы: на широком окне это поле ячейки, на телефоне строки таблицы —
 *  карточками без полей. */
function SameLine({ box, shown }: { box: StageBox | undefined; shown: Shown }) {
  if (box === undefined || (shown.now && shown.next && shown.state)) return null;
  return (
    <Group gap={4} px={{ base: 0, sm: 'md' }} wrap="nowrap">
      <Text size="sm">{sameWords(box, shown)}</Text>
      {shown.now ? null : <WarmupHint />}
    </Group>
  );
}

function WarmupHint() {
  return <InfoHint name="Как идёт разгон">{WARMUP_RULE}</InfoHint>;
}

interface Part extends Column {
  /** Ширина колонки в rem — она же в счёте ширины, уже которой таблица уезжает в прокрутку. */
  rem: number;
}

/** Адрес ящика — не уже 15rem: по швам он ложится в две строки, а не рвётся посреди слова. */
const ADDRESS_REM = 15;

function columnsOf(shown: Shown): Part[] {
  const hint: ReactNode = <WarmupHint />;
  const parts: (Part | false)[] = [
    // «день 12 · до 15 из 20», значок «ещё не писал» — строкой ниже.
    shown.now && { title: 'Ступень сейчас', width: '12rem', rem: 12, hint },
    // «11 (лимит снижен до 10)» — в одну строку.
    { title: 'Ушло сегодня', width: '13rem', rem: 13 },
    shown.next && { title: 'Следующая ступень', width: '11rem', rem: 11 },
    // Самый длинный значок сервера («направление исчерпано», `limits.SHORT`) и «i» рядом.
    shown.state && { title: 'Пишет ли', width: '14rem', rem: 14 },
  ];
  return parts.filter((part): part is Part => part !== false);
}

function BoxRow({ box, shown }: { box: StageBox; shown: Shown }) {
  return (
    <Table.Tr>
      <Table.Td className="cellName">
        <Text size="sm" fw={500}>
          <Seams text={box.email} address />
        </Text>
      </Table.Td>
      {shown.now ? (
        <Table.Td data-label="Ступень сейчас">
          <StepNow box={box} />
        </Table.Td>
      ) : null}
      <Table.Td className="wrapCell" data-label="Ушло сегодня">
        {sentLine(box)}
      </Table.Td>
      {shown.next ? <Table.Td data-label="Следующая ступень">{stepNext(box)}</Table.Td> : null}
      {shown.state ? (
        <Table.Td className="phoneWide" data-label="Пишет ли">
          <BoxState box={box} />
        </Table.Td>
      ) : null}
    </Table.Tr>
  );
}

export function MailingBoxes({ view }: { view: StageBoxesView }) {
  const boxes = view.senders;
  const first = boxes[0];
  const shown: Shown = {
    now: !same(boxes, (box) => `${stepNow(box)}${fresh(box) ? '+' : ''}`),
    next: !same(boxes, stepNext),
    state: !(same(boxes, stateKey) && first !== undefined && stateKey(first) === 'пишет'),
  };
  const columns = columnsOf(shown);
  const rem = columns.reduce((sum, part) => sum + part.rem, ADDRESS_REM);
  return (
    <Stack gap="sm" className="mailingBoxesBlock">
      <SameLine box={first} shown={shown} />
      <DataTable
        columns={[{ title: 'Ящик' }, ...columns]}
        minWidth={rem * 16}
        className="mailingBoxes"
        label="Ящики и разгон"
        cardRow
      >
        {boxes.map((box) => (
          <BoxRow key={box.id} box={box} shown={shown} />
        ))}
      </DataTable>
    </Stack>
  );
}
