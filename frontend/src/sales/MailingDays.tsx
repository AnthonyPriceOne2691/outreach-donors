/**
 * «Рассылка»: план на семь дней — мощность ящиков с разгоном, первые письма и добивки: план и, у
 * сегодня, факт рядом, — и факт за семь дней: шесть прошлых и сегодня. Дни — по UTC, как дневной
 * лимит ящиков.
 *
 * **Числа — прогноза** (`GET /letters/forecast`): мощность дня — правилом экрана «Домены рассылки»
 * с разгоном (ящик, ещё не писавший, начинает его в первый день отправки), план первых — мощность,
 * но не больше, чем есть что отправить (у продаж — сначала очередь, затем лиды, которым сборка
 * напишет); добивки — условием прохода добивок. Почему в день уйдёт меньше мощности или ничего —
 * словами сервера в той же строке: день без окна получателя — ноль с причиной, а не мощность,
 * которую некому потратить.
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
 * экран). Обе таблицы стоят в одной ячейке сетки, и блок всегда высотой с бо́льшую (замер 10.11.2026:
 * план с причинами — 810 px на 1440 и 1863 на 390, факт — 422 и 547; с 12.11.2026 карточка дня плана
 * на телефоне выше на ряд — пары «план и факт»); раньше блок сжимался, браузер поджимал прокрутку к
 * низу страницы, и экран прыгал на разницу. Скрытая вкладка — `visibility: hidden` и `inert`: её не
 * видно, фокус и программа чтения её не находят, а место она держит.
 *
 * **План и факт — колонками рядом, у сегодня — оба** (замечание проверки 12.11.2026: «уйдёт первых
 * — план, ушло первых — факт, добивок — план, добивок — факт. А не просто „ушло N писем“… каких
 * ушло? непонятно»). До этого ушедшее за сегодня стояло мелкой строкой над таблицей («Сегодня ушло
 * первых — N, добивок — M…»), и её не заметили, а «Уйдёт первых» у сегодня было остатком дня:
 * рядом с мощностью стояло число меньше её, и что остальное уже ушло, говорила только причина.
 * Прежнее решение экрана (26.10.2026) «Сегодня — строкой над планом» сменено 12.11.2026: у первых
 * писем и у добивок — по колонке плана и факта, слова — те же, что у вкладок. **Сегодня план —
 * день целиком** (`wholeDay`): что уже ушло и что ещё уйдёт, — а факт растёт, пока письма уходят
 * (прогноз перечитывается раз в минуту). Мощность у сегодня — тоже дня целиком, и вечером после
 * закрытия окна получателя: так её отдаёт сервер, экран её не считает и не выбирает (`planNumbers`).
 * У будущих дней факта нет — приглушённый прочерк, а не ноль: ноль читался бы «не ушло ничего».
 * Строка над таблицей осталась про одно — сколько ещё пустят лимиты ящиков (это другое число: без
 * окна получателя и темпа) — и чьи лимиты снижены мягкими отказами. Окно получателя и пояс — в «i»
 * плана: это «как считается».
 *
 * **Факт — с сегодняшним днём, последней строкой** (то же замечание: «Факт за 7 дней — это реальная
 * статистика или устаревшая, за прошлую неделю?» — сегодня в факте не было, и семь прошлых дней
 * читались сводкой прошлой недели). Прежнее решение «факт без сегодня: оно на вкладке плана»
 * (26.10.2026, подсказка факта — 05.11.2026) сменено 12.11.2026: шесть прошлых дней и сегодня с
 * пометкой — всего семь, заголовок вкладки остаётся правдой; сегодняшняя строка растёт, пока
 * письма уходят.
 *
 * **Почему меньше мощности — головой причины, целиком — в «i» той же ячейки** (замечание владельца
 * 10.11.2026: причина сегодняшнего дня вставала абзацем в десять строк — исчерпанные домены и ящики
 * по одному). Голова — слова сервера до первого «: », « — » или «; »; экран причину не сочиняет, а
 * делит. **Одна причина у всех дней** — строкой над таблицей, а колонки нет: семь одинаковых строк
 * читались простынёй (панель рекламодателей с пустой очередью, разведка экранов 10.11.2026).
 *
 * **Пояс на виду — смещением от UTC, имя из базы поясов — в «i»** (проверка прода 10.11.2026: в
 * причине дня стояло имя вида «(пояс Europe/Berlin)»). Сервер называет пояс именем — и в причине
 * дня, и в ответе (`zone`); на виду это имя заменено смещением («UTC+1», `zoneOffset.ts`), а слова
 * сервера целиком, с именем, — в «i» той же ячейки. В подсказке плана — и смещение, и имя.
 *
 * На телефоне строка дня — карточкой (`DataTable`, `phoneCardsRow`). У плана — двумя половинами:
 * день и мощность, под ними план и факт первых, план и факт добивок — план под планом, факт под
 * фактом, — причина во всю ширину (`mailingPlan` в `glass.css`). У факта — день и два числа в ряд.
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
import type {
  ForecastDay,
  ForecastPastDay,
  ForecastToday,
  MailingForecastView,
} from './mailingTypes';
import { offsetWords, zoneOffset } from './zoneOffset';

const PLAN_TITLE = `План на ${PLAN_DAYS} дней`;
const PAST_TITLE = `Факт за ${PAST_DAYS} дней`;

/** Слова «план» и «факт» — те же, что у вкладок блока: одно слово — один смысл.
 *
 *  **Заголовки пар — в две строки** («Первых:» / «план», `headsWrap`), колонка пары — по слову.
 *  Замер 12.11.2026: блок таблицы — 930 px при окне 1280 (1920×1080 с масштабом 150 %), 1016 при
 *  1366, 1090 при 1440; с заголовками в строку таблица была не уже 1 040 px и ехала вбок на 1280
 *  и 1366 — причина уходила за край. Ширины — по замеру шрифтом экрана, поля ячейки — 32 px:
 *  «Первых:» — 59 px из 64, «Добивок:» — 66 из 72, день «пн 30.11» — 60 из 72; «Мощность ящиков»
 *  — в одну строку, 130 из 136 (при 10rem оставалось 128, и с переносом она встала бы в две).
 *  Остальное — причине: в блоке 930 px ей 258 px, и частые причины с «i» стоят в одну строку
 *  («отправлять больше нечего» — 238 px, «домен исчерпан на сегодня» — 247). */
const PLAN_COLUMNS: Column[] = [
  { title: 'День', width: '6.5rem' },
  { title: 'Мощность ящиков', width: '10.5rem' },
  { title: 'Первых: план', width: '6rem' },
  { title: 'Первых: факт', width: '6rem' },
  { title: 'Добивок: план', width: '6.5rem' },
  { title: 'Добивок: факт', width: '6.5rem' },
  { title: 'Почему меньше мощности' },
];
/** Без колонки причины — день и колонки чисел (42rem); в блоке шире таблица растягивается. */
const PLAN_NUMBERS_MIN_WIDTH = 672;
/** Уже — прокрутка: день, пять колонок чисел и причина не уже 13rem — 880 px. В блок 930 px (окно
 *  1280) таблица встаёт с запасом 50 px; на 1366 и 1440 лишнее берёт колонка причины. */
export const PLAN_MIN_WIDTH = PLAN_NUMBERS_MIN_WIDTH + 208;

const PAST_COLUMNS: Column[] = [
  { title: 'День', width: '8rem' },
  { title: 'Ушло первых', width: '9rem' },
  { title: 'Добивок', width: '7rem' },
];
export const PAST_MIN_WIDTH = 384;

/** Причина во всю ширину карточки телефона; в таблице сетки нет, и правило ни на что не влияет. */
const WHOLE_LINE = { gridColumn: '1 / -1' } as const;
/** День — в одном ряду с числами: у факта — со своими двумя (карточка дня в две строки, а не в три,
 *  снимок стенда 390 px), у плана — с мощностью, а пары «план и факт» встают под ними. В таблице
 *  сетки нет, и правило ни на что не влияет. */
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
      <span>Первых: план — мощность, но не больше, чем есть: {words.supply}.</span>
      <span>
        Добивок: план — со сроком в этот день; просроченные — сегодня, срок в день без окна — в
        следующем дне отправки. С ящика в сутки уходит не больше потолка добивок: что сверх него —
        тоже в следующем дне отправки. В лимит первых писем добивки не входят.
      </span>
      <span>
        Сегодня план — день целиком: что уже ушло и что ещё уйдёт. Факт — сколько ушло на самом
        деле: у сегодня он растёт, пока письма уходят (экран обновляется раз в минуту), у будущих
        дней — прочерк.
      </span>
      <span>Дни — по UTC, как дневной лимит ящиков.</span>
      <span>{window}</span>
    </Stack>
  );
}

const PAST_HINT =
  'Сколько писем ушло на самом деле — первых и добивок, старые дни первыми. Последняя строка — сегодня: она растёт, пока письма уходят. Дни — по UTC, как дневной лимит ящиков.';

/** Сегодня над таблицей — сколько первых писем ещё пустят лимиты ящиков: это не план дня из строки
 *  ниже, а лимиты без окна получателя и темпа. Ушедшего здесь нет: оно в колонках факта
 *  (12.11.2026). Сниженные лимиты — словами сервера, янтарной плашкой, как другие «нужно
 *  внимание» вкладки: мощность дня меньше обычной, и это не сбой. */
function Today({ today }: { today: ForecastToday }) {
  return (
    <>
      <Text size="sm" px="md" className="mailingToday">
        {`Сегодня лимиты ящиков пустят ещё ${formatNumber(today.remaining_first)}\u00a0первых.`}
      </Text>
      {today.cut === null ? null : (
        <Alert color="yellow" py={6} px="sm" mx="md" className="mailingCut">
          <Text size="sm">{today.cut}</Text>
        </Alert>
      )}
    </>
  );
}

/** Пояс в подсказке: смещением от UTC и именем из базы поясов в скобках — «UTC+1 (Europe/Berlin)»;
 *  браузер пояса не знает — одним именем. */
function zoneWords(zone: string): string {
  const offset = zoneOffset(zone);
  return offset === null ? zone : `${offset} (${zone})`;
}

/** Окно получателя и пояс, в котором посчитаны его часы, — словами прогноза. */
function windowLine(view: MailingForecastView): string {
  if (view.window === null) return 'Окна получателя нет — письма уходят в любой час.';
  const zone =
    view.zone === null
      ? ''
      : `; часы дня — по поясу ${zoneWords(view.zone)}, самому частому в очереди`;
  return `Окно получателя — ${view.window} по его часам${zone}.`;
}

function DayCell({ day, today }: { day: string; today: boolean }) {
  return (
    <Table.Td className="cellName" style={IN_ROW}>
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

/** Почему меньше мощности: голова — на виду, целиком — в «i» с этим именем; короткая — как есть.
 *  Имя пояса прогноза (`zone`) на виду — смещением от UTC, слова сервера с именем — в том же «i».
 */
function Reason({ words, name, zone }: { words: string; name: string; zone: string | null }) {
  const head = reasonHead(offsetWords(words, zone));
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

/** План сегодняшнего дня целиком: что уже ушло и что ещё уйдёт. Сервер у сегодня отдаёт в плане
 *  остаток дня (`planned_first` — сколько первых ещё уйдёт от сейчас, `followups_due` — сколько
 *  добивок ещё уйдёт сегодня), а рядом с мощностью дня и фактом читается только план дня: остаток
 *  меньше мощности на ушедшее, и день, который идёт во всю мощность, выглядел бы не выбирающим её
 *  (замечание проверки 12.11.2026). Поля ответа не меняются: остаток дня сервер считает сам и по
 *  нему же называет срок очереди. Складывает экран — одним местом для первых и добивок.
 *  Письмо «в полёте» (отдано почте, исход ещё не записан) лимит дня уже занимает, а в факт ещё не
 *  вошло: пока оно летит, план дня здесь меньше мощности на такие письма, хотя причины у дня нет. */
function wholeDay(sent: number, left: number): number {
  return sent + left;
}

/** Числа дня плана: план и факт первых писем и добивок; факта нет (`null`) — день не наступил. */
interface PlanNumbers {
  first: number;
  followups: number;
  sentFirst: number | null;
  sentFollowups: number | null;
}

/** Сегодня — план дня целиком и факт; будущий день — план сервера как есть, факта ещё нет.
 *  Мощность у всех дней — та, что в строке плана (`capacity_first`), и у сегодня тоже: экран её
 *  не считает и не выбирает. Что у сегодня это мощность дня целиком — и вечером, когда окно
 *  получателя уже закрылось, а план и факт дня не нулевые, — решает сервер (12.11.2026): только
 *  он отличает закрывшееся окно от дня, который окну не принадлежит (там ноль, как у других дней
 *  без окна). */
function planNumbers(day: ForecastDay, today: ForecastToday | null): PlanNumbers {
  if (today === null) {
    return {
      first: day.planned_first,
      followups: day.followups_due,
      sentFirst: null,
      sentFollowups: null,
    };
  }
  return {
    first: wholeDay(today.sent_first, day.planned_first),
    followups: wholeDay(today.sent_followups, day.followups_due),
    sentFirst: today.sent_first,
    sentFollowups: today.sent_followups,
  };
}

/** Факт дня плана: у сегодня — число, у дня, который не наступил, — приглушённый прочерк. Не ноль:
 *  ноль читался бы «не ушло ничего». Подпись — для карточки телефона, где шапки таблицы нет. */
function FactCell({ label, sent }: { label: string; sent: number | null }) {
  return (
    <Table.Td data-label={label}>
      {sent === null ? (
        <Text span inherit c="dimmed">
          —
        </Text>
      ) : (
        formatNumber(sent)
      )}
    </Table.Td>
  );
}

interface PlanRowProps {
  day: ForecastDay;
  /** Сегодня у этапа — у первой строки плана; у будущих дней — `null`. */
  today: ForecastToday | null;
  /** Колонка причины есть: причины у дней разные. */
  reason: boolean;
  /** Пояс прогноза — имя, которым сервер называет его в причине дня. */
  zone: string | null;
}

function PlanRow({ day, today, reason, zone }: PlanRowProps) {
  const numbers = planNumbers(day, today);
  return (
    <Table.Tr>
      <DayCell day={day.day} today={today !== null} />
      {/* Подписи в карточке телефона: у мощности — короче заголовка («Мощность ящиков» вставала в
          две строки, и число под ней садилось ниже соседних), у плана и факта — те же слова. */}
      <Table.Td data-label="Мощность">{formatNumber(day.capacity_first)}</Table.Td>
      <Table.Td data-label="Первых: план">{formatNumber(numbers.first)}</Table.Td>
      <FactCell label="Первых: факт" sent={numbers.sentFirst} />
      <Table.Td data-label="Добивок: план">{formatNumber(numbers.followups)}</Table.Td>
      <FactCell label="Добивок: факт" sent={numbers.sentFollowups} />
      {reason && (
        <Table.Td className="wrapCell mailingWhyNot" style={WHOLE_LINE}>
          {day.why_not === null ? (
            <Text size="sm" c="dimmed">
              во всю мощность
            </Text>
          ) : (
            <Reason
              words={day.why_not}
              name={`Почему меньше мощности — ${dayLabel(day.day)}`}
              zone={zone}
            />
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

/** План на семь дней: строка на день, у сегодня (первой) — план дня целиком и факт; над таблицей —
 *  сколько ещё пустят лимиты ящиков; окно получателя — в «i» плана; одна причина у всех дней —
 *  строкой над таблицей, без колонки. */
function PlanDays({ view }: { view: MailingForecastView }) {
  const one = oneReason(view.days);
  return (
    <>
      <Today today={view.today} />
      {one === null ? null : (
        <Group gap={4} px="md" wrap="nowrap" align="flex-start" className="mailingWhyAll">
          <Text size="sm">Во все дни меньше мощности:</Text>
          <Reason words={one} name="Почему меньше мощности — во все дни" zone={view.zone} />
        </Group>
      )}
      <Cards>
        <DataTable
          columns={one === null ? PLAN_COLUMNS : PLAN_COLUMNS.slice(0, -1)}
          minWidth={one === null ? PLAN_MIN_WIDTH : PLAN_NUMBERS_MIN_WIDTH}
          className="mailingPlan headsWrap"
          cardRow
        >
          {view.days.map((day, index) => (
            <PlanRow
              key={day.day}
              day={day}
              today={index === 0 ? view.today : null}
              reason={one === null}
              zone={view.zone}
            />
          ))}
        </DataTable>
      </Cards>
    </>
  );
}

/** День факта: сколько ушло на самом деле; сегодняшний — с пометкой. */
interface FactDay extends ForecastPastDay {
  today: boolean;
}

/** Строки факта — всего `PAST_DAYS`: прошлые дни, старые первыми, и последней — сегодня. Сервер
 *  отдаёт `PAST_DAYS` прошлых дней без сегодня, а сегодняшнее ушедшее — отдельно (`today`), с днём
 *  первой строки плана: старший из прошлых уступает место сегодня, и «за 7 дней» остаётся правдой.
 *  Плана нет (сервер не назвал сегодняшний день) — строки сегодня нет: дату экран не выдумывает. */
function factDays(view: MailingForecastView): FactDay[] {
  const today = view.days[0];
  if (today === undefined) return view.past.map((day) => ({ ...day, today: false }));
  return [
    ...view.past.slice(-(PAST_DAYS - 1)).map((day) => ({ ...day, today: false })),
    {
      day: today.day,
      sent_first: view.today.sent_first,
      sent_followups: view.today.sent_followups,
      today: true,
    },
  ];
}

/** Факт за семь дней: сколько ушло на самом деле — шесть прошлых дней, старые первыми, и сегодня
 *  последней строкой: она растёт, пока письма уходят. */
function PastDays({ view }: { view: MailingForecastView }) {
  return (
    // Три колонки во всю панель стояли бы на полэкрана друг от друга — таблица по колонкам.
    <Cards width={`${PAST_MIN_WIDTH / 16}rem`}>
      <DataTable columns={PAST_COLUMNS} minWidth={PAST_MIN_WIDTH} className="mailingPast" cardRow>
        {factDays(view).map((day) => (
          <Table.Tr key={day.day}>
            <DayCell day={day.day} today={day.today} />
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
