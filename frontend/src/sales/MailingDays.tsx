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
 * **Смена вкладки не двигает страницу** (замечание владельца 10.11.2026: «План ↔ Факт» подбрасывал
 * экран). Обе таблицы стоят в одной ячейке сетки, и блок всегда высотой с бо́льшую: план с причинами
 * — 810 px на 1440 и 1863 на 390, факт — 422 и 547; раньше блок сжимался, браузер поджимал прокрутку
 * к низу страницы, и экран прыгал на разницу. Скрытая вкладка — `visibility: hidden` и `inert`: её не
 * видно, фокус и программа чтения её не находят, а место она держит.
 *
 * **Сегодня — строкой над планом**: сколько уже ушло, сколько ещё пустят лимиты ящиков и чьи лимиты
 * снижены мягкими отказами. Первая строка плана — тоже сегодня: мощность дня целиком и сколько ещё
 * уйдёт с окном получателя и темпом. Окно получателя и пояс — в «i» плана: это «как считается».
 *
 * **Почему меньше мощности — головой причины, целиком — в «i» той же ячейки** (то же замечание:
 * причина сегодняшнего дня вставала абзацем в десять строк — исчерпанные домены и ящики по одному).
 * Голова — слова сервера до первого «: », « — » или «; »; экран причину не сочиняет, а делит.
 * **Одна причина у всех дней** — строкой над таблицей, а колонки нет: семь одинаковых строк читались
 * простынёй (панель рекламодателей с пустой очередью, разведка экранов 10.11.2026).
 *
 * На телефоне строка дня — карточкой: день первым, числа — в ряд с подписью, причина — во всю
 * ширину (`DataTable`, `phoneCardsRow`).
 */

import { Alert, Group, SegmentedControl, Stack, Table, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useState } from 'react';
import type { ReactNode } from 'react';

import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { InfoHint } from '../components/InfoHint';
import { formatNumber } from '../format';
import { Cards } from './MailingBlock';
import { dayLabel, PAST_DAYS, PLAN_DAYS, reasonHead } from './mailingData';
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
/** Без колонки причины — только колонки чисел (34rem). */
const PLAN_NUMBERS_MIN_WIDTH = 544;

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

function PlanHint({ words, window }: { words: PlanWords; window: string }) {
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
        дне отправки. С ящика в сутки уходит не больше потолка добивок: что сверх него — тоже в
        следующем дне отправки. В лимит первых писем добивки не входят.
      </span>
      <span>Дни — по UTC, как дневной лимит ящиков.</span>
      <span>{window}</span>
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

/** Строкой — по ячейке: в таблице по центру, в карточке телефона — с левого края. */
const INLINE = { display: 'inline-flex', maxWidth: '100%' } as const;

/** Почему меньше мощности: голова — на виду, целиком — в «i» с этим именем; короткая — как есть. */
function Reason({ words, name }: { words: string; name: string }) {
  const head = reasonHead(words);
  if (head === words) return <Text size="sm">{words}</Text>;
  return (
    <Group component="span" gap={4} wrap="nowrap" align="flex-start" style={INLINE}>
      <Text span size="sm">
        {head}
      </Text>
      <InfoHint name={name} width={360}>
        {words}
      </InfoHint>
    </Group>
  );
}

/** Причина, одна у всех дней плана; разные или у какого-то дня её нет — `null`. */
export function oneReason(days: readonly ForecastDay[]): string | null {
  const first = days[0]?.why_not ?? null;
  if (first === null) return null;
  return days.every((day) => day.why_not === first) ? first : null;
}

function PlanRow({ day, today, reason }: { day: ForecastDay; today: boolean; reason: boolean }) {
  return (
    <Table.Tr>
      <DayCell day={day.day} today={today} />
      {/* Подпись в карточке телефона — короче заголовка: «Мощность ящиков» вставала в две строки,
          и число под ней садилось ниже соседних. */}
      <Table.Td data-label="Мощность">{formatNumber(day.capacity_first)}</Table.Td>
      <Table.Td data-label="Уйдёт первых">{formatNumber(day.planned_first)}</Table.Td>
      <Table.Td data-label="Добивок">{formatNumber(day.followups_due)}</Table.Td>
      {reason && (
        <Table.Td className="wrapCell mailingWhyNot" style={WHOLE_LINE}>
          {day.why_not === null ? (
            <Text size="sm" c="dimmed">
              во всю мощность
            </Text>
          ) : (
            <Reason words={day.why_not} name={`Почему меньше мощности — ${dayLabel(day.day)}`} />
          )}
        </Table.Td>
      )}
    </Table.Tr>
  );
}

interface PlanProps {
  view: MailingForecastView;
  /** Слова этапа в подсказке; без них — продаж. */
  words?: PlanWords;
}

/** План на семь дней: сегодня и строка на день; окно получателя — в «i» плана; одна причина у
 *  всех дней — строкой над таблицей, без колонки. */
function PlanDays({ view }: { view: MailingForecastView }) {
  const one = oneReason(view.days);
  return (
    <>
      <Today today={view.today} />
      {one === null ? null : (
        <Group gap={4} px="md" wrap="nowrap" align="flex-start" className="mailingWhyAll">
          <Text size="sm">Во все дни меньше мощности:</Text>
          <Reason words={one} name="Почему меньше мощности — во все дни" />
        </Group>
      )}
      <Cards>
        <DataTable
          columns={one === null ? PLAN_COLUMNS : PLAN_COLUMNS.slice(0, -1)}
          minWidth={one === null ? PLAN_MIN_WIDTH : PLAN_NUMBERS_MIN_WIDTH}
          className="mailingPlan"
          cardRow
        >
          {view.days.map((day, index) => (
            <PlanRow key={day.day} day={day} today={index === 0} reason={one === null} />
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

/** Обе вкладки — в одной ячейке сетки: блок высотой с бо́льшую, смена вкладки страницу не двигает. */
const LAYERS = { display: 'grid' } as const;
const LAYER = { gridArea: '1 / 1', minWidth: 0 } as const;
const HIDDEN_LAYER = { ...LAYER, visibility: 'hidden' } as const;

/** Вкладка — областью со своим именем; скрытая держит место, но её нет ни глазу, ни фокусу, ни
 *  программе чтения. */
function Layer({ title, shown, children }: { title: string; shown: boolean; children: ReactNode }) {
  return (
    <Stack
      gap="sm"
      component="section"
      aria-label={title}
      aria-hidden={shown ? undefined : true}
      inert={!shown}
      style={shown ? LAYER : HIDDEN_LAYER}
    >
      {children}
    </Stack>
  );
}

/** План и факт — вкладками: заголовок блока — переключатель, «как считается» — у каждой вкладки. */
export function MailingDaysTabs({ view, words = SALES_WORDS }: PlanProps) {
  const narrow = useMediaQuery(NARROW, false, { getInitialValueInEffect: false }) === true;
  const [tab, setTab] = useState<'plan' | 'past'>('plan');
  const plan = tab === 'plan';
  const title = plan ? PLAN_TITLE : PAST_TITLE;
  return (
    <Stack gap="sm" className={plan ? 'mailingPlanBlock' : 'mailingPastBlock'}>
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
          {plan ? <PlanHint words={words} window={windowLine(view)} /> : PAST_HINT}
        </InfoHint>
      </Group>
      <div style={LAYERS}>
        <Layer title={PLAN_TITLE} shown={plan}>
          <PlanDays view={view} />
        </Layer>
        <Layer title={PAST_TITLE} shown={!plan}>
          <PastDays view={view} />
        </Layer>
      </div>
    </Stack>
  );
}
