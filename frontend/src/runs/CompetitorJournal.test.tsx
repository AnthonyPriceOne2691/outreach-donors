/**
 * Отчёт прогона по ссылкам конкурентов: вход словами и журнал списков Ahrefs
 * (`CompetitorJournal`) — что пришло по каждому конкуренту и почём, кто пропущен и почему,
 * что выброшено до проверки и цена строки по ответу рядом со сметой.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

describe('отчёт прогона по конкурентам', () => {
  const RIVALS_RUN = {
    id: 32,
    status: 'done',
    country: 'us',
    keywords: 0,
    estimated_units: 9900,
    actual_units: 6100,
    estimate_error: -0.38,
    stats: {
      source: {
        kind: 'competitors',
        depth: 100,
        row_units: 14,
        targets: [
          { target: 'example.com', rows: 100, units: 1400, per_row: 4, kept: 87 },
          { target: 'example.net', rows: 60, units: 840, per_row: 4, kept: 41 },
        ],
        dropped: { competitor: 1, repeat: 21, donor: 10, unparsed: 0 },
        skipped: [
          {
            target: 'example.org',
            reason: 'Ahrefs не принял этот домен (код 404), хотя списки других конкурентов отдал.',
          },
        ],
      },
    },
    started_at: '2026-10-20T10:00:00Z',
    alive_at: '2026-10-20T10:40:00Z',
    hosts: 128,
    reviewed: 0,
    disagreements: 0,
    queue: {},
    reason: null,
    started_by: null,
    competitors: ['example.com', 'example.net'],
  };
  const REPORT = {
    run: RIVALS_RUN,
    thresholds: {
      min_dr: 20,
      min_org_traffic: 500,
      min_refdomains: 100,
      min_keywords: 300,
      geo_top_n: 5,
      geo_min_share: 0.2,
    },
    domains: {
      found: 128,
      excluded: 4,
      fresh: 20,
      to_check: 104,
      checked: 104,
      suitable: 50,
      unsuitable: 50,
      unchecked: 4,
      not_reached: 0,
      judge_enforced: false,
      resumes: 0,
    },
    rejected: [],
    spending: [],
    free_requests: 0,
  };

  it('называет вход, что пришло от Ahrefs, что выброшено и цену строки по ответу', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/runs/32/report': { body: REPORT } });
    renderWith(<AppRoutes />, '/runs/32');

    expect(await screen.findByText(/конкуренты example\.com, example\.net/)).toBeVisible();
    expect(screen.getByText(/Из ссылок конкурентов — 128 доменов/)).toBeVisible();
    expect(
      screen.getByText(
        'Списки Ahrefs: example.com — 100 строк за 1 400 юн., нового 87; example.net — 60 строк за 840 юн., нового 41.',
      ),
    ).toBeVisible();
    expect(
      screen.getByText('Выброшено до проверки: сам конкурент 1, повтор 21, уже донор 10.'),
    ).toBeVisible();
    expect(screen.getByText(/по ответу Ahrefs — 4 юн., по смете — 14/)).toBeVisible();
    expect(
      screen.getByText(
        'Пропущен example.org: Ahrefs не принял этот домен (код 404), хотя списки других конкурентов отдал.',
      ),
    ).toBeVisible();
  });
});
