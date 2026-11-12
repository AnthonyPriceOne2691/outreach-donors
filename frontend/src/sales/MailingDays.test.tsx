/**
 * План и факт «Рассылки» — вкладками одного блока (`MailingDaysTabs`) и слова этапа параметром
 * (`PlanWords`): у продаж текст подсказки тот же, что был до параметра, у другого этапа — свои ящики
 * и что ждёт отправки, а разгон, окно, добивки и дни по UTC — общие правила прогноза, одним текстом.
 *
 * Смена вкладки страницу не двигает (замечание владельца 10.11.2026): обе таблицы в одной ячейке
 * сетки, скрытая держит место. Высоты в jsdom нет — проверяется устройство: обе вкладки в ячейке,
 * скрытая — `visibility: hidden`, `inert` и `aria-hidden`; сам прыжок мерит снимок стенда.
 *
 * План и факт — колонками рядом (замечание проверки 12.11.2026): у сегодня план — день целиком, а
 * не остаток дня, факт — ушедшее; у будущих дней факт — прочерк; в факте за семь дней последней
 * строкой — сегодня. Причина дня в записанном прогнозе — прежними словами сервера («сегодня уже
 * ушло…»): экран слов сервера не разбирает, и что они сменились, ему всё равно.
 *
 * Часы закреплены на «сейчас» записанного прогноза (`vi.useFakeTimers({ toFake: ['Date'] })`): пояс
 * на виду — смещением от UTC на сегодня, а оно у пояса с летним временем зависит от дня.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { MailingDaysTabs, oneReason } from './MailingDays';
import { reasonHead } from './mailingData';
import { FORECAST, MAILING_NOW, NOTHING_LEFT } from './mailingFixtures';
import type { MailingForecastView } from './mailingTypes';

const PLAN = 'План на 7 дней';
const PAST = 'Факт за 7 дней';
const PLAN_HINT = `Как считается «${PLAN}»`;
/** Окно получателя записанного прогноза — абзацем подсказки плана, а не строкой над таблицей. В
 *  подсказке пояс — и смещением, и именем из базы поясов: 2 ноября в Торонто зимнее время. */
const WINDOW =
  'Окно получателя — пн–пт 09:00–17:00 по его часам; часы дня — по поясу UTC−5 (America/Toronto), самому частому в очереди.';

/** Причина сегодняшнего дня — словами фильтра лимитов, как сервер называет её с 12.11.2026: что
 *  мешает дню выбрать мощность, а не сколько уже ушло. */
const HINDERED_WORDS = 'домен исчерпан на сегодня: a.example.test — 10 из 10';
/** Записанный прогноз, в котором сегодня что-то мешает: ушло 6, ещё уйдёт 14 — при мощности 30. */
const HINDERED: MailingForecastView = {
  ...FORECAST,
  days: FORECAST.days.map((day, index) =>
    index === 0 ? { ...day, planned_first: 14, why_not: HINDERED_WORDS } : day,
  ),
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(MAILING_NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

/** Подсказка плана словами этапа — по абзацам; своё у этапа только в двух местах. */
function hint(boxes: string, supply: string): string[] {
  return [
    `Мощность ящиков — сколько первых писем могут отправить ${boxes} в этот день: ступень разгона, лимиты домена и направления; ящик, ещё не писавший, начинает разгон в первый день отправки. В день без окна получателя — ноль. Сегодня — мощность дня целиком.`,
    `Первых: план — мощность, но не больше, чем есть: ${supply}.`,
    'Добивок: план — со сроком в этот день; просроченные — сегодня, срок в день без окна — в следующем дне отправки. С ящика в сутки уходит не больше потолка добивок: что сверх него — тоже в следующем дне отправки. В лимит первых писем добивки не входят.',
    'Сегодня план — день целиком: что уже ушло и что ещё уйдёт. Факт — сколько ушло на самом деле: у сегодня он растёт, пока письма уходят (экран обновляется раз в минуту), у будущих дней — прочерк.',
    'Дни — по UTC, как дневной лимит ящиков.',
    WINDOW,
  ];
}

/** Вкладка по имени — и скрытая: она в документе, только её нет ни глазу, ни фокусу. Ролью её не
 *  найти: у скрытой области имени для программы чтения нет — так и задумано. */
function layer(name: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`section[aria-label="${name}"]`);
  if (found === null) throw new Error(`вкладки «${name}» нет`);
  return found;
}

/** Скрытая вкладка держит место, но её не видно, не достать фокусом и не прочесть. */
function expectHidden(element: HTMLElement): void {
  expect(element).toHaveStyle({ visibility: 'hidden' });
  expect(element).toHaveAttribute('inert');
  expect(element).toHaveAttribute('aria-hidden', 'true');
}

function expectShown(element: HTMLElement): void {
  expect(element).not.toHaveStyle({ visibility: 'hidden' });
  expect(element).not.toHaveAttribute('inert');
  expect(element).not.toHaveAttribute('aria-hidden');
}

/** Строка таблицы по первой ячейке — дню. */
function rowOf(where: HTMLElement, day: string): HTMLElement {
  const row = within(where)
    .getAllByRole('row')
    .find((found) => found.querySelector('td')?.textContent?.startsWith(day));
  if (row === undefined) throw new Error(`строки «${day}» нет`);
  return row;
}

function cells(row: HTMLElement): string[] {
  return [...row.querySelectorAll('td')].map((cell) => cell.textContent ?? '');
}

/** Строки таблицы без шапки — ячейками. */
function body(where: HTMLElement): string[][] {
  return within(where)
    .getAllByRole('row')
    .slice(1)
    .map((row) => cells(row));
}

/** Что читает человек: наводит на «i» у вкладок плана — абзацы подсказки по порядку. */
async function shownHint(): Promise<string[]> {
  await userEvent.setup().hover(screen.getByRole('button', { name: PLAN_HINT }));
  const first = await screen.findByText(/^Мощность ящиков — /);
  const paragraphs = first.parentElement;
  if (paragraphs === null) throw new Error('у абзаца подсказки нет обёртки');
  return Array.from(paragraphs.children, (paragraph) => paragraph.textContent ?? '');
}

describe('рассылка: план и факт — вкладками', () => {
  it('сначала план: строка лимитов сегодня и семь дней; факта на экране нет', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);

    expect(screen.getByRole('radio', { name: PLAN })).toBeChecked();
    const plan = within(screen.getByRole('region', { name: PLAN }));
    expect(plan.getByText(/^Сегодня лимиты ящиков пустят ещё/)).toBeInTheDocument();
    // Окно получателя — «как считается»: абзацем в «i», а не строкой над таблицей.
    expect(screen.queryByText(/^Окно получателя/)).toBeNull();
    expect(plan.getAllByRole('row')).toHaveLength(8);
    expect(screen.queryByRole('region', { name: PAST })).toBeNull();
    // Заголовка рядом с переключателем нет: то же слово дважды — лишняя строка, а блок ради краткости.
    expect(screen.queryByRole('heading')).toBeNull();
  });

  it('факт — по нажатию: его таблица вместо плана, «как считается» — своё; план возвращается', async () => {
    const user = userEvent.setup();
    renderWith(<MailingDaysTabs view={FORECAST} />);

    await user.click(screen.getByRole('radio', { name: PAST }));

    expect(screen.getByRole('radio', { name: PAST })).toBeChecked();
    const past = within(screen.getByRole('region', { name: PAST }));
    expect(past.getAllByRole('row')).toHaveLength(8);
    // Строка над таблицей — про лимиты плана: у факта её нет.
    expect(past.queryByText(/^Сегодня лимиты ящиков/)).toBeNull();
    expect(screen.queryByRole('region', { name: PLAN })).toBeNull();
    expect(screen.queryByRole('button', { name: PLAN_HINT })).toBeNull();
    await user.hover(screen.getByRole('button', { name: `Как считается «${PAST}»` }));
    expect((await screen.findByText(/^Сколько писем ушло на самом деле/)).textContent).toBe(
      'Сколько писем ушло на самом деле — первых и добивок, старые дни первыми. Последняя строка — сегодня: она растёт, пока письма уходят. Дни — по UTC, как дневной лимит ящиков.',
    );

    await user.click(screen.getByRole('radio', { name: PLAN }));

    expect(screen.getByRole('region', { name: PLAN })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: PAST })).toBeNull();
    expect(screen.getByRole('button', { name: PLAN_HINT })).toBeInTheDocument();
  });

  it('смена вкладки не двигает страницу: обе в одной ячейке сетки, скрытая держит место', async () => {
    const user = userEvent.setup();
    renderWith(<MailingDaysTabs view={FORECAST} />);
    const plan = layer(PLAN);
    const past = layer(PAST);

    // Одна ячейка сетки на двоих — блок высотой с бо́льшую вкладку, какая бы ни была выбрана.
    expect(plan.parentElement).toBe(past.parentElement);
    expect(plan.parentElement).toHaveStyle({ display: 'grid' });
    expect(plan).toHaveStyle({ gridArea: '1 / 1' });
    expect(past).toHaveStyle({ gridArea: '1 / 1' });
    expectShown(plan);
    expectHidden(past);

    await user.click(screen.getByRole('radio', { name: PAST }));

    // Те же узлы: вкладки не пересоздаются, меняется только, какая видна.
    expect(layer(PLAN)).toBe(plan);
    expect(layer(PAST)).toBe(past);
    expectHidden(plan);
    expectShown(past);
  });

  it('на телефоне — столбиком во всю ширину: в ряд с «i» на 360 px подпись срезалась', () => {
    // Заглушку снимает `restoreAllMocks` после теста (`test/setup.ts`).
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query) =>
        ({
          matches: query.includes('36em'),
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
        }) as unknown as MediaQueryList,
    );
    renderWith(<MailingDaysTabs view={FORECAST} />);

    const tabs = screen.getByRole('radiogroup', { name: 'План или факт по дням' });
    expect(tabs).toHaveAttribute('data-orientation', 'vertical');
    expect(tabs).toHaveAttribute('data-full-width');
  });

  it('клавиатура: стрелка переключает вкладку, а фокус остаётся на переключателе', async () => {
    const user = userEvent.setup();
    renderWith(<MailingDaysTabs view={FORECAST} />);

    await user.tab();
    expect(screen.getByRole('radio', { name: PLAN })).toHaveFocus();
    await user.keyboard('{ArrowRight}');

    expect(screen.getByRole('region', { name: PAST })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: PAST })).toHaveFocus();
  });
});

describe('рассылка: план и факт — колонками рядом', () => {
  // Замечание проверки 12.11.2026: «уйдёт первых — план, ушло первых — факт, добивок — план,
  // добивок — факт. А не просто „ушло N писем“… каких ушло? непонятно».
  it('колонки — план и факт у первых писем и у добивок, словами вкладок', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    expect(plan.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'День',
      'Мощность ящиков',
      'Первых: план',
      'Первых: факт',
      'Добивок: план',
      'Добивок: факт',
      'Почему меньше мощности',
    ]);
  });

  it('сегодня: план — день целиком, ушедшее и то, что ещё уйдёт; факт — ушедшее', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);
    const plan = screen.getByRole('region', { name: PLAN });

    // Сервер у сегодня отдаёт остаток дня: первых 24, добивок 5; ушло 6 и 2. План дня — 30 и 7:
    // мощность 30 рядом с планом 24 читалась бы днём, который её не выбирает. Причины у такого дня
    // нет (так сервер отвечает с 12.11.2026: ушедшее — не причина) — «во всю мощность».
    expect(cells(rowOf(plan, 'пн 02.11'))).toEqual([
      'пн 02.11сегодня',
      '30',
      '30',
      '6',
      '7',
      '2',
      'во всю мощность',
    ]);
  });

  it('сегодня что-то мешает: план дня меньше мощности, причина — что мешает, а не сколько ушло', () => {
    renderWith(<MailingDaysTabs view={HINDERED} />);
    const plan = screen.getByRole('region', { name: PLAN });

    // Ушло 6 и ещё уйдёт 14 — план дня 20 при мощности 30; причина — головой, слова фильтра лимитов.
    expect(cells(rowOf(plan, 'пн 02.11'))).toEqual([
      'пн 02.11сегодня',
      '30',
      '20',
      '6',
      '7',
      '2',
      'домен исчерпан на сегодня',
    ]);
  });

  it('окно получателя на сегодня закрылось: мощность — дня целиком, как отдал сервер; план равен факту', () => {
    // Вечером сервер отдаёт у сегодня остаток дня нулём, а мощность — дня целиком: день отправки
    // был (до 12.11.2026 приходил ноль, и рядом с планом дня стояло бы «мощность 0, план 6»).
    // Мощность экран берёт из строки плана, а не из «сегодня»: у ответа они здесь разные.
    const closed = 'окно получателя на сегодня закрылось в 12:00 UTC (пояс America/Toronto)';
    const over = { sending_day: false, why_not: closed, planned_first: 0, followups_due: 0 };
    const days = FORECAST.days.map((day, index) =>
      index === 0 ? { ...day, ...over, capacity_first: 28 } : day,
    );
    renderWith(<MailingDaysTabs view={{ ...FORECAST, days }} />);
    const plan = screen.getByRole('region', { name: PLAN });

    expect(cells(rowOf(plan, 'пн 02.11'))).toEqual([
      'пн 02.11сегодня',
      '28',
      '6',
      '6',
      '2',
      '2',
      'окно получателя на сегодня закрылось в 12:00 UTC (пояс UTC−5)',
    ]);
  });

  it('сегодня не день отправки: мощность — ноль, как у других дней без окна; план — ушедшее', () => {
    // Что сегодня за день — закрывшееся окно или день без окна, — решает сервер: экран мощность
    // дня из «сегодня» (`allowance`, в ответе 30) не подставляет. Ушедшее руками в такой день —
    // в плане дня и в факте, как пришло.
    const weekend = 'не день отправки: окно получателя — пн–пт 09:00–17:00 по его часам';
    const off = {
      sending_day: false,
      why_not: weekend,
      capacity_first: 0,
      planned_first: 0,
      followups_due: 0,
    };
    const days = FORECAST.days.map((day, index) => (index === 0 ? { ...day, ...off } : day));
    const { unmount } = renderWith(<MailingDaysTabs view={{ ...FORECAST, days }} />);

    expect(cells(rowOf(screen.getByRole('region', { name: PLAN }), 'пн 02.11'))).toEqual([
      'пн 02.11сегодня',
      '0',
      '6',
      '6',
      '2',
      '2',
      'не день отправки',
    ]);
    unmount();

    // Обычный такой день: ничего не уходило — нули, а не прочерки: день наступил.
    const quiet = { ...FORECAST.today, sent_first: 0, sent_followups: 0 };
    renderWith(<MailingDaysTabs view={{ ...FORECAST, days, today: quiet }} />);

    expect(
      cells(rowOf(screen.getByRole('region', { name: PLAN }), 'пн 02.11')).slice(1, 6),
    ).toEqual(['0', '0', '0', '0', '0']);
  });

  it('будущие дни: план — как отдал сервер, факт — прочерк, а не ноль', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);
    const plan = screen.getByRole('region', { name: PLAN });

    expect(cells(rowOf(plan, 'вт 03.11'))).toEqual([
      'вт 03.11',
      '35',
      '35',
      '—',
      '3',
      '—',
      'во всю мощность',
    ]);
    // День, в который ничего не уйдёт: план — ноль, а факта всё равно нет — прочерк.
    expect(cells(rowOf(plan, 'сб 07.11'))).toEqual([
      'сб 07.11',
      '0',
      '0',
      '—',
      '0',
      '—',
      'не день отправки',
    ]);
    // Пометка «сегодня» — только у первой строки.
    expect(within(plan).getAllByText('сегодня')).toHaveLength(1);
  });

  it('сегодня ещё ничего не ушло — факт ноль, а не прочерк: день наступил', () => {
    const fresh = { ...FORECAST, today: { ...FORECAST.today, sent_first: 0, sent_followups: 0 } };
    renderWith(<MailingDaysTabs view={fresh} />);
    const plan = screen.getByRole('region', { name: PLAN });

    expect(cells(rowOf(plan, 'пн 02.11')).slice(1, 6)).toEqual(['30', '24', '0', '5', '0']);
  });

  it('на телефоне шапки нет — у каждого числа своя короткая подпись', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);
    const plan = screen.getByRole('region', { name: PLAN });

    for (const day of ['пн 02.11', 'вт 03.11']) {
      const labels = [...rowOf(plan, day).querySelectorAll('td[data-label]')].map((cell) =>
        cell.getAttribute('data-label'),
      );
      expect(labels).toEqual([
        'Мощность',
        'Первых: план',
        'Первых: факт',
        'Добивок: план',
        'Добивок: факт',
      ]);
    }
  });

  it('над таблицей — только сколько ещё пустят лимиты: ушедшее — в колонках факта', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    const line = plan.getByText(/^Сегодня лимиты ящиков/);
    // Число не отрывается от «первых» — неразрывным пробелом.
    expect(line.textContent).toBe('Сегодня лимиты ящиков пустят ещё 24\u00a0первых.');
    expect(line).toHaveClass('mailingToday');
    expect(plan.queryByText(/ушло первых/)).toBeNull();
  });
});

describe('рассылка: факт за семь дней — с сегодняшним днём', () => {
  // То же замечание: «Факт за 7 дней — это реальная статистика или устаревшая, за прошлую
  // неделю?» — сегодня в факте не было.
  async function openPast(view: MailingForecastView): Promise<HTMLElement> {
    renderWith(<MailingDaysTabs view={view} />);
    await userEvent.setup().click(screen.getByRole('radio', { name: PAST }));
    return screen.getByRole('region', { name: PAST });
  }

  it('шесть прошлых дней, старые первыми, и сегодня — последней строкой с пометкой', async () => {
    const past = await openPast(FORECAST);

    expect(body(past)).toEqual([
      ['вт 27.10', '22', '6'],
      ['ср 28.10', '25', '3'],
      ['чт 29.10', '28', '8'],
      ['пт 30.10', '30', '5'],
      ['сб 31.10', '0', '0'],
      ['вс 01.11', '0', '0'],
      ['пн 02.11сегодня', '6', '2'],
    ]);
    // Строк — семь, как в заголовке вкладки: старший из прошлых дней уступил место сегодня.
    expect(within(past).queryByText('пн 26.10')).toBeNull();
  });

  it('сегодняшняя строка — числа «сегодня» прогноза: растёт с ними', async () => {
    const later = { ...FORECAST, today: { ...FORECAST.today, sent_first: 19, sent_followups: 4 } };
    const past = await openPast(later);

    expect(body(past).at(-1)).toEqual(['пн 02.11сегодня', '19', '4']);
  });

  it('прошлых дней сервер отдал меньше — они все и сегодня', async () => {
    const past = await openPast({ ...FORECAST, past: FORECAST.past.slice(-2) });

    expect(body(past)).toEqual([
      ['сб 31.10', '0', '0'],
      ['вс 01.11', '0', '0'],
      ['пн 02.11сегодня', '6', '2'],
    ]);
  });

  it('плана нет — сегодняшний день не назван: прошлые дни как есть, без строки сегодня', async () => {
    const past = await openPast({ ...FORECAST, days: [] });

    expect(body(past)).toHaveLength(7);
    expect(body(past)[0]).toEqual(['пн 26.10', '20', '4']);
    expect(within(past).queryByText('сегодня')).toBeNull();
  });
});

describe('рассылка: подсказка плана — слова этапа', () => {
  it('без слов — продаж: подсказка та же, что была до параметра', async () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);

    expect(await shownHint()).toEqual(
      hint('ящики продаж', 'сначала очередь, затем лиды, которым сборка напишет'),
    );
  });

  it('слова этапа — его ящики и что ждёт отправки; разгон, окно, добивки и дни — общие', async () => {
    renderWith(
      <MailingDaysTabs
        view={FORECAST}
        words={{ boxes: 'ящики рекламодателей', supply: 'письма в очереди рекламодателям' }}
      />,
    );

    expect(await shownHint()).toEqual(
      hint('ящики рекламодателей', 'письма в очереди рекламодателям'),
    );
  });
});

describe('рассылка: почему меньше мощности — головой причины', () => {
  it('голова — слова сервера до первого «: », « — » или «; »; без них — причина целиком', () => {
    expect(reasonHead('не день отправки: окно получателя — пн–пт 09:00–17:00 по его часам')).toBe(
      'не день отправки',
    );
    // Причины сегодняшнего дня с 12.11.2026 — без «сегодня уже ушло…»: слова фильтра лимитов.
    expect(
      reasonHead(
        'домен исчерпан на сегодня: a.example.test — 10 из 10; домен на паузе: b.example.test — жалоба',
      ),
    ).toBe('домен исчерпан на сегодня');
    expect(
      reasonHead(
        'темп автоотправки: письмо с ящика раз в 15 мин — до 15:00 UTC успеет не больше 4 с ящика',
      ),
    ).toBe('темп автоотправки');
    // Раздела в словах нет — причина целиком, и «i» рядом не нужен.
    expect(reasonHead('часть лимита домена выбрали ящики другого направления на нём')).toBe(
      'часть лимита домена выбрали ящики другого направления на нём',
    );
    // Двоеточие во времени — не раздел: после него нет пробела.
    expect(reasonHead('окно получателя на сегодня закрылось в 12:00 UTC (пояс Asia/Almaty)')).toBe(
      'окно получателя на сегодня закрылось в 12:00 UTC (пояс Asia/Almaty)',
    );
  });

  it('в строке дня — голова и «i» с причиной целиком; короткая причина — без «i»', async () => {
    renderWith(<MailingDaysTabs view={HINDERED} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    expect(plan.getByText('домен исчерпан на сегодня')).toBeInTheDocument();
    expect(plan.queryByText(/10 из 10/)).toBeNull();
    // У дней «во всю мощность» причины нет — и «i» нет.
    expect(plan.getAllByRole('button', { name: /^Почему меньше мощности — / })).toHaveLength(5);

    await userEvent
      .setup()
      .hover(plan.getByRole('button', { name: /^Почему меньше мощности — пн 02\.11/ }));
    expect(await screen.findByText(HINDERED_WORDS)).toBeInTheDocument();
  });
});

describe('рассылка: пояс на виду — смещением от UTC, имя из базы поясов — в «i»', () => {
  // Проверка прода 10.11.2026: в причине дня стояло имя пояса из базы поясов — код, а не слова.
  const CLOSED = 'окно получателя на сегодня закрылось в 12:00 UTC (пояс America/Toronto)';
  const TODAY_HINT = /^Почему меньше мощности — пн 02\.11/;

  /** Прогноз, у которого окно сегодня уже закрылось: причина дня — слова сервера с именем пояса. */
  function closedToday(words: string, zone: string | null): MailingForecastView {
    const days = FORECAST.days.map((day, index) =>
      index === 0 ? { ...day, why_not: words } : day,
    );
    return { ...FORECAST, zone, days };
  }

  it('в причине дня имя пояса — смещением; слова сервера с именем — в «i» той же ячейки', async () => {
    renderWith(<MailingDaysTabs view={closedToday(CLOSED, 'America/Toronto')} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    expect(
      plan.getByText('окно получателя на сегодня закрылось в 12:00 UTC (пояс UTC−5)'),
    ).toBeInTheDocument();
    expect(plan.queryByText(/America\/Toronto/)).toBeNull();

    await userEvent.setup().hover(plan.getByRole('button', { name: TODAY_HINT }));
    expect(await screen.findByText(CLOSED)).toBeInTheDocument();
  });

  it('смещение — на сегодня: летом у того же пояса оно другое', () => {
    vi.setSystemTime(new Date('2026-07-06T10:00:00Z'));
    renderWith(<MailingDaysTabs view={closedToday(CLOSED, 'America/Toronto')} />);

    expect(
      screen.getByText('окно получателя на сегодня закрылось в 12:00 UTC (пояс UTC−4)'),
    ).toBeInTheDocument();
  });

  it('браузер пояса не знает — слова сервера как есть, без «i»; в подсказке плана — одно имя', async () => {
    const odd = 'окно получателя на сегодня закрылось в 12:00 UTC (пояс Mars/Olympus)';
    renderWith(<MailingDaysTabs view={closedToday(odd, 'Mars/Olympus')} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    expect(plan.getByText(odd)).toBeInTheDocument();
    expect(plan.queryByRole('button', { name: TODAY_HINT })).toBeNull();
    expect((await shownHint()).at(-1)).toBe(
      'Окно получателя — пн–пт 09:00–17:00 по его часам; часы дня — по поясу Mars/Olympus, самому частому в очереди.',
    );
  });

  it('пояса у прогноза нет — причина как есть, подсказка плана — без пояса', async () => {
    renderWith(<MailingDaysTabs view={closedToday(CLOSED, null)} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    expect(plan.getByText(CLOSED)).toBeInTheDocument();
    expect((await shownHint()).at(-1)).toBe('Окно получателя — пн–пт 09:00–17:00 по его часам.');
  });
});

describe('рассылка: одна причина у всех дней — один раз над таблицей', () => {
  const same = {
    ...FORECAST,
    days: FORECAST.days.map((day) => ({ ...day, why_not: NOTHING_LEFT })),
  };

  it('одна у всех — строкой над таблицей, колонки причин нет', async () => {
    renderWith(<MailingDaysTabs view={same} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    expect(plan.getByText('Во все дни меньше мощности:')).toBeInTheDocument();
    expect(plan.getByText('отправлять больше нечего')).toBeInTheDocument();
    expect(plan.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'День',
      'Мощность ящиков',
      'Первых: план',
      'Первых: факт',
      'Добивок: план',
      'Добивок: факт',
    ]);
    await userEvent
      .setup()
      .hover(plan.getByRole('button', { name: 'Почему меньше мощности — во все дни' }));
    expect(await screen.findByText(NOTHING_LEFT)).toBeInTheDocument();
  });

  it('разные или у дня причины нет — колонка, как была', () => {
    expect(oneReason(FORECAST.days)).toBeNull();
    expect(oneReason(same.days)).toBe(NOTHING_LEFT);
    expect(oneReason([])).toBeNull();
    expect(oneReason(FORECAST.days.map((day) => ({ ...day, why_not: null })))).toBeNull();
  });
});
