/**
 * Заготовки экрана прогона для тестов: смета, которая помещается, страница
 * истории и сам экран с записанными ответами сервера.
 *
 * Отдельным файлом, потому что экран проверяют два набора: сам экран
 * (`runs/RunPage.test.tsx`) и смета на нём (`runs/Estimate.test.tsx`). Две копии
 * заготовок разошлись бы при первой правке ответа сметы.
 */

import { screen } from '@testing-library/react';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from './fixtures';
import { renderWith } from './render';
import type { Recorded } from './server';
import { serve } from './server';

/** Смета, которая помещается в бюджет. */
export const FITS = {
  keywords: 2,
  depth_pages: 1,
  expected_results: 20,
  expected_domains: 3,
  units_screen: 50,
  units_metrics: 72,
  units_by_country: 55,
  units_total: 177,
  units_left: 100000,
  units_cap: 100000,
  units_spent_this_month: 6416,
  cap_left: 93584,
  run_ceiling: null,
  budget: 93584,
  affordable: true,
  shortfall: 0,
  serp_cost_usd: 0.0012,
  // Три домена на двадцать результатов — доля, которой посчитана смета.
  unique_share: 0.15,
};

/** Ответ истории: страница, её размер и сколько прогонов всего. */
export function history(runs: unknown[], extra: Record<string, unknown> = {}) {
  return {
    body: { runs, total: runs.length, page: 1, limit: 10, workers: 1, queued: 0, ...extra },
  };
}

/** Экран прогона с записанными ответами; поверх — ответы теста. */
export async function openRun(
  routes: Record<string, unknown> = {},
  path = '/run',
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/runs/countries': { body: ['us', 'de'] },
    'GET /api/runs?page=1': history([]),
    'GET /api/keywords/yield?country=us': { body: [] },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, path);
  await screen.findByLabelText('Ключевые слова');
  return recorded;
}
