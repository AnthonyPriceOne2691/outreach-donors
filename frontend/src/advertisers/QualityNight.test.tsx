/**
 * Ночной обход в «Качестве загрузок»: одна строка, что нового за ночь, прошлые ночи раскрытием.
 *
 * Проверяется то, чем строка может соврать: «новых нет» там, где считать не по чему; прошлые ночи,
 * спрошенные без нажатия; список доменов у прошлой ночи; ночь, показанная дважды.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { QualityHostsView, QualityRow, QualityView } from '../api/advertiserPanel';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';
import { PanelQuality } from './PanelQuality';

const NIGHT_ROW: QualityRow = {
  at: '2026-11-10T01:30:00Z',
  kind: 'night',
  load_id: null,
  day: '2026-11-10',
  file_name: null,
  donors: 40,
  host: null,
  scanned: 38,
  pages: 30_000,
  selling_links: 900,
  selling_pages: 600,
  share: 0.02,
  night: { nights: 4, running: false, fresh: 3 },
};

const BUTTON_ROW: QualityRow = {
  ...NIGHT_ROW,
  at: '2026-11-02T09:00:00Z',
  kind: 'crawl',
  day: '2026-11-02',
  donors: 3,
  pages: 1_200,
  night: null,
};

/** Прошлая ночь: те же доноры, свой итог; «нового» у неё нет. */
function past(day: string, pages: number): QualityRow {
  return { ...NIGHT_ROW, at: `${day}T01:30:00Z`, day, pages, night: null };
}

const QUALITY_ROUTE = 'GET /api/advertisers/panel/quality?page=1';
const NIGHTS_ROUTE = 'GET /api/advertisers/panel/quality/nights?page=1';
const MORE_ROUTE = 'GET /api/advertisers/panel/quality/nights?page=2';

function quality(night: QualityRow = NIGHT_ROW): { body: QualityView } {
  return { body: { rows: [night, BUTTON_ROW], total: 2, page: 1, limit: 20 } };
}

function nights(rows: QualityRow[], page = 1): { body: QualityView } {
  return { body: { rows, total: 3, page, limit: 2 } };
}

function open(extra: Record<string, unknown> = {}, night: QualityRow = NIGHT_ROW) {
  const recorded = serve({ [QUALITY_ROUTE]: quality(night), ...extra });
  renderWith(<PanelQuality />, '/advertisers');
  return recorded;
}

function paths(recorded: { calls: Call[] }): string[] {
  return recorded.calls.map((call: Call) => call.path);
}

async function nightRow(): Promise<HTMLElement> {
  return (await screen.findByText('ночной обход — каждую ночь')).closest('tr') as HTMLElement;
}

describe('ночной обход в качестве загрузок', () => {
  it('одна строка: итог последней ночи, что нового и сколько ночей за ней', async () => {
    const recorded = open();

    const row = await nightRow();
    expect(within(row).getByText('10.11.2026')).toBeVisible();
    // Дата строки — последняя ночь, числа — её итог, а не сумма ночей.
    expect(within(row).getByText('последняя ночь')).toBeVisible();
    expect(within(row).getByText('30 000')).toBeVisible();
    expect(within(row).getByText('900')).toBeVisible();
    expect(within(row).getByText('2%')).toBeVisible();
    // Число не отрывается от подписи — неразрывным пробелом.
    expect(within(row).getByText(/^новых рекламодателей/).textContent).toBe(
      'новых рекламодателей за\u00a0ночь:\u00a03',
    );
    const more = within(row).getByRole('button', { name: 'ещё\u00a03\u00a0ночи' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    expect(more.textContent).toBe('ещё\u00a03\u00a0ночи');
    // Что ночь проходит тех же доноров и дублей нет — у самой строки.
    expect(within(row).getByRole('button', { name: 'Что такое ночной обход' })).toBeVisible();
    // Строка одна, обход кнопкой — своей строкой дня; способы разные — колонка способа на месте.
    expect(screen.getAllByText('ночной обход — каждую ночь')).toHaveLength(1);
    expect(screen.getByText('обход кнопкой')).toBeVisible();
    expect(screen.getByRole('columnheader', { name: 'Способ загрузки' })).toBeVisible();
    // Прошлые ночи без нажатия не спрашиваются.
    expect(paths(recorded)).toEqual(['/api/advertisers/panel/quality?page=1']);
  });

  it('пояснение блока — окошком по нажатию: повтор тех же доноров — не дубли', async () => {
    open();
    const user = userEvent.setup();
    await nightRow();

    await user.click(screen.getByRole('button', { name: 'Как устроен экран «Качество загрузок»' }));

    // Длинное пояснение — окошком, а не всплывающей подсказкой: его можно удержать и прокрутить.
    // Окошко ещё в переходе появления — читаем его текст, а не видимость.
    const about = await screen.findByRole('dialog', {
      name: 'Как устроен экран «Качество загрузок»',
    });
    expect(within(about).getByText(/^Ночной обход — одна строка/)).toHaveTextContent(
      'итог последней ночи',
    );
    const said = within(about).getByText(/^Повторный обход — ночной или кнопкой/);
    expect(said).toHaveTextContent('проходит тех же доноров заново, это не новые загрузки');
    expect(said).toHaveTextContent('решения людей сохраняются. Дублей нет.');
    expect(within(about).getByText(/^Новый рекламодатель — домен «куплено»/)).toBeInTheDocument();
  });

  it('прошлые ночи — по нажатию и порциями; «Свернуть» — та же кнопка', async () => {
    const recorded = open({
      [NIGHTS_ROUTE]: nights([past('2026-11-09', 29_000), past('2026-11-08', 28_000)]),
      [MORE_ROUTE]: nights([past('2026-11-07', 27_000)], 2),
    });
    const user = userEvent.setup();
    const row = await nightRow();

    await user.click(within(row).getByRole('button', { name: 'ещё\u00a03\u00a0ночи' }));

    const yesterday = (await screen.findByText('09.11.2026')).closest('tr') as HTMLElement;
    expect(within(yesterday).getByText('29 000')).toBeVisible();
    expect(screen.getByText('08.11.2026')).toBeVisible();
    // У прошлой ночи списка доменов нет: окно показало бы нынешние обходы, а не ту ночь.
    expect(within(yesterday).queryByRole('button')).toBeNull();
    expect(within(yesterday).getByText('40')).toBeVisible();
    const fold = within(row).getByRole('button', { name: 'Свернуть' });
    expect(fold).toHaveAttribute('aria-expanded', 'true');
    const controlled = (fold.getAttribute('aria-controls') ?? '').split(' ');
    expect(controlled).toHaveLength(2);
    expect(controlled.map((id) => document.getElementById(id))).toEqual([
      yesterday,
      screen.getByText('08.11.2026').closest('tr'),
    ]);
    expect(screen.getByText(/^показано/).textContent).toBe('показано 2 из\u00a03');

    await user.click(screen.getByRole('button', { name: 'Показать ещё\u00a01' }));

    expect(await screen.findByText('07.11.2026')).toBeVisible();
    expect(screen.queryByRole('button', { name: /Показать ещё/ })).toBeNull();
    expect(paths(recorded)).toEqual([
      '/api/advertisers/panel/quality?page=1',
      '/api/advertisers/panel/quality/nights?page=1',
      '/api/advertisers/panel/quality/nights?page=2',
    ]);

    await user.click(within(row).getByRole('button', { name: 'Свернуть' }));

    expect(screen.queryByText('09.11.2026')).toBeNull();
    expect(within(row).getByRole('button', { name: 'ещё\u00a03\u00a0ночи' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('ночь, пришедшая в двух порциях, показана один раз', async () => {
    // Новая ночь началась между порциями: сдвиг отдал 08.11 и во второй.
    open({
      [NIGHTS_ROUTE]: nights([past('2026-11-09', 29_000), past('2026-11-08', 28_000)]),
      [MORE_ROUTE]: nights([past('2026-11-08', 28_000), past('2026-11-07', 27_000)], 2),
    });
    const user = userEvent.setup();

    await user.click(
      within(await nightRow()).getByRole('button', { name: 'ещё\u00a03\u00a0ночи' }),
    );
    await user.click(await screen.findByRole('button', { name: 'Показать ещё\u00a01' }));

    expect(await screen.findByText('07.11.2026')).toBeVisible();
    expect(screen.getAllByText('08.11.2026')).toHaveLength(1);
  });

  it.each([
    { said: 'числа нет — слов нет', night: { fresh: null }, words: null },
    {
      said: 'ноль — «новых нет»',
      night: { fresh: 0 },
      words: 'новых рекламодателей за\u00a0ночь нет',
    },
    {
      said: 'обход ещё идёт — итог не окончательный',
      night: { fresh: 2, running: true },
      words: 'обход ещё идёт · новых рекламодателей за\u00a0ночь:\u00a02',
    },
    {
      said: 'обход идёт, а считать ещё не по чему',
      night: { fresh: null, running: true },
      words: 'обход ещё идёт',
    },
  ])('что нового: $said', async ({ night, words }) => {
    open({}, { ...NIGHT_ROW, night: { nights: 4, running: false, ...night } });

    const row = await nightRow();

    const said = row.querySelector('.qualityNews');
    expect(said === null ? null : said.textContent).toBe(words);
  });

  it('первая ночь — раскрывать нечего', async () => {
    open({}, { ...NIGHT_ROW, night: { nights: 1, running: false, fresh: 5 } });

    const row = await nightRow();

    expect(within(row).getByText(/^новых рекламодателей/)).toBeVisible();
    expect(within(row).queryByRole('button', { name: /ещё/ })).toBeNull();
  });

  it('один донор ночи — им самим, а не «1»', async () => {
    open({}, { ...NIGHT_ROW, donors: 1, host: 'lone.example.test' });

    const row = await nightRow();

    expect(row).toHaveTextContent('lone.example.test');
    expect(within(row).queryByRole('button', { name: /Домены строки/ })).toBeNull();
  });

  it('короткий способ помечен для телефона — «обход кнопкой» встаёт в строку даты', async () => {
    // С ночной строкой способы на странице разные, и колонка способа есть у каждой строки: на
    // телефоне короткий способ не занимает свою строку карточки (стили — по `data-short`).
    const lone: QualityRow = {
      ...BUTTON_ROW,
      day: '2026-11-01',
      donors: 1,
      host: 'lone.example.test',
    };
    open({ [QUALITY_ROUTE]: { body: { ...quality().body, rows: [NIGHT_ROW, BUTTON_ROW, lone] } } });

    const short = (await screen.findByText('обход кнопкой')).closest('tr') as HTMLElement;

    expect(short).toHaveAttribute('data-short', 'true');
    // Способ с доменом и ночная строка — во всю ширину карточки, как были.
    const named = screen.getByText('обход кнопкой: lone.example.test').closest('tr') as HTMLElement;
    expect(named).not.toHaveAttribute('data-short');
    expect(await nightRow()).not.toHaveAttribute('data-short');
  });

  it('сервер старше экрана — строки без поля ночи читаются загрузками и днями', async () => {
    // Выкатка идёт не разом: ответ прежнего сервера о ночной строке не знает и поля не шлёт.
    const old: QualityRow = { ...BUTTON_ROW };
    delete old.night;
    const earlier = { ...old, day: '2026-11-01', at: '2026-11-01T09:00:00Z' };
    open({ [QUALITY_ROUTE]: { body: { rows: [old, earlier], total: 2, page: 1, limit: 20 } } });

    expect(await screen.findByText('Способ загрузки во всех строках: обход кнопкой')).toBeVisible();
    expect(screen.getByText('02.11.2026')).toBeVisible();
    expect(screen.queryByText(/ночной обход/)).toBeNull();
  });

  it('домены ночной строки — окном, по ночи', async () => {
    const hosts: QualityHostsView = {
      rows: [{ host: 'one.example.test', crawl: null, selling_links: 4 }],
      total: 1,
      page: 1,
      limit: 50,
    };
    const route = '/api/advertisers/panel/quality/hosts?night=2026-11-10&page=1';
    const recorded = open({ [`GET ${route}`]: { body: hosts } });
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', { name: 'Домены строки: ночной обход — каждую ночь' }),
    );

    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('обхода не было')).toBeVisible();
    await waitFor(() => expect(paths(recorded)).toContain(route));
  });

  it('отказ прошлых ночей — словами сервера, строка ночи живёт', async () => {
    open({ [NIGHTS_ROUTE]: { status: 503, body: { detail: 'База не ответила' } } });
    const user = userEvent.setup();
    const row = await nightRow();

    await user.click(within(row).getByRole('button', { name: 'ещё\u00a03\u00a0ночи' }));

    expect(await screen.findByText('Прошлые ночи не загрузились')).toBeVisible();
    expect(screen.getByText('База не ответила')).toBeVisible();
    expect(within(row).getByText('30 000')).toBeVisible();
    expect(screen.queryByRole('button', { name: /Показать ещё/ })).toBeNull();
  });
});
