/**
 * Вкладка «Воронка»: числа шагов — словами сервера, доли — со своей основой, фильтры —
 * гипотеза и период. Ответы сервера — записанные (`serve()`), незаписанный запрос роняет
 * тест; имена выдуманы.
 *
 * Проверяется то, ради чего вкладка: экран не считает сам — показывает числа ответа; доля
 * передачи — от ответивших, остальные — от отправленных, основа ноль — словами, а не «0%»;
 * гипотеза и период уходят на сервер, период — полуночами по часам браузера; негодные свои
 * даты не уходят вовсе, а что не так — сказано под полем; отказ сервера — словами.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { FunnelCounts, HypothesesView, SalesFunnelView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { funnelQuery, NO_FUNNEL_FILTERS, periodProblem, stepHint } from './funnelData';

const SCREEN_WAIT = { timeout: 5000 };
const FUNNEL = '/api/sales/funnel';

function hypothesis(id: number, name: string) {
  return {
    id,
    name,
    description: null,
    created_at: '2026-10-01T10:00:00+00:00',
    leads: { new: 0, ready: 3, rejected: 0 },
    total: 3,
  };
}

const HYPOTHESES: HypothesesView = {
  rows: [hypothesis(5, 'тестовая гипотеза'), hypothesis(8, 'вторая тестовая')],
  total: 2,
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

function counts(values: Partial<FunnelCounts>): FunnelCounts {
  return {
    queued: 0,
    sent: 0,
    delivered: 0,
    bounced: 0,
    answered: 0,
    handed_off: 0,
    ...values,
  };
}

const FIRST = counts({
  queued: 3,
  sent: 13,
  delivered: 11,
  bounced: 2,
  answered: 4,
  handed_off: 1,
});
const SECOND = counts({ sent: 1, delivered: 1 });

const ALL_VIEW: SalesFunnelView = {
  hypothesis_id: null,
  since: null,
  until: null,
  rows: [
    { hypothesis_id: 5, name: 'тестовая гипотеза', counts: FIRST },
    { hypothesis_id: 8, name: 'вторая тестовая', counts: SECOND },
  ],
  total: counts({ queued: 3, sent: 14, delivered: 12, bounced: 2, answered: 4, handed_off: 1 }),
};

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openFunnel(routes: Routes = {}, view: SalesFunnelView = ALL_VIEW) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [`GET ${FUNNEL}`]: { body: view },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=funnel');
  await screen.findByText('Отправлено', { selector: '.metricTitle' }, SCREEN_WAIT);
  return recorded;
}

function calls(recorded: Recorded, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === 'GET' && call.path === path);
}

/** Плитка шага: подпись, число и пояснение — тексты её трёх строк. */
function tile(title: string): string[] {
  const label = screen.getByText(title, { selector: '.metricTitle' });
  const card = label.closest('.metricTile');
  if (!(card instanceof HTMLElement)) throw new Error(`плитки «${title}» нет`);
  return Array.from(card.children).map((child) => child.textContent ?? '');
}

/** Ячейка таблицы: число и доля под ним — строками ячейки через « / ». */
function cellText(cell: HTMLElement): string {
  return Array.from(cell.querySelectorAll('p'))
    .map((line) => line.textContent)
    .join(' / ');
}

/** Полночь суток `daysAgo` назад по часам браузера — моментом ISO. */
function midnightAgo(daysAgo: number): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo).toISOString();
}

describe('воронка продаж', () => {
  it('плитки — числа сервера, доли — от своей основы словами', async () => {
    // A4, вторая половина: экран показывает числа ответа сервера как есть.
    await openFunnel();

    expect(tile('В очереди')).toEqual(['В очереди', '3', 'ещё ничего не ушло']);
    expect(tile('Отправлено')).toEqual(['Отправлено', '14', 'лидов, не писем']);
    expect(tile('Доставлено')).toEqual(['Доставлено', '12', '85,7% от отправленных']);
    expect(tile('Отказ')).toEqual(['Отказ', '2', '14,3% от отправленных']);
    expect(tile('Ответ')).toEqual(['Ответ', '4', '28,6% от отправленных']);
    // Передача — от ответивших, а не от отправленных: её основа другая.
    expect(tile('Лид передан')).toEqual(['Лид передан', '1', '25% от ответивших']);
  });

  it('по гипотезам — строка на каждую и итог, числа и доли сервера', async () => {
    // A4, вторая половина: экран показывает числа ответа сервера как есть.
    await openFunnel();

    const table = screen.getByRole('table', { name: 'Воронка по гипотезам' });
    const rows = within(table).getAllByRole('row');
    const texts = rows.slice(1).map((row) => within(row).getAllByRole('cell').map(cellText));
    expect(texts).toEqual([
      ['тестовая гипотеза', '3', '13', '11 / 84,6%', '2 / 15,4%', '4 / 30,8%', '1 / 25%'],
      ['вторая тестовая', '0', '1', '1 / 100%', '0 / 0%', '0 / 0%', '0'],
      ['Всего', '3', '14', '12 / 85,7%', '2 / 14,3%', '4 / 28,6%', '1 / 25%'],
    ]);
  });

  it('гипотеза — запрос с её номером, строк по гипотезам нет', async () => {
    const recorded = await openFunnel({
      [`GET ${FUNNEL}?hypothesis=8`]: {
        body: { ...ALL_VIEW, hypothesis_id: 8, rows: [ALL_VIEW.rows[1]], total: SECOND },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'Гипотеза' }));
    await user.click(
      await screen.findByRole('option', { name: 'вторая тестовая', hidden: true }, SCREEN_WAIT),
    );

    await waitFor(() => expect(calls(recorded, `${FUNNEL}?hypothesis=8`)).toHaveLength(1));
    await waitFor(() => expect(tile('Отправлено')[1]).toBe('1'), SCREEN_WAIT);
    expect(screen.queryByRole('table', { name: 'Воронка по гипотезам' })).toBeNull();
    // Ответивших нет — доля передачи названа словами, а не «0%».
    expect(tile('Лид передан')).toEqual(['Лид передан', '0', 'нет ответивших']);
  });

  it('7 дней — с полуночи шесть суток назад по часам браузера', async () => {
    const since = new URLSearchParams({ since: midnightAgo(6) }).toString();
    const recorded = await openFunnel({ [`GET ${FUNNEL}?${since}`]: { body: ALL_VIEW } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: '7 дней' }));

    await waitFor(() => expect(calls(recorded, `${FUNNEL}?${since}`)).toHaveLength(1));
  });

  it('свои даты: первый день позже последнего — сказано, запрос не уходит', async () => {
    const from = new URLSearchParams({ since: new Date(2026, 9, 5).toISOString() }).toString();
    const asked = new URLSearchParams({
      since: new Date(2026, 9, 5).toISOString(),
      until: new Date(2026, 9, 10).toISOString(),
    }).toString();
    const recorded = await openFunnel({
      [`GET ${FUNNEL}?${from}`]: { body: ALL_VIEW },
      [`GET ${FUNNEL}?${asked}`]: { body: ALL_VIEW },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Свои даты' }));
    // Только первый день — период без конца: запрос уходит сразу.
    fireEvent.change(screen.getByLabelText('Первый день'), { target: { value: '2026-10-05' } });
    await waitFor(() => expect(calls(recorded, `${FUNNEL}?${from}`)).toHaveLength(1));
    const before = recorded.calls.length;

    fireEvent.change(screen.getByLabelText(/Последний день/), { target: { value: '2026-10-01' } });

    expect(
      await screen.findByText('Первый день позже последнего — поменяйте даты местами.'),
    ).toBeInTheDocument();
    expect(recorded.calls.length).toBe(before);

    // Последний день — включительно: граница уходит полуночью следующих суток.
    fireEvent.change(screen.getByLabelText(/Последний день/), { target: { value: '2026-10-09' } });

    await waitFor(() => expect(calls(recorded, `${FUNNEL}?${asked}`)).toHaveLength(1));
    expect(recorded.calls.slice(before).map((call) => call.path)).toEqual([`${FUNNEL}?${asked}`]);
  });

  it('за период ничего — сказано словами, а не одними нулями', async () => {
    const week = new URLSearchParams({ since: midnightAgo(29) }).toString();
    const empty = { ...ALL_VIEW, rows: [], total: counts({}) };
    await openFunnel({ [`GET ${FUNNEL}?${week}`]: { body: empty } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: '30 дней' }));

    expect(
      await screen.findByText(
        'За этот период первые письма лидам не уходили и в очередь не вставали.',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
    expect(tile('Доставлено')).toEqual(['Доставлено', '0', 'нет отправленных']);
  });

  it('на узком окне период — столбиком во всю ширину: в ряд «Свои даты» уходили за край', async () => {
    // Заглушку снимает `restoreAllMocks` после теста (`test/setup.ts`).
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query.includes('max-width: 36em'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }));
    await openFunnel();

    const period = screen.getByRole('radiogroup', { name: 'Период' });
    expect(period).toHaveAttribute('data-orientation', 'vertical');
    expect(period).toHaveAttribute('data-full-width');
  });

  it('отказ на новом фильтре — словами над прежними числами, а не молча', async () => {
    await openFunnel({
      [`GET ${FUNNEL}?hypothesis=8`]: {
        status: 404,
        body: { detail: 'гипотезы №8 нет — обновите список гипотез' },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'Гипотеза' }));
    await user.click(
      await screen.findByRole('option', { name: 'вторая тестовая', hidden: true }, SCREEN_WAIT),
    );

    const alert = (await screen.findByText('Воронка не загрузилась', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent('гипотезы №8 нет — обновите список гипотез');
  });

  it('отказ сервера — словами', async () => {
    openFunnelRefused();

    const alert = (await screen.findByText('Воронка не загрузилась', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent('гипотезы №5 нет — обновите список гипотез');
  });
});

function openFunnelRefused() {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [`GET ${FUNNEL}`]: {
      status: 404,
      body: { detail: 'гипотезы №5 нет — обновите список гипотез' },
    },
  });
  renderWith(<AppRoutes />, '/sales?tab=funnel');
}

describe('фильтры воронки → запрос', () => {
  const NOW = new Date(2026, 9, 14, 9, 37);

  it('всё время и все гипотезы — без условий', () => {
    expect(funnelQuery(NO_FUNNEL_FILTERS, NOW)).toEqual({});
  });

  it('30 дней — сегодня и двадцать девять суток до него, с полуночи', () => {
    expect(funnelQuery({ ...NO_FUNNEL_FILTERS, period: 'month', hypothesis: 5 }, NOW)).toEqual({
      hypothesis: 5,
      since: new Date(2026, 8, 15).toISOString(),
    });
  });

  it('свои даты — с полуночи первого дня до полуночи после последнего; пустое — без границы', () => {
    const custom = { ...NO_FUNNEL_FILTERS, period: 'custom' as const };
    expect(funnelQuery({ ...custom, from: '2026-10-01', to: '2026-10-07' }, NOW)).toEqual({
      since: new Date(2026, 9, 1).toISOString(),
      until: new Date(2026, 9, 8).toISOString(),
    });
    expect(funnelQuery({ ...custom, to: '2026-10-07' }, NOW)).toEqual({
      until: new Date(2026, 9, 8).toISOString(),
    });
  });

  it('один и тот же день — годится, обратный порядок — нет', () => {
    const custom = { ...NO_FUNNEL_FILTERS, period: 'custom' as const };
    expect(periodProblem({ ...custom, from: '2026-10-07', to: '2026-10-07' })).toBeNull();
    expect(periodProblem({ ...custom, from: '2026-10-08', to: '2026-10-07' })).toBe(
      'Первый день позже последнего — поменяйте даты местами.',
    );
  });

  it('доля — от своей основы, основа ноль — словами', () => {
    expect(stepHint(FIRST, 'handed_off')).toBe('25% от ответивших');
    expect(stepHint(counts({ answered: 3 }), 'answered')).toBe('нет отправленных');
  });
});
