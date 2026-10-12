/**
 * Доля ложных рекламодателей в шапке очереди (сверка ТЗ 11.10.2026) — по случайной
 * выборке «куплено» (решение Anthony 12.10.2026).
 *
 * Проверяется то, чем строка может соврать: «не мерили» — не «0%»; вывод «в допуске» —
 * только с выборки судьи Этапа 1, до неё — «мало решений для вывода»; допуск и выборка —
 * те, что назвал сервер; цвет значка — от вывода, а не от доли самой по себе.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { ScoringCheck } from '../api/advertisers';
import { ADMIN, CRAWL_ROUTES, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { falseShareLine } from './FalseShare';

function check(overrides: Partial<ScoringCheck> = {}): ScoringCheck {
  return {
    bought: { total: 400, decided: 300, write: 290, skip: 10 },
    pending: { total: 9, decided: 3, write: 1, skip: 2 },
    sample: { total: 80, decided: 12, write: 11, skip: 1 },
    false_share: 1 / 12,
    false_limit: 0.1,
    within_limit: null,
    min_decisions: 200,
    ...overrides,
  };
}

const NOT_YET = { total: 0, decided: 0, write: 0, skip: 0 };

describe('доля ложных словами', () => {
  it('мало решений выборки — число есть, вывода нет', () => {
    expect(falseShareLine(check())).toBe(
      'по случайной выборке: 12 решений, «не пишем» 1 — мало решений для вывода (12 из 200); ' +
        'допуск ТЗ — не больше 10%',
    );
  });

  it('выборки хватает — без оговорки', () => {
    const line = falseShareLine(
      check({
        sample: { total: 300, decided: 201, write: 190, skip: 11 },
        false_share: 11 / 201,
        within_limit: true,
      }),
    );
    expect(line).toBe(
      'по случайной выборке: 201 решение, «не пишем» 11; допуск ТЗ — не больше 10%',
    );
  });

  it('в выборке есть, но не решали — мерить нечем, а не «0%»', () => {
    const line = falseShareLine(
      check({ sample: { ...NOT_YET, total: 7 }, false_share: null, within_limit: null }),
    );
    expect(line).toBe('мерить нечем: по выборке решений пока нет; допуск ТЗ — не больше 10%');
  });

  it('выборка пуста — так и сказано', () => {
    const line = falseShareLine(check({ sample: NOT_YET, false_share: null }));
    expect(line).toBe(
      'мерить нечем: в случайной выборке «куплено» пока нет; допуск ТЗ — не больше 10%',
    );
  });
});

async function openWith(scoring: ScoringCheck | undefined) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/advertisers/niche?page=1': {
      body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
    },
    'GET /api/advertisers': {
      body: { rows: [], waiting: 0, counts: { bought: 400 }, ...(scoring ? { scoring } : {}) },
    },
  });
  renderWith(<AppRoutes />, '/advertisers');
  await screen.findByText(/Спорных нет/);
}

describe('доля ложных в шапке очереди', () => {
  it('за допуском на полной выборке — розой, с десятой: 12,5% не «13%»', async () => {
    await openWith(
      check({
        sample: { total: 300, decided: 200, write: 175, skip: 25 },
        false_share: 0.125,
        within_limit: false,
      }),
    );

    const badge = screen.getByText('12,5%').closest('.mantine-Badge-root');
    expect(badge?.getAttribute('style')).toContain('red');
    expect(screen.getByText('Ложных в «куплено»:')).toBeInTheDocument();
  });

  it('мало решений — значок серый, хоть доля и выше допуска', async () => {
    await openWith(
      check({ sample: { total: 80, decided: 4, write: 2, skip: 2 }, false_share: 0.5 }),
    );

    const badge = screen.getByText('50%').closest('.mantine-Badge-root');
    expect(badge?.getAttribute('style')).toContain('gray');
    expect(screen.getByText(/мало решений для вывода \(4 из 200\)/)).toBeInTheDocument();
  });

  it('сервер старше экрана — строки нет, очередь на месте', async () => {
    await openWith(undefined);

    expect(screen.queryByText('Ложных в «куплено»:')).toBeNull();
  });
});
