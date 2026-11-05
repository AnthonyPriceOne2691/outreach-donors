/**
 * Доля ложных рекламодателей и ошибка базы «писать всем спорным» в шапке очереди (сверка ТЗ
 * 11.10.2026, замер спорных 02.11.2026) — по случайной выборке.
 *
 * Проверяется то, чем строка может соврать: «не мерили» — не «0%»; вывод — по границе от
 * сервера, а не по доле самой по себе: ровно 10 % на горстке решений — не «в допуске»;
 * «сколько ещё решить» больше нерешённых в выборке — так и сказано; цвет значка — от вывода.
 */

import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { SampleShare, ScoringCheck } from '../api/advertisers';
import { ADMIN, CRAWL_ROUTES, reviewQueue, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { disputedLine, falseShareLine } from './FalseShare';

function share(overrides: Partial<SampleShare> = {}): SampleShare {
  return {
    total: 80,
    decided: 12,
    write: 11,
    skip: 1,
    left: 68,
    aside: 0,
    share: 1 / 12,
    low: 0.0043,
    high: 0.339,
    within: 'unproven',
    to_go: 34,
    ...overrides,
  };
}

const EMPTY = share({
  total: 0,
  decided: 0,
  write: 0,
  skip: 0,
  left: 0,
  share: null,
  low: null,
  high: null,
  within: 'unmeasured',
  to_go: 29,
});

function check(overrides: Partial<ScoringCheck> = {}): ScoringCheck {
  return {
    bought: { total: 400, decided: 300, write: 290, skip: 10 },
    pending: { total: 600, decided: 40, write: 30, skip: 10 },
    bought_sample: share(),
    pending_sample: EMPTY,
    false_limit: 0.1,
    ...overrides,
  };
}

describe('доля ложных словами', () => {
  it('вывода нет — граница с обеих сторон и сколько ещё решить', () => {
    expect(falseShareLine(check())).toBe(
      'по случайной выборке: 12 решений, «не пишем» 1; с запасом на случай — от 0,4% до 33,9%: ' +
        'вывода нет; для «в допуске» нужно ещё 34 решения без «не пишем»; допуск ТЗ — не больше 10%',
    );
  });

  it('в допуске — верхняя граница не выше допуска', () => {
    const line = falseShareLine(
      check({
        bought_sample: share({
          decided: 29,
          write: 29,
          skip: 0,
          share: 0,
          low: 0,
          high: 0.0982,
          within: 'within',
          to_go: null,
        }),
      }),
    );
    expect(line).toBe(
      'по случайной выборке: 29 решений, «не пишем» 0; с запасом на случай — не больше 9,8%: в ' +
        'допуске; допуск ТЗ — не больше 10%',
    );
  });

  it('вне допуска — нижняя граница выше него, и «сколько ещё» не утешает', () => {
    const line = falseShareLine(
      check({
        bought_sample: share({
          decided: 30,
          write: 22,
          skip: 8,
          share: 8 / 30,
          low: 0.1349,
          high: 0.4224,
          within: 'outside',
          to_go: null,
        }),
      }),
    );
    expect(line).toBe(
      'по случайной выборке: 30 решений, «не пишем» 8; с запасом на случай — не меньше 13,5%: вне ' +
        'допуска; допуск ТЗ — не больше 10%',
    );
  });

  it('три решения, а для вывода нужно больше, чем осталось в выборке', () => {
    const line = falseShareLine(
      check({
        bought_sample: share({
          total: 30,
          decided: 3,
          write: 2,
          skip: 1,
          left: 27,
          share: 1 / 3,
          low: 0.017,
          high: 0.8647,
          to_go: 43,
        }),
      }),
    );
    expect(line).toBe(
      'по случайной выборке: 3 решения, «не пишем» 1; с запасом на случай — от 1,7% до 86,5%: ' +
        'вывода нет; для «в допуске» нужно ещё 43 решения без «не пишем», а в выборке осталось ' +
        'строк 27: вывод придёт с новыми обходами; допуск ТЗ — не больше 10%',
    );
  });

  it('в выборке есть, но не решали — мерить нечем, а не «0%»', () => {
    const line = falseShareLine(check({ bought_sample: { ...EMPTY, total: 35, left: 35 } }));
    expect(line).toBe(
      'мерить нечем: по выборке решений по порядку пока нет; для «в допуске» нужно ещё 29 решений ' +
        'без «не пишем»; допуск ТЗ — не больше 10%',
    );
  });

  it('выборка пуста — так и сказано', () => {
    expect(falseShareLine(check({ bought_sample: EMPTY }))).toBe(
      'мерить нечем: в случайной выборке «куплено» пока нет; допуск ТЗ — не больше 10%',
    );
  });
});

describe('база «писать всем спорным» словами', () => {
  it('вне допуска — писать всем спорным нельзя, с нижней границей', () => {
    const line = disputedLine(
      check({
        pending_sample: share({
          total: 120,
          decided: 31,
          write: 22,
          skip: 9,
          left: 89,
          share: 9 / 31,
          low: 0.163,
          high: 0.448,
          within: 'outside',
          to_go: null,
        }),
      }),
    );
    expect(line).toBe(
      'по случайной выборке спорных: 31 решение, «не пишем» 9; с запасом на случай — не меньше ' +
        '16,3%: писать всем спорным нельзя — ложных за допуском',
    );
  });

  it('в выборке спорных нет решений — мерить нечем', () => {
    expect(disputedLine(check({ pending_sample: { ...EMPTY, total: 120, left: 120 } }))).toBe(
      'мерить нечем: по выборке спорных решений по порядку пока нет; для «в допуске» нужно ещё 29 ' +
        'решений без «не пишем»',
    );
    expect(disputedLine(check())).toBe('мерить нечем: в случайной выборке спорных пока нет');
  });

  it('решения не по порядку выборки — числом, а не долей: «в допуске» по верху очереди не выйдет', () => {
    const line = disputedLine(
      check({ pending_sample: { ...EMPTY, total: 150, left: 150, aside: 30 } }),
    );
    expect(line).toBe(
      'мерить нечем: по выборке спорных решений по порядку пока нет; для «в допуске» нужно ещё 29 ' +
        'решений без «не пишем»; ещё 30 решений — не по порядку выборки, в долю войдут, когда до ' +
        'них дойдёт очередь',
    );
  });
});

const STRATA = {
  points: [
    { key: '2', title: '2', total: 10, decided: 4, write: 3, skip: 1 },
    { key: '3', title: '3', total: 25, decided: 9, write: 9, skip: 0 },
  ],
  features: [
    { key: 'anchor', title: 'коммерческий анкор', total: 20, decided: 6, write: 5, skip: 1 },
  ],
  niches: [{ key: 'unknown', title: 'неизвестна', total: 35, decided: 13, write: 12, skip: 1 }],
};

async function openWith(scoring: ScoringCheck | undefined) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/advertisers/niche?page=1': {
      body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
    },
    'GET /api/advertisers': {
      body: reviewQueue({ counts: { bought: 400 }, ...(scoring ? { scoring } : {}) }),
    },
    'GET /api/advertisers/pending/strata': { body: STRATA },
  });
  renderWith(<AppRoutes />, '/advertisers?view=review');
  await screen.findByText(/Спорных нет/);
  return recorded;
}

describe('доля ложных в шапке очереди', () => {
  it('вне допуска — розой, с десятой: 12,5% не «13%»', async () => {
    await openWith(
      check({
        bought_sample: share({
          decided: 200,
          write: 175,
          skip: 25,
          share: 0.125,
          low: 0.0894,
          high: 0.1704,
          within: 'outside',
          to_go: null,
        }),
      }),
    );

    const badge = screen.getByText('12,5%').closest('.mantine-Badge-root');
    expect(badge?.getAttribute('style')).toContain('red');
    expect(screen.getByText('Ложных в «куплено»:')).toBeInTheDocument();
    expect(screen.getByText('«Не пишем» среди спорных:')).toBeInTheDocument();
  });

  it('вывода нет — значок серый, хоть доля и выше допуска', async () => {
    await openWith(check({ bought_sample: share({ decided: 4, write: 2, skip: 2, share: 0.5 }) }));

    const badge = screen.getByText('50%').closest('.mantine-Badge-root');
    expect(badge?.getAttribute('style')).toContain('gray');
  });

  it('сервер старше экрана — строки нет, очередь на месте', async () => {
    await openWith(undefined);

    expect(screen.queryByText('Ложных в «куплено»:')).toBeNull();
  });
});

describe('разбивка выборки спорных', () => {
  it('спрашивается, только когда её раскрыли, и называет баллы словами', async () => {
    const recorded = await openWith(check());
    const asked = () => recorded.calls.filter((call) => call.path.endsWith('/pending/strata'));

    expect(asked()).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Разбивка выборки спорных' }));

    expect(await screen.findByText('2 балла')).toBeInTheDocument();
    expect(screen.getByText('3 балла')).toBeInTheDocument();
    expect(screen.getByText('коммерческий анкор')).toBeInTheDocument();
    expect(screen.getByText('неизвестна')).toBeInTheDocument();
    expect(asked()).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Скрыть разбивку выборки' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });
});
