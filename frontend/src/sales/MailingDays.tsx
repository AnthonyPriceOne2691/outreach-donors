/**
 * «Рассылка»: план на семь дней — мощность ящиков с разгоном, сколько первых писем уйдёт и добивки,
 * — и факт семи прошлых дней. Дни — по UTC, как дневной лимит ящиков.
 *
 * **Числа — прогноза** (`GET /letters/forecast`): мощность дня — правилом экрана «Домены рассылки»
 * с разгоном (ящик, ещё не писавший, начинает его в первый день отправки), «уйдёт» — мощность, но
 * не больше, чем есть что отправить (у продаж — сначала очередь, затем лиды, которым сборка напишет);
 * добивки — условием прохода добивок. Почему в день уйдёт меньше мощности или ничего — словами
 * сервера в той же строке: день без окна получателя — ноль с причиной, а не мощность, которую
 * некому потратить.
 *
 * **Слова этапа — параметром** (`PlanWords`): прогноз у этапов один (`?stage=`), а своё у этапа в
 * подсказке плана — только чьи ящики и что ждёт отправки. Панель рекламодателей берёт план со своими
 * словами, а не копией подсказки: разгон, окно, добивки и дни по UTC живут здесь одним текстом.
 *
 * **План и факт — вкладками, одним блоком** (`MailingDaysTabs`): две таблицы подряд растягивали экран
 * простынёй, а смотрят их по очереди. Первым — план: он отвечает на главный вопрос экрана. Выбор — в
 * состоянии блока, а не в адресе: блок общий у двух экранов со своими адресами, и обновление раз в
 * минуту выбор не сбрасывает. Переключатель стоит вне содержимого вкладок: смена не пересоздаёт его,
 * и клавиатура остаётся на нём.
 *
 * **Сегодня — строкой над планом**: сколько уже ушло, сколько ещё пустят лимиты ящиков и чьи лимиты
 * снижены мягкими отказами. Первая строка плана — тоже сегодня: мощность дня целиком и сколько ещё
 * уйдёт с окном получателя и темпом.
 *
 * На телефоне строка дня — карточкой: день первым, числа — в ряд с подписью, причина — во всю
 * ширину (`DataTable`, `phoneCardsRow`).
 */

import { Alert, Group, SegmentedControl, Stack, Table, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useState } from 'react';

import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { InfoHint } from '../components/InfoHint';
import { formatNumber } from '../format';
import { Cards } from './MailingBlock';
import { dayLabel, PAST_DAYS, PLAN_DAYS } from './mailingData';
import type { ForecastDay, ForecastToday, MailingForecastView } from './mailingTypes';

const PLAN_TITLE = `План на ${PLAN_DAYS} дней`;
const PAST_TITLE = `Факт за ${PAST_DAYS} дней`;

const PLAN_COLUMNS: Column[] = [
  { title: 'День', width: '8rem' },
  { title: 'Мощность ящиков', width: '10rem' },
  { title: 'Уйдёт первых', width: '9rem' },
  { title: 'Добивок', width: '7rem' },
  { title: 'Почему меньше мощности' },
];
/** Уже — прокрутка: четыре колонки чисел и причина не уже 16rem. */
export const PLAN_MIN_WIDTH = 800;

const PAST_COLUMNS: Column[] = [
  { title: 'День', width: '8rem' },
  { title: 'Ушло первых', width: '9rem' },
  { title: 'Добивок', width: '7rem' },
];
export const PAST_MIN_WIDTH = 384;

/** Причина во всю ширину карточки телефона; в таблице сетки нет, и правило ни на что не влияет. */
const WHOLE_LINE = { gridColumn: '1 / -1' } as const;
/** День факта — в одном ряду со своими числами: карточка дня в две строки, а не в три (снимок
 *  стенда 390 px). В таблице сетки нет, и правило ни на что не влияет. */
const IN_ROW = { gridColumn: 'auto' } as const;

/** Слова подсказки плана, свои у этапа. Остальное в подсказке — правила общего прогноза. */
export interface PlanWords {
  /** Чьи ящики: «ящики продаж». */
  boxes: string;
  /** Что ждёт отправки, по порядку: «сначала очередь, затем лиды, которым сборка напишет». */
  supply: string;
}

const SALES_WORDS: PlanWords = {
  boxes: 'ящики продаж',
  supply: 'сначала очередь, затем лиды, которым сборка напишет',
};

function PlanHint({ words }: { words: PlanWords }) {
  return (
    <Stack gap={6}>
      <span>
        Мощность ящиков — сколько первых писем могут отправить {words.boxes} в этот день: ступень
        разгона, лимиты домена и направления; ящик, ещё не писавший, начинает разгон в первый день
        отправки. В день без окна получателя — ноль. Сегодня — мощность дня целиком.
      </span>
      <span>
        Уйдёт первых — мощность, но не больше, чем есть: {words.supply}. Сегодня — сколько ещё
        уйдёт.
      </span>
      <span>
        Добивок — со сроком в этот день; просроченные — сегодня, срок в день без окна — в следующем
        дне отправки. В лимит первых писем добивки не входят.
      </span>
      <span>Дни — по UTC, как дневной лимит ящиков.</span>
    </Stack>
  );
}

const PAST_HINT =
  'Сколько писем ушло на самом деле — первых и добивок, старые дни первыми. Сегодня здесь нет: оно на вкладке плана. Дни — по UTC, как дневной лимит ящиков.';

/** Сегодня: ушло и сколько ещё пустят лимиты ящиков; сниженные лимиты — словами сервера, янтарной
 *  плашкой, как другие «нужно внимание» вкладки: мощность дня меньше обычной, и это не сбой. */
function Today({ today }: { today: ForecastToday }) {
  return (
    <>
      <Text size="sm" px="md" className="mailingToday">
        Сегодня ушло первых — {formatNumber(today.sent_first)}, добивок —{' '}
        {formatNumber(today.sent_followups)}; лимиты ящиков пустят ещё{' '}
        {formatNumber(today.remaining_first)} первых.
      </Text>
      {today.cut === null ? null : (
        <Alert color="yellow" py={6} px="sm" mx="md" className="mailingCut">
          <Text size="sm">{today.cut}</Text>
        </Alert>
      )}
    </>
  );
}

/** Окно получателя и пояс, в котором посчитаны его часы, — словами прогноза. */
function windowLine(view: MailingForecastView): string {
  if (view.window === null) return 'Окна получателя нет — письма уходят в любой час.';
  const zone =
    view.zone === null ? '' : `; часы дня — по поясу ${view.zone}, самому частому в очереди`;
  return `Окно получателя — ${view.window} по его часам${zone}.`;
}

function DayCell({ day, today, inRow = false }: { day: string; today: boolean; inRow?: boolean }) {
  return (
    <Table.Td className="cellName" style={inRow ? IN_ROW : undefined}>
      <Text size="sm" fw={500}>
        {dayLabel(day)}
      </Text>
      {today ? (
        <Text size="xs" c="dimmed">
          сегодня
        </Text>
      ) : null}
    </Table.Td>
  );
}

function PlanRow({ day, today }: { day: ForecastDay; today: boolean }) {
  return (
    <Table.Tr>
      <DayCell day={day.day} today={today} />
      {/* Подпись в карточке телефона — короче заголовка: «Мощность ящиков» вставала в две строки,
          и число под ней садилось ниже соседних. */}
      <Table.Td data-label="Мощность">{formatNumber(day.capacity_first)}</Table.Td>
      <Table.Td data-label="Уйдёт первых">{formatNumber(day.planned_first)}</Table.Td>
      <Table.Td data-label="Добивок">{formatNumber(day.followups_due)}</Table.Td>
      <Table.Td className="wrapCell mailingWhyNot" style={WHOLE_LINE}>
        {day.why_not === null ? (
          <Text size="sm" c="dimmed">
            во всю мощность
          </Text>
        ) : (
          <Text size="sm">{day.why_not}</Text>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

interface PlanProps {
  view: MailingForecastView;
  /** Слова этапа в подсказке; без них — продаж. */
  words?: PlanWords;
}

/** План на семь дней: сегодня, окно получателя и строка на день. */
function PlanDays({ view }: { view: MailingForecastView }) {
  return (
    <>
      <Today today={view.today} />
      <Text size="sm" c="dimmed" px="md" className="mailingWindow">
        {windowLine(view)}
      </Text>
      <Cards>
        <DataTable columns={PLAN_COLUMNS} minWidth={PLAN_MIN_WIDTH} className="mailingPlan" cardRow>
          {view.days.map((day, index) => (
            <PlanRow key={day.day} day={day} today={index === 0} />
          ))}
        </DataTable>
      </Cards>
    </>
  );
}

/** Факт семи прошлых дней: сколько ушло на самом деле — старые первыми, без сегодня. */
function PastDays({ view }: { view: MailingForecastView }) {
  return (
    // Три колонки во всю панель стояли бы на полэкрана друг от друга — таблица по колонкам.
    <Cards width={`${PAST_MIN_WIDTH / 16}rem`}>
      <DataTable columns={PAST_COLUMNS} minWidth={PAST_MIN_WIDTH} className="mailingPast" cardRow>
        {view.past.map((day) => (
          <Table.Tr key={day.day}>
            <DayCell day={day.day} today={false} inRow />
            <Table.Td data-label="Ушло первых">{formatNumber(day.sent_first)}</Table.Td>
            <Table.Td data-label="Добивок">{formatNumber(day.sent_followups)}</Table.Td>
          </Table.Tr>
        ))}
      </DataTable>
    </Cards>
  );
}

const DAYS_TABS = [
  { value: 'plan', label: PLAN_TITLE },
  { value: 'past', label: PAST_TITLE },
];

/** Узкое окно — то же, что у вкладок других экранов. В ряд с «i» на 360 px подпись срезалась. */
const NARROW = '(max-width: 36em)';

/** План и факт — вкладками: заголовок блока — переключатель, «как считается» — у каждой вкладки. */
export function MailingDaysTabs({ view, words = SALES_WORDS }: PlanProps) {
  const narrow = useMediaQuery(NARROW, false, { getInitialValueInEffect: false }) === true;
  const [tab, setTab] = useState<'plan' | 'past'>('plan');
  const plan = tab === 'plan';
  const title = plan ? PLAN_TITLE : PAST_TITLE;
  return (
    <Stack
      gap="sm"
      component="section"
      aria-label={title}
      className={plan ? 'mailingPlanBlock' : 'mailingPastBlock'}
    >
      <Group gap={4} wrap="nowrap" px="md" align="center">
        <SegmentedControl
          aria-label="План или факт по дням"
          value={tab}
          onChange={(picked) => setTab(picked === 'past' ? 'past' : 'plan')}
          data={DAYS_TABS}
          orientation={narrow ? 'vertical' : 'horizontal'}
          fullWidth={narrow}
          style={narrow ? { flex: 1 } : undefined}
        />
        <InfoHint name={`Как считается «${title}»`} width={360}>
          {plan ? <PlanHint words={words} /> : PAST_HINT}
        </InfoHint>
      </Group>
      {plan ? <PlanDays view={view} /> : <PastDays view={view} />}
    </Stack>
  );
}
