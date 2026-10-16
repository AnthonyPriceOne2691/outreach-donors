/**
 * «Гео» — все пять стран донора (решение 16.10.2026).
 *
 * Оператор видел в колонке «Гео» одну страну («США · 21%»), а нужны все пять и
 * фильтр по ним. Проверяется: первая строка — как была, под ней мельче — остальные
 * страны топ-5 с долями; у неполной разбивки — пометка, что остальные не запрашивались;
 * фильтр «Гео» предлагает страны разбивок и уходит на сервер кодом; карточка у неполной
 * разбивки говорит то же и объясняет в «i».
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DonorFullCard, DonorRowCard, DonorsPage } from '../api/types';
import { CARD, CONTACTS_STATE, page, ROW } from '../test/donorFixtures';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

/** Полная разбивка: США первыми, за ними Великобритания и Канада. */
const FULL: DonorRowCard = {
  ...ROW,
  geo: 'us',
  geo_top_share: 0.55,
  geo_breakdown: [
    { country: 'us', share: 0.55 },
    { country: 'gb', share: 0.12 },
    { country: 'ca', share: 0.08 },
  ],
  geo_partial: false,
};

/** Донор до 16.10.2026: спрошена только верхняя страна. */
const PARTIAL: DonorRowCard = {
  ...ROW,
  id: 2,
  host: 'partial.example.test',
  geo: 'us',
  geo_top_share: 0.8,
  geo_breakdown: [{ country: 'us', share: 0.8 }],
  geo_partial: true,
};

/** Заведён руками: страна есть, разбивки нет. */
const MANUAL: DonorRowCard = {
  ...ROW,
  id: 3,
  host: 'manual.example.test',
  geo: 'de',
  geo_top_share: 0.4,
  geo_breakdown: null,
  geo_partial: false,
};

const GEO_PAGE: DonorsPage = page({
  rows: [FULL, PARTIAL, MANUAL],
  total: 3,
  counts: { suitable: 3 },
  countries: { us: 2, gb: 1, ca: 1, de: 1 },
  freshness: { fresh: 3 },
});

function donorsAt(query: string): string {
  return `GET /api/donors?${query}`;
}

async function openDonors(routes: Record<string, Answer> = {}): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/contacts': { body: CONTACTS_STATE },
    [donorsAt('limit=20&offset=0')]: { body: GEO_PAGE },
    ...routes,
  });
  renderWith(<AppRoutes />, '/donors');
  await screen.findByText('good.example.test');
  return recorded;
}

function rowOf(host: string): HTMLElement {
  return screen.getByText(host).closest('tr')!;
}

describe('доноры: колонка «Гео» — все пять стран', () => {
  it('первая строка — как была, под ней — остальные страны топ-5 с долями', async () => {
    await openDonors();

    const full = rowOf('good.example.test');
    expect(within(full).getByText('США · 55%')).toBeInTheDocument();
    expect(within(full).getByText('Великобритания 12% · Канада 8%')).toBeInTheDocument();
    expect(within(full).queryByText('остальные не запрашивались')).not.toBeInTheDocument();
  });

  it('неполная разбивка — одна страна и пометка, что остальные не запрашивались', async () => {
    await openDonors();

    const partial = rowOf('partial.example.test');
    expect(within(partial).getByText('США · 80%')).toBeInTheDocument();
    expect(within(partial).getByText('остальные не запрашивались')).toBeInTheDocument();
  });

  it('без разбивки — верхняя страна, как раньше, и без пометок', async () => {
    await openDonors();

    const manual = rowOf('manual.example.test');
    const cells = within(manual).getAllByRole('cell');
    const geo = cells.find((cell) => cell.textContent?.startsWith('Германия'));
    expect(geo?.textContent).toBe('Германия · 40%');
  });

  it('что в колонке и что ищет фильтр — в «i» у заголовка', async () => {
    await openDonors();
    const user = userEvent.setup();

    await user.hover(screen.getByRole('button', { name: 'Страна в топ-5' }));

    expect(
      await screen.findByText(/Фильтр находит донора, у которого страна в топ-5/),
    ).toBeInTheDocument();
  });
});

function listCalls(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/donors?'))
    .map((call: Call) => call.path);
}

describe('доноры: фильтр «Гео» — страна в топ-5', () => {
  it('предлагает страны разбивок и уходит на сервер кодом', async () => {
    const recorded = await openDonors({
      [donorsAt('geo=ca&limit=20&offset=0')]: { body: page({ rows: [FULL], total: 1 }) },
    });
    const user = userEvent.setup();
    const field = screen.getByRole('textbox', { name: 'Гео' });

    await user.click(field);
    const options = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
    expect(
      (await options.findAllByRole('option', { hidden: true })).map((o) => o.textContent),
    ).toEqual(['все · 3', 'США · 2', 'Великобритания · 1', 'Германия · 1', 'Канада · 1']);
    await user.click(options.getByRole('option', { name: 'Канада · 1', hidden: true }));

    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe('/api/donors?geo=ca&limit=20&offset=0'),
    );
    expect(await screen.findByText('США · 55%')).toBeInTheDocument();
  });

  it('пустой результат называет условие «в топ-5»', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/contacts': { body: CONTACTS_STATE },
      [donorsAt('geo=fr&limit=20&offset=0')]: {
        body: { ...GEO_PAGE, rows: [], total: 0 },
      },
    });
    renderWith(<AppRoutes />, '/donors?geo=fr');

    expect(
      await screen.findByText('Условия: страна «Франция» в топ-5. Всего доноров — 3.'),
    ).toBeInTheDocument();
  });
});

async function openCard(card: DonorFullCard): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    [`GET /api/donors/${card.id}`]: { body: card },
  });
  renderWith(<AppRoutes />, `/donors/${card.id}`);
  await screen.findByRole('heading', { name: card.host });
}

describe('карточка донора: откуда трафик', () => {
  it('неполная разбивка — страна, пометка и почему в «i»', async () => {
    await openCard({
      ...CARD,
      geo_top_share: 0.8,
      geo_breakdown: [{ country: 'us', share: 0.8 }],
      geo_partial: true,
    });
    const user = userEvent.setup();

    expect(screen.getByText('Откуда трафик')).toBeInTheDocument();
    expect(screen.getByText('США · 80%')).toBeInTheDocument();
    expect(screen.getByText('остальные не запрашивались')).toBeInTheDocument();
    await user.hover(screen.getByRole('button', { name: 'Как проверяется страна' }));
    expect(
      await screen.findByText(
        /Остальные страны топ-5 не запрашивались: до 16\.10\.2026 их не спрашивали, когда верхняя страна совпадала со страной прогона/,
      ),
    ).toBeInTheDocument();
  });

  it('полная разбивка — все страны, без пометки, «i» — как было', async () => {
    await openCard({
      ...CARD,
      geo_top_share: 0.55,
      geo_breakdown: [
        { country: 'us', share: 0.55 },
        { country: 'gb', share: 0.12 },
      ],
      geo_partial: false,
    });
    const user = userEvent.setup();

    expect(screen.getByText('Великобритания · 12%')).toBeInTheDocument();
    expect(screen.queryByText('остальные не запрашивались')).not.toBeInTheDocument();
    await user.hover(screen.getByRole('button', { name: 'Как проверяется страна' }));
    expect(await screen.findByText(/Проверяется вхождение в топ-5/)).toBeInTheDocument();
  });

  it('одна страна полной разбивки — строки нет: она та же, что в плитке «Гео»', async () => {
    await openCard({
      ...CARD,
      geo_breakdown: [{ country: 'us', share: 0.62 }],
      geo_partial: false,
    });

    expect(screen.queryByText('Откуда трафик')).not.toBeInTheDocument();
  });
});
