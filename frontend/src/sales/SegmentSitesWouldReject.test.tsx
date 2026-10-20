/**
 * «Судья отказал бы» на экране — вариант (b) судьи сегмента (решение владельца 20.10): судья в
 * наблюдении, его отказы — своей вкладкой и плиткой, решать по ним не обязательно, «На проверку» —
 * только сомнительные, лид — только после «Принять» человека.
 *
 * Ответы сервера — записанные (`serve()`), незаписанный запрос роняет тест; домены выдуманы, ниши
 * разные, слова судьи — латиницей, японским письмом без пробелов, арабским справа налево и
 * кириллицей. Проверяется:
 * - плитка отчёта у прогона в наблюдении — числом и ссылкой на свою вкладку окна; у прогона, который
 *   режет, без таких сайтов — ни плитки, ни вкладки;
 * - на вкладке — строка «решать не обязательно», «Принять» и «Отказ»; после решения — «решено» и
 *   «Отменить решение», после отмены — снова кнопки на месте; числа вкладок — сразу;
 * - слова судьи — как есть, без приставки «Отказ: »: что это отказ, говорит полоса;
 * - «кто решил: язык» — словом в строке и в фильтре;
 * - «было → стало» пересуда называет новую полосу тем же словом.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import type { SegmentHome, SegmentMode, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import type { SegmentSite, SegmentSitesPage, SiteTab } from './SegmentSitesApi';
import { LANE_WORDS, moveWords, reasonWords, WOULD_REJECT_NOTE } from './SegmentSitesWords';

const WAIT = { timeout: 5000 };
const RUN_ID = 44;
const SITES = `/api/sales/segment/runs/${RUN_ID}/sites`;
const REPORT = `/api/sales/segment/runs/${RUN_ID}`;
const WINDOW = `Сайты прогона №${RUN_ID}`;
const WORKSHOP_WHY = 'Branchenverzeichnis für Tischler — baut selbst keine Möbel';
const PORTAL_WHY = '英会話教室の比較ポータルで、自社のレッスンはない';
const HUB_WHY = 'مجمع مدارس: قائمة بأكاديميات الآخرين وأسعارها';
const LANGUAGE_WHY = 'язык сайта — украинский, письма — русский';

function runOf(mode: SegmentMode): SegmentRunRow {
  return {
    id: RUN_ID,
    created_at: '2026-10-20T08:00:00+00:00',
    finished_at: '2026-10-20T08:09:00+00:00',
    created_by: 'seller@ours.example.test',
    country: 'de',
    language: 'ru',
    depth_pages: 2,
    keywords: 4,
    mode,
    stage: 'done',
    error: null,
    job_id: null,
  };
}

function homeOf(mode: SegmentMode): SegmentHome {
  return {
    hypothesis_id: 7,
    segment: 'Мастерские мебели на заказ: делают и продают свою мебель.',
    mode,
    countries: ['de'],
    languages: ['ru'],
    max_keywords: 100,
    max_depth: 10,
    runs: [runOf(mode)],
  };
}

const SHADOW_COUNTS: Record<SiteTab, number> = {
  all: 9,
  accept: 3,
  review: 1,
  would_reject: 3,
  reject: 1,
  pending: 1,
  changed: 0,
};
const ENFORCE_COUNTS: Record<SiteTab, number> = { ...SHADOW_COUNTS, would_reject: 0, reject: 4 };

function detailOf(mode: SegmentMode, counts: Record<SiteTab, number>): SegmentRunDetail {
  return {
    run: runOf(mode),
    hypothesis: 'мебель DE',
    keywords: ['möbel nach maß'],
    estimate_lines: [],
    summary: {
      hosts: counts.all,
      accepted: counts.accept,
      review: counts.review,
      would_reject: counts.would_reject,
      rejected: counts.reject,
      pending: counts.pending,
      no_address: 0,
      leads: 0,
    },
    lanes: {
      pending: counts.pending,
      accept: counts.accept,
      review: counts.review,
      would_reject: counts.would_reject,
      reject: counts.reject,
    },
    rejected: [],
    no_address: [],
    waiting_address: 0,
    leads: { new: 0, ready: 0, rejected: 0 },
    lead_reasons: {},
    unanswered: 0,
    report: {},
  };
}

/** Сайт, которому судья в наблюдении отказал, — так его кладёт сервер: своя полоса, слова как есть. */
function refusedOf(id: number, host: string, extra: Partial<SegmentSite> = {}): SegmentSite {
  return {
    id,
    host,
    page_url: `https://${host}/`,
    page_title: host,
    keyword: 'möbel nach maß',
    lane: 'would_reject',
    verdict: 'reject',
    decided_by: 'model',
    decided_by_user: null,
    decided_at: '2026-10-20T08:05:00+00:00',
    reason: WORKSHOP_WHY,
    quote: 'Alle Möbeltischler in Ihrer Stadt',
    unanswered: false,
    model: 'gpt-5-mini',
    undoable: false,
    judge_reason: null,
    outcome: null,
    change: null,
    ...extra,
  };
}

function acceptedBy(site: SegmentSite): SegmentSite {
  return {
    ...site,
    lane: 'accept',
    decided_by: 'human',
    decided_by_user: ADMIN.email,
    decided_at: '2026-10-20T09:00:00+00:00',
    reason: 'принял человек',
    undoable: true,
    judge_reason: site.reason,
  };
}

function pageOf(
  tab: SiteTab,
  rows: SegmentSite[],
  counts: Record<SiteTab, number> = SHADOW_COUNTS,
): SegmentSitesPage {
  return {
    run_id: RUN_ID,
    lane: tab,
    decider: null,
    page: 1,
    limit: 50,
    total: rows.length,
    lanes: counts,
    deciders: { rule: 0, model: 2, language: 1, human: 0, none: 0 },
    rows,
  };
}

const hypotheses: HypothesesView = {
  rows: [
    {
      id: 7,
      name: 'мебель DE',
      description: 'Мастерские мебели на заказ.',
      created_at: '2026-10-10T10:00:00+00:00',
      leads: { new: 0, ready: 0, rejected: 0 },
      total: 0,
    },
  ],
  total: 1,
  module_enabled: true,
};

const kb = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

function served(mode: SegmentMode, counts: Record<SiteTab, number>, routes: Routes): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  return serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: hypotheses },
    'GET /api/sales/kb': { body: kb },
    'GET /api/sales/segment?hypothesis=7': { body: homeOf(mode) },
    [`GET ${REPORT}`]: { body: detailOf(mode, counts) },
    ...routes,
  });
}

async function openWindow(tab: SiteTab, routes: Routes): Promise<Recorded> {
  const recorded = served('shadow', SHADOW_COUNTS, routes);
  renderWith(
    <AppRoutes />,
    `/sales?tab=collect&hypothesis=7&run=${RUN_ID}&sites_run=${RUN_ID}&sites=${tab}`,
  );
  await screen.findByRole('dialog', { name: WINDOW }, WAIT);
  await screen.findByRole('table', {}, WAIT);
  return recorded;
}

function bodyRows(): HTMLElement[] {
  const dialog = screen.getByRole('dialog', { name: WINDOW });
  return within(dialog)
    .getAllByRole('row')
    .filter((row) => row.closest('tbody') !== null);
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

/** Подпись плитки — с неразрывным пробелом перед «бы»: ищется по словам, а не по пробелу. */
const TILE = /^Судья отказал\s+бы\s*(\d+)$/;

describe('«Судья отказал бы»: плитка отчёта и вкладка окна', () => {
  it('у прогона в наблюдении плитка — числом и ссылкой на свою вкладку', async () => {
    const recorded = served('shadow', SHADOW_COUNTS, {
      [`GET ${SITES}?lane=would_reject&page=1`]: {
        body: pageOf('would_reject', [refusedOf(3, 'moebel-verzeichnis.example.test')]),
      },
    });
    renderWith(<AppRoutes />, `/sales?tab=collect&hypothesis=7&run=${RUN_ID}`);
    await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
    const tile = screen.getByRole('link', { name: TILE });
    expect(tile.textContent).toMatch(/3$/);
    const user = userEvent.setup();

    await user.click(tile);

    const dialog = await screen.findByRole('dialog', { name: WINDOW }, WAIT);
    expect(
      await within(dialog).findByRole('radio', { name: 'Судья отказал бы · 3' }, WAIT),
    ).toBeChecked();
    await waitFor(
      () => expect(calls(recorded, 'GET', `${SITES}?lane=would_reject&page=1`)).toHaveLength(1),
      WAIT,
    );
    expect(within(dialog).getByText(WOULD_REJECT_NOTE)).toBeInTheDocument();
  });

  it('у прогона, который режет, без отказов в наблюдении — ни плитки, ни вкладки', async () => {
    served('enforce', ENFORCE_COUNTS, {
      [`GET ${SITES}?lane=all&page=1`]: {
        body: pageOf(
          'all',
          [refusedOf(3, 'velo-news.example.test', { lane: 'reject' })],
          ENFORCE_COUNTS,
        ),
      },
    });
    renderWith(<AppRoutes />, `/sales?tab=collect&hypothesis=7&run=${RUN_ID}&sites_run=${RUN_ID}`);
    await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
    await screen.findByRole('table', {}, WAIT);
    const dialog = screen.getByRole('dialog', { name: WINDOW });

    expect(within(dialog).getByRole('radio', { name: 'Отсечены · 4' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('radio', { name: /Судья отказал бы/ })).toBeNull();
    expect(screen.queryByRole('link', { name: TILE })).toBeNull();
  });
});

describe('«Судья отказал бы»: решение по вкладке', () => {
  it('«Принять» — «решено» и отмена, после неё — снова кнопки; числа вкладок — сразу', async () => {
    const workshop = refusedOf(3, 'moebel-verzeichnis.example.test');
    const portal = refusedOf(4, 'eikaiwa-portal.example.test', {
      reason: PORTAL_WHY,
      quote: '全国の英会話教室を比較して口コミで選べる',
    });
    const hub = refusedOf(5, 'akadimiyat-sample.example.test', {
      reason: HUB_WHY,
      quote: 'قائمة بأكاديميات',
    });
    const recorded = await openWindow('would_reject', {
      [`GET ${SITES}?lane=would_reject&page=1`]: {
        body: pageOf('would_reject', [workshop, portal, hub]),
      },
      'POST /api/sales/segment/candidates/4/decision': { body: acceptedBy(portal) },
      'DELETE /api/sales/segment/candidates/4/decision': { body: portal },
    });
    const user = userEvent.setup();
    // Решать не обязательно — строкой над таблицей; кнопки — у каждой строки.
    expect(screen.getByText(WOULD_REJECT_NOTE)).toBeInTheDocument();
    for (const row of bodyRows()) {
      expect(within(row).getByRole('button', { name: 'Принять' })).toBeEnabled();
      expect(within(row).getByRole('button', { name: 'Отказ' })).toBeEnabled();
      expect(
        within(row).getByText(LANE_WORDS.would_reject, { selector: '.mantine-Badge-label' }),
      ).toBeInTheDocument();
    }
    // Слова судьи — как есть, без приставки: что это отказ, говорит полоса.
    expect(within(bodyRows()[1]!).getByText(PORTAL_WHY)).toBeInTheDocument();
    expect(within(bodyRows()[2]!).getByText(HUB_WHY)).toBeInTheDocument();

    await user.click(within(bodyRows()[1]!).getByRole('button', { name: 'Принять' }));

    const undo = await within(bodyRows()[1]!).findByRole(
      'button',
      { name: 'Отменить решение' },
      WAIT,
    );
    expect(within(bodyRows()[1]!).getByText('решено: принят')).toBeInTheDocument();
    expect(within(bodyRows()[1]!).getByText(`Судья: ${PORTAL_WHY}`)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Судья отказал бы · 2' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Приняты · 4' })).toBeInTheDocument();

    await user.click(undo);

    await waitFor(
      () => expect(within(bodyRows()[1]!).getByRole('button', { name: 'Принять' })).toBeEnabled(),
      WAIT,
    );
    expect(screen.getByRole('radio', { name: 'Судья отказал бы · 3' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Приняты · 3' })).toBeInTheDocument();
    const posted = calls(recorded, 'POST', '/api/sales/segment/candidates/4/decision');
    expect(posted.map((call) => call.body)).toEqual([{ accept: true }]);
  });

  it('«кто решил: язык» — словом в строке и в фильтре', async () => {
    const uk = refusedOf(6, 'velo-maisternia-sample.com', {
      decided_by: 'language',
      model: null,
      quote: null,
      reason: LANGUAGE_WHY,
    });
    await openWindow('would_reject', {
      [`GET ${SITES}?lane=would_reject&page=1`]: { body: pageOf('would_reject', [uk]) },
    });
    const user = userEvent.setup();
    const [row] = bodyRows();
    expect(within(row!).getByText('язык')).toBeInTheDocument();
    expect(within(row!).getByText(LANGUAGE_WHY)).toBeInTheDocument();

    await user.click(screen.getByRole('textbox', { name: 'Кто решил' }));

    expect(await screen.findByRole('option', { name: 'язык · 1' }, WAIT)).toBeInTheDocument();
  });
});

describe('слова новой полосы', () => {
  it('«было → стало» пересуда называет её тем же словом, что строка и вкладка', () => {
    expect(moveWords({ was: 'review', now: 'would_reject' })).toBe(
      'на проверке → судья отказал бы',
    );
    expect(moveWords({ was: 'would_reject', now: 'accept' })).toBe('судья отказал бы → принят');
  });

  it('слова судьи в новой полосе показываются как есть, на любом языке', () => {
    for (const words of [WORKSHOP_WHY, PORTAL_WHY, HUB_WHY, LANGUAGE_WHY]) {
      expect(reasonWords(words)).toBe(words);
    }
  });
});
