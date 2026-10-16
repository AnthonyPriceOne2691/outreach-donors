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

/** Ключ Ahrefs по снимку: свой лимит, цикл от обнуления 21-го, кап — весь лимит ключа.
 *  Числа выдуманные: лимиты настоящего ключа в публичный репозиторий не идут. */
export const AHREFS_KEY = {
  size: 50000,
  own_limit: true,
  key_used: 15000,
  workspace_limit: 500000,
  workspace_used: 120000,
  asked_at: '2026-10-16T11:05:00+00:00',
  cycle_start: '2026-10-21T00:00:00+00:00',
  next_reset: '2026-11-21T00:00:00+00:00',
  by_key: true,
  spent_by_us: 6416,
  cap: 50000,
  chosen: null as number | null,
  above_key: false,
  left: 35000,
  binding: 'key' as string | null,
  used_percent: 30,
  unknown: null as string | null,
  limits: { min: 1, max: 50000 } as { min: number; max: number } | null,
  changes: [] as unknown[],
};

/** Расход с базы разработки: юниты Ahrefs и выдача доноров, кап — весь лимит ключа. */
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
  ahrefs_left: 35000,
  ahrefs_key: AHREFS_KEY,
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

/** Открыть «Расход» и дождаться строки нашего капа — она есть всегда, и по ней видно, что
 *  ответ пришёл. */
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
  await screen.findByText(/^Наш кап:/);
  return recorded;
}
