/**
 * Гипотезы из адреса больше нет (волна 15, замечание ревью): её удалили, а номер остался в
 * ссылке коллеге, во вкладке «назад», в памяти места раздела. Раздел не падает и не показывает
 * пустоту: берёт первую гипотезу и говорит это одной строкой. Ответы — записанные (`serve()`):
 * незаписанный запрос роняет тест, поэтому видно и то, по какой гипотезе раздел спросил лидов.
 */

import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesisCard } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };

function card(id: number, name: string): HypothesisCard {
  return {
    id,
    name,
    description: null,
    created_at: '2026-10-10T09:00:00+00:00',
    leads: { new: 0, ready: 0, rejected: 0 },
    total: 0,
  };
}

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

const NO_LEADS = {
  rows: [],
  total: 0,
  page: 1,
  limit: 20,
  states: { new: 0, ready: 0, rejected: 0 },
  reasons: {},
};

function open(path: string, routes: Record<string, Answer>) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  return recorded;
}

describe('гипотезы из адреса больше нет', () => {
  it('раздел берёт первую и говорит одной строкой; лидов спрашивает по первой', async () => {
    const recorded = open('/sales?hypothesis=99', {
      'GET /api/sales/hypotheses': {
        body: { rows: [card(1, 'сайты EN'), card(2, 'Clínicas MX')], total: 2 },
      },
      // Первый запрос лидов уходит вместе со списком гипотез — по номеру из адреса.
      'GET /api/sales/leads?hypothesis=99': { body: NO_LEADS },
      'GET /api/sales/leads?hypothesis=1': { body: NO_LEADS },
    });

    expect(
      await screen.findByText(
        'Гипотезы №99 больше нет — выбрана первая: «сайты EN».',
        {},
        SCREEN_WAIT,
      ),
    ).toHaveAttribute('role', 'status');
    await waitFor(
      () =>
        expect(recorded.calls.map((call) => call.path)).toContain('/api/sales/leads?hypothesis=1'),
      SCREEN_WAIT,
    );
  });

  it('гипотез не осталось вовсе — строка так и говорит, лидов спрашивает без гипотезы', async () => {
    const recorded = open('/sales?hypothesis=99', {
      'GET /api/sales/hypotheses': { body: { rows: [], total: 0 } },
      'GET /api/sales/leads?hypothesis=99': { body: NO_LEADS },
      'GET /api/sales/leads': { body: NO_LEADS },
    });

    expect(
      await screen.findByText('Гипотезы №99 больше нет — гипотез пока нет.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    await waitFor(
      () => expect(recorded.calls.map((call) => call.path)).toContain('/api/sales/leads'),
      SCREEN_WAIT,
    );
  });

  it('номер из адреса есть в списке — строки нет', async () => {
    open('/sales?hypothesis=2', {
      'GET /api/sales/hypotheses': {
        body: { rows: [card(1, 'сайты EN'), card(2, 'Clínicas MX')], total: 2 },
      },
      'GET /api/sales/leads?hypothesis=2': { body: NO_LEADS },
    });

    await screen.findByText('Лидов пока нет.', {}, SCREEN_WAIT);
    expect(screen.queryByText(/больше нет/)).not.toBeInTheDocument();
  });
});
