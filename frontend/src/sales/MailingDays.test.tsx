/**
 * План и факт «Рассылки» — вкладками одного блока (`MailingDaysTabs`) и слова этапа параметром
 * (`PlanWords`): у продаж текст подсказки тот же, что был до параметра, у другого этапа — свои ящики
 * и что ждёт отправки, а разгон, окно, добивки и дни по UTC — общие правила прогноза, одним текстом.
 *
 * Смена вкладки страницу не двигает (замечание владельца 10.11.2026): обе таблицы в одной ячейке
 * сетки, скрытая держит место. Высоты в jsdom нет — проверяется устройство: обе вкладки в ячейке,
 * скрытая — `visibility: hidden`, `inert` и `aria-hidden`; сам прыжок мерит снимок стенда.
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
    `Уйдёт первых — мощность, но не больше, чем есть: ${supply}. Сегодня — сколько ещё уйдёт.`,
    'Добивок — со сроком в этот день; просроченные — сегодня, срок в день без окна — в следующем дне отправки. С ящика в сутки уходит не больше потолка добивок: что сверх него — тоже в следующем дне отправки. В лимит первых писем добивки не входят.',
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

/** Что читает человек: наводит на «i» у вкладок плана — абзацы подсказки по порядку. */
async function shownHint(): Promise<string[]> {
  await userEvent.setup().hover(screen.getByRole('button', { name: PLAN_HINT }));
  const first = await screen.findByText(/^Мощность ящиков — /);
  const paragraphs = first.parentElement;
  if (paragraphs === null) throw new Error('у абзаца подсказки нет обёртки');
  return Array.from(paragraphs.children, (paragraph) => paragraph.textContent ?? '');
}

describe('рассылка: план и факт — вкладками', () => {
  it('сначала план: сегодня и семь дней; факта на экране нет', () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);

    expect(screen.getByRole('radio', { name: PLAN })).toBeChecked();
    const plan = within(screen.getByRole('region', { name: PLAN }));
    expect(plan.getByText(/^Сегодня ушло первых/)).toBeInTheDocument();
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
    expect(past.queryByText(/^Сегодня ушло первых/)).toBeNull();
    expect(screen.queryByRole('region', { name: PLAN })).toBeNull();
    expect(screen.queryByRole('button', { name: PLAN_HINT })).toBeNull();
    await user.hover(screen.getByRole('button', { name: `Как считается «${PAST}»` }));
    expect(await screen.findByText(/^Сколько писем ушло на самом деле/)).toHaveTextContent(
      'Сегодня здесь нет: оно на вкладке плана.',
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
    expect(
      reasonHead(
        'сегодня уже ушло 255 первых писем — остаток дня 6; домен исчерпан на сегодня: a.test',
      ),
    ).toBe('сегодня уже ушло 255 первых писем');
    expect(reasonHead('остаток дня 6; часть лимита домена выбрали ящики другого направления')).toBe(
      'остаток дня 6',
    );
    // Двоеточие во времени — не раздел: после него нет пробела.
    expect(reasonHead('окно получателя на сегодня закрылось в 12:00 UTC (пояс Asia/Almaty)')).toBe(
      'окно получателя на сегодня закрылось в 12:00 UTC (пояс Asia/Almaty)',
    );
  });

  it('в строке дня — голова и «i» с причиной целиком; короткая причина — без «i»', async () => {
    renderWith(<MailingDaysTabs view={FORECAST} />);
    const plan = within(screen.getByRole('region', { name: PLAN }));

    expect(plan.getByText('сегодня уже ушло 6 первых писем')).toBeInTheDocument();
    expect(plan.queryByText(/остаток дня 24/)).toBeNull();
    // У дней «во всю мощность» причины нет — и «i» нет.
    expect(plan.getAllByRole('button', { name: /^Почему меньше мощности — / })).toHaveLength(5);

    await userEvent
      .setup()
      .hover(plan.getByRole('button', { name: /^Почему меньше мощности — пн 02\.11/ }));
    expect(
      await screen.findByText('сегодня уже ушло 6 первых писем — остаток дня 24'),
    ).toBeInTheDocument();
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
      'Уйдёт первых',
      'Добивок',
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
