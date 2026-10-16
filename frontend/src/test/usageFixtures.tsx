/**
 * Экран «Расход» в тестах: ответ сервера и вход на экран — одни у тестов экрана
 * (`settings/UsagePage.test.tsx`) и капа Ahrefs (`settings/AhrefsCap.test.tsx`).
 */

import { screen } from '@testing-library/react';

import { AppRoutes } from '../App';
import type { Me } from '../api/types';
import { ADMIN, TOKEN_KEY } from './fixtures';
import { renderWith } from './render';
import { serve } from './server';
import type { Recorded } from './server';

/** Расход с базы разработки: юниты Ahrefs и выдача доноров, кап по умолчанию сервера. */
export const SPENDING = {
  since: '2026-09-01T00:00:00+00:00',
  articles: [
    {
      provider: 'ahrefs',
      operation: 'batch_metrics',
      units: 6416,
      amount_usd: '0',
      calls: 12,
      stage: 'donors',
    },
    {
      provider: 'serp',
      operation: 'serp_search',
      units: 0,
      amount_usd: '0.3600',
      calls: 4,
      stage: 'donors',
    },
  ],
  stages: [
    {
      stage: 'donors',
      units_by_provider: { ahrefs: 6416 },
      amount_usd: '0.3600',
      calls: 16,
    },
  ],
  crawl: { cap_usd: null, spent_usd: '0' },
  units_by_provider: { ahrefs: 6416, llm: 1361 },
  amount_by_provider: { serp: '0.3600' },
  total_units: 7777,
  total_amount: '0.3600',
  ahrefs_left: 913000,
  ahrefs_cap: 100000,
  ahrefs_cap_left: 93584,
  ahrefs_cap_limits: { min: 1, max: 1000000 },
  ahrefs_cap_changes: [] as unknown[],
  ahrefs_spent_by_us: 6416,
  ahrefs_left_error: null,
  serp_left_usd: '51.90',
  serp_spent_by_us: '0.3600',
  serp_left_error: null,
};

interface Opening {
  /** Кто вошёл; по умолчанию — админ со всеми правами. */
  me?: Me;
  /** Свои ответы сервера поверх ответа экрана: смена капа, второе чтение расхода. */
  routes?: Parameters<typeof serve>[0];
}

/** Открыть «Расход» и дождаться строки капа — по ней видно, что ответ пришёл. */
export async function openUsage(
  spending: unknown = SPENDING,
  { me = ADMIN, routes = {} }: Opening = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: me },
    'GET /api/usage': { body: spending },
    ...routes,
  });
  renderWith(<AppRoutes />, '/usage');
  await screen.findByText(/потрачено за месяц/);
  return recorded;
}
