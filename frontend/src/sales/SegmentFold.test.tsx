/**
 * «Сбор базы» компактно (волна 15): форма «Новый прогон» свёрнута в кнопку, если у гипотезы уже
 * есть прогоны, и раскрыта без них; запуск её сворачивает; описание сегмента — три строки и
 * «показать целиком», когда в три строки не помещается (мерит браузер — здесь высоты подменены).
 *
 * Ответы сервера — записанные (`serve()`), незаписанный запрос роняет тест; ниша и домены
 * выдуманы.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call } from '../test/server';
import { HELP_QUIET } from './segmentHelpFixtures';
import type { SegmentEstimate, SegmentHome, SegmentRunDetail, SegmentRunRow } from './segmentApi';

const SCREEN_WAIT = { timeout: 5000 };
const SEGMENT_TEXT =
  'Zahnarztpraxen mit eigener Website: behandeln selbst, nehmen Termine online an und nennen ' +
  'ihre Leistungen. Nicht: Bewertungsportale, Verzeichnisse, Dentallabore ohne Praxis.';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 4,
      name: 'Praxen DE',
      description: SEGMENT_TEXT,
      created_at: '2026-10-04T10:00:00+00:00',
      leads: { new: 0, ready: 0, rejected: 0 },
      total: 0,
    },
  ],
  total: 1,
  module_enabled: true,
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

const RUN: SegmentRunRow = {
  id: 52,
  created_at: '2026-10-15T08:00:00+00:00',
  finished_at: '2026-10-15T08:12:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'de',
  language: 'en',
  depth_pages: 1,
  keywords: 1,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: 'sales-segment-52',
};

const HOME: SegmentHome = {
  hypothesis_id: 4,
  segment: SEGMENT_TEXT,
  mode: 'shadow',
  countries: ['de', 'us'],
  languages: ['ru', 'en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [RUN],
};

const DETAIL: SegmentRunDetail = {
  run: RUN,
  hypothesis: 'Praxen DE',
  keywords: ['zahnarzt berlin'],
  estimate_lines: ['смета'],
  summary: { hosts: 0, accepted: 0, review: 0, rejected: 0, no_address: 0, leads: 0 },
  lanes: {},
  rejected: [],
  no_address: [],
  waiting_address: 0,
  leads: {},
  lead_reasons: {},
  unanswered: 0,
  report: {},
};

const ESTIMATE: SegmentEstimate = {
  keywords: ['zahnarzt berlin'],
  excluded: [],
  keyword_lines: { 'zahnarzt berlin': 1 },
  rejected: {},
  notes: {},
  segment: SEGMENT_TEXT,
  digest: 'seen-praxen',
  country: 'de',
  language: 'en',
  depth_pages: 1,
  mode: 'shadow',
  serp_mode: 'live',
  serp_page_usd: 0.002,
  serp_wait_s: 10,
  results: 10,
  share: 0.5,
  share_runs: 1,
  hosts: 5,
  known: 0,
  sites: 5,
  serp_usd: 0.002,
  tokens: 5500,
  tokens_usd: 0.003,
  tokens_left: null,
  hunter_searches: 0,
  hunter_checks: 0,
  lines: ['Выдача — быстрая (Live): ключей 1 × страниц 1.'],
  refusal: null,
};

async function openTab(
  home: SegmentHome,
  routes: Record<string, Answer | ((call: Call) => Answer)> = {},
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    ...HELP_QUIET,
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/segment?hypothesis=4': { body: home },
    'GET /api/sales/segment/runs/52': { body: DETAIL },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=collect');
  await screen.findByText('Сегмент гипотезы', {}, SCREEN_WAIT);
  return recorded;
}

/** Высота описания сегмента — как у браузера: текст выше трёх строк. */
function tallSegment(): void {
  vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(function (this: Element) {
    return this.classList.contains('segmentText') ? 120 : 0;
  });
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockImplementation(function (this: Element) {
    return this.classList.contains('segmentText') ? 60 : 0;
  });
}

describe('сбор базы компактно: форма свёрнута, описание — три строки', () => {
  it('у гипотезы с прогонами форма свёрнута в кнопку «Новый прогон» и раскрывается ею', async () => {
    await openTab(HOME);
    const user = userEvent.setup();
    const toggle = screen.getByRole('button', { name: 'Новый прогон' });

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'Посчитать смету' })).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Ключи' })).toBeNull();
    expect(screen.getByText('ключи, рынок и глубина — смета до запуска')).toBeInTheDocument();

    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(
      await screen.findByRole('button', { name: 'Посчитать смету' }, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Ключи' })).toBeInTheDocument();
  });

  it('прогонов нет — форма раскрыта, и сворачивать её нечем', async () => {
    await openTab({ ...HOME, runs: [] });

    expect(screen.getByRole('button', { name: 'Посчитать смету' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Новый прогон' })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Новый прогон' })).toBeInTheDocument();
  });

  it('запущенный прогон сворачивает форму', async () => {
    await openTab(HOME, {
      'POST /api/sales/segment/estimate': { body: ESTIMATE },
      'POST /api/sales/segment/runs': { body: { run_id: 53, job_id: 'sales-segment-53' } },
      'GET /api/sales/segment/runs/53': { body: { ...DETAIL, run: { ...RUN, id: 53 } } },
    });
    const user = userEvent.setup();
    const toggle = screen.getByRole('button', { name: 'Новый прогон' });
    await user.click(toggle);
    await user.click(await screen.findByRole('textbox', { name: 'Ключи' }, SCREEN_WAIT));
    await user.paste('zahnarzt berlin');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    const start = await screen.findByRole('button', { name: 'Запустить прогон' }, SCREEN_WAIT);
    await waitFor(() => expect(start).toBeEnabled(), SCREEN_WAIT);

    await user.click(start);

    await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'false'), SCREEN_WAIT);
  });

  it('описание не помещается в три строки — «показать целиком» и «свернуть»', async () => {
    tallSegment();
    await openTab(HOME);
    const user = userEvent.setup();
    const text = screen.getByText(SEGMENT_TEXT);
    const more = screen.getByRole('button', { name: 'показать целиком' });

    expect(text).toHaveAttribute('data-line-clamp');
    expect(more).toHaveAttribute('aria-expanded', 'false');

    await user.click(more);

    expect(text).not.toHaveAttribute('data-line-clamp');
    const less = screen.getByRole('button', { name: 'свернуть' });
    expect(less).toHaveAttribute('aria-expanded', 'true');
    // Правка описания — как раньше: кнопка «Изменить» на месте.
    expect(screen.getByRole('button', { name: 'Изменить' })).toBeInTheDocument();
  });

  it('описание помещается — кнопки «показать целиком» нет', async () => {
    await openTab(HOME);

    expect(screen.getByText(SEGMENT_TEXT)).toHaveAttribute('data-line-clamp');
    expect(screen.queryByRole('button', { name: 'показать целиком' })).toBeNull();
  });
});
