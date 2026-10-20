/**
 * «Отменить решение» и «Отказ:» в окне «Сайты прогона» (тест продаж 20.10).
 *
 * Ответы сервера — записанные (`serve()`), незаписанный запрос роняет тест; домены выдуманы. Правило
 * одно для любого сайта, ниши и языка: ниши разные, слова судьи — в четырёх письменностях (латиница,
 * японский без пробелов, арабский справа налево, кириллица). Проверяется:
 * - на «На проверку» решённая строка — «решено: …» и «Отменить решение»; после отмены — снова
 *   «Принять» и «Отказ», числа вкладок и отчёт — без перезагрузки;
 * - на других вкладках отмена — под «кто решил»; отменённая строка стоит на месте и решается там же;
 * - кнопки нет, когда сервер говорит, что отменить нельзя (лид заведён); отказ сервера — его словами;
 * - отказ судьи в наблюдении — «Отказ: …» и у старой причины («судья в наблюдении отрезал бы: …»),
 *   и в «стало бы», и в словах судьи у решения человека; сайт с ним — «на проверке», «решает человек»;
 * - функция показа меняет только нашу приписку в начале, слова судьи после неё — как есть.
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
import type { SegmentHome, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import type { SegmentSite, SegmentSitesPage, SiteTab } from './SegmentSitesApi';
import { reasonWords } from './SegmentSitesWords';

const WAIT = { timeout: 5000 };
const RUN_ID = 31;
const SITES = `/api/sales/segment/runs/${RUN_ID}/sites`;
const REPORT = `/api/sales/segment/runs/${RUN_ID}`;
const SELLER = 'seller@ours.example.test';
/** Наша приписка до 20.10 и теперь; слова судьи после неё — на любом языке, как их дала модель. */
const BEFORE = 'судья в наблюдении отрезал бы: ';
const WORKSHOP_WHY = 'Branchenverzeichnis für Tischler — baut selbst keine Möbel';
const PORTAL_WHY = '英会話教室の比較ポータルで、自社のレッスンはない';
const HUB_WHY = 'مجمع مدارس: قائمة بأكاديميات الآخرين وأسعارها';
const OLD_CUT = `${BEFORE}${WORKSHOP_WHY}`;
const CUT = `Отказ: ${WORKSHOP_WHY}`;
const PORTAL = `Отказ: ${PORTAL_WHY}`;
const LEAD_MADE = 'лид уже заведён — отклоните его во вкладке «emails»';

const run: SegmentRunRow = {
  id: RUN_ID,
  created_at: '2026-10-20T08:00:00+00:00',
  finished_at: '2026-10-20T08:09:00+00:00',
  created_by: SELLER,
  country: 'de',
  language: 'en',
  depth_pages: 2,
  keywords: 4,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: null,
};

const home: SegmentHome = {
  hypothesis_id: 7,
  segment: 'Мастерские мебели на заказ: делают и продают свою мебель.',
  mode: 'shadow',
  countries: ['de'],
  languages: ['en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [run],
};

const counts: Record<SiteTab, number> = {
  all: 9,
  accept: 3,
  review: 2,
  reject: 3,
  pending: 1,
  changed: 0,
};

function detailOf(review: number): SegmentRunDetail {
  return {
    run,
    hypothesis: 'мебель DE',
    keywords: ['möbel nach maß'],
    estimate_lines: [],
    summary: { hosts: 9, accepted: 3, review, rejected: 3, no_address: 0, leads: 0 },
    lanes: { pending: 1, accept: 3, review, reject: 3 },
    rejected: [],
    no_address: [],
    waiting_address: 0,
    leads: { new: 0, ready: 0, rejected: 0 },
    lead_reasons: {},
    unanswered: 0,
    report: {},
  };
}

/** Сайт на проверке: судья в наблюдении отказал — так его кладёт сервер. */
function reviewOf(id: number, host: string, extra: Partial<SegmentSite> = {}): SegmentSite {
  return {
    id,
    host,
    page_url: `https://${host}/`,
    page_title: host,
    keyword: 'möbel nach maß',
    lane: 'review',
    verdict: 'reject',
    decided_by: 'model',
    decided_by_user: null,
    decided_at: '2026-10-20T08:05:00+00:00',
    reason: CUT,
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

/** Ответ сервера на решение: строка списка — решил человек, слова судьи рядом. */
function decidedBy(site: SegmentSite, accept: boolean): SegmentSite {
  return {
    ...site,
    lane: accept ? 'accept' : 'reject',
    decided_by: 'human',
    decided_by_user: ADMIN.email,
    decided_at: '2026-10-20T09:00:00+00:00',
    reason: accept ? 'принял человек' : 'отказал человек',
    undoable: true,
    judge_reason: site.reason,
  };
}

function pageOf(tab: SiteTab, rows: SegmentSite[], lanes = counts): SegmentSitesPage {
  return {
    run_id: RUN_ID,
    lane: tab,
    decider: null,
    page: 1,
    limit: 50,
    total: rows.length,
    lanes,
    deciders: { rule: 0, model: 6, human: 2, none: 1 },
    rows,
  };
}

const hypotheses: HypothesesView = {
  rows: [
    {
      id: 7,
      name: 'мебель DE',
      description: home.segment,
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

async function open(tab: SiteTab, routes: Routes): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: hypotheses },
    'GET /api/sales/kb': { body: kb },
    'GET /api/sales/segment?hypothesis=7': { body: home },
    [`GET ${REPORT}`]: { body: detailOf(2) },
    ...routes,
  });
  renderWith(
    <AppRoutes />,
    `/sales?tab=collect&hypothesis=7&run=${RUN_ID}&sites_run=${RUN_ID}&sites=${tab}`,
  );
  await screen.findByRole('dialog', { name: `Сайты прогона №${RUN_ID}` }, WAIT);
  await screen.findByRole('table', {}, WAIT);
  return recorded;
}

/** Строки тела таблицы окна: над ними — подписи колонок и строка фильтров. */
function bodyRows(): HTMLElement[] {
  const dialog = screen.getByRole('dialog', { name: `Сайты прогона №${RUN_ID}` });
  return within(dialog)
    .getAllByRole('row')
    .filter((row) => row.closest('tbody') !== null);
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

describe('«Отменить решение»', () => {
  it('на «На проверку»: «решено» и отмена; после неё — снова «Принять» и «Отказ», числа — сразу', async () => {
    const workshop = reviewOf(3, 'moebel-verzeichnis.example.test');
    const portal = reviewOf(4, 'eikaiwa-portal.example.test', {
      reason: PORTAL,
      quote: '全国の英会話教室を比較して口コミで選べる',
    });
    const recorded = await open('review', {
      [`GET ${SITES}?lane=review&page=1`]: { body: pageOf('review', [workshop, portal]) },
      'POST /api/sales/segment/candidates/3/decision': { body: decidedBy(workshop, false) },
      'DELETE /api/sales/segment/candidates/3/decision': { body: workshop },
    });
    const user = userEvent.setup();

    await user.click(within(bodyRows()[0]!).getByRole('button', { name: 'Отказ' }));
    const undo = await within(bodyRows()[0]!).findByRole(
      'button',
      { name: 'Отменить решение' },
      WAIT,
    );
    expect(within(bodyRows()[0]!).getByText('решено: отсечён')).toBeInTheDocument();
    // С чем не согласился человек — словами судьи рядом с его решением.
    expect(within(bodyRows()[0]!).getByText(`Судья: ${CUT}`)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'На проверку · 1' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Отсечены · 4' })).toBeInTheDocument();

    await user.click(undo);

    const [top, bottom] = bodyRows();
    await waitFor(
      () => expect(within(top!).getByRole('button', { name: 'Принять' })).toBeEnabled(),
      WAIT,
    );
    expect(within(top!).getByRole('button', { name: 'Отказ' })).toBeEnabled();
    expect(within(top!).queryByRole('button', { name: 'Отменить решение' })).toBeNull();
    expect(within(top!).getByText(CUT)).toBeInTheDocument();
    expect(top).toHaveTextContent(workshop.host);
    expect(bottom).toHaveTextContent(portal.host);
    expect(screen.getByRole('radio', { name: 'На проверку · 2' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Отсечены · 3' })).toBeInTheDocument();
    expect(calls(recorded, 'DELETE', '/api/sales/segment/candidates/3/decision')).toHaveLength(1);
    // Отчёт перечитан после решения и после отмены: плитки и «На проверку N» — без перезагрузки.
    await waitFor(() => expect(calls(recorded, 'GET', REPORT).length).toBeGreaterThan(2), WAIT);
  });

  it('на «Приняты»: отмена — под «кто решил», строка стоит на месте и решается там же', async () => {
    const school = reviewOf(5, 'madrasat-lughat.example.test', {
      verdict: 'review',
      reason: 'غير واضح من النص: مدرسة لغات أم مدونة معلم',
      quote: 'تعلم اللغة الإنجليزية مع دروسنا',
    });
    const accepted = { ...decidedBy(school, true), decided_by_user: SELLER };
    const shop = reviewOf(6, 'velo-shop.example.test', {
      lane: 'accept',
      verdict: 'accept',
      reason: 'магазин велосипедов',
    });
    const recorded = await open('accept', {
      [`GET ${SITES}?lane=accept&page=1`]: { body: pageOf('accept', [accepted, shop]) },
      'DELETE /api/sales/segment/candidates/5/decision': { body: school },
      'POST /api/sales/segment/candidates/5/decision': { body: decidedBy(school, false) },
    });
    const user = userEvent.setup();
    const [human, model] = bodyRows();
    expect(within(model!).queryByRole('button')).toBeNull();
    expect(within(human!).getByText('человек · seller')).toBeInTheDocument();

    await user.click(within(human!).getByRole('button', { name: 'Отменить решение' }));

    await waitFor(
      () =>
        expect(
          within(bodyRows()[0]!).getByText('на проверке', { selector: '.mantine-Badge-label' }),
        ).toBeInTheDocument(),
      WAIT,
    );
    const [back] = bodyRows();
    expect(back).toHaveTextContent(school.host);
    expect(within(back!).getByText('модель')).toBeInTheDocument();
    expect(within(back!).getByText(school.reason!)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Приняты · 2' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'На проверку · 3' })).toBeInTheDocument();

    await user.click(within(back!).getByRole('button', { name: 'Отказ' }));

    await waitFor(
      () =>
        expect(
          within(bodyRows()[0]!).getByRole('button', { name: 'Отменить решение' }),
        ).toBeInTheDocument(),
      WAIT,
    );
    expect(within(bodyRows()[0]!).getByText('отсечён', { selector: '.mantine-Badge-label' }));
    expect(screen.getByRole('radio', { name: 'Отсечены · 4' })).toBeInTheDocument();
    const posted = calls(recorded, 'POST', '/api/sales/segment/candidates/5/decision');
    expect(posted.map((call) => call.body)).toEqual([{ accept: false }]);
  });

  it('лид уже заведён — кнопки нет; завели посреди — отказ словами сервера, строка прежняя', async () => {
    const withLead = {
      ...decidedBy(reviewOf(7, 'tischlerei-mit-lead.example.test'), true),
      undoable: false,
      outcome: {
        kind: 'lead' as const,
        words: 'лид заведён',
        lead_id: 12,
        lead_status: 'ready',
        lead_reason: null,
      },
    };
    const racing = decidedBy(reviewOf(8, 'werkstatt-sample.example.test'), true);
    await open('accept', {
      [`GET ${SITES}?lane=accept&page=1`]: { body: pageOf('accept', [withLead, racing]) },
      'DELETE /api/sales/segment/candidates/8/decision': {
        status: 409,
        body: { detail: LEAD_MADE },
      },
    });
    const user = userEvent.setup();
    const [lead, other] = bodyRows();
    expect(within(lead!).queryByRole('button', { name: 'Отменить решение' })).toBeNull();

    await user.click(within(other!).getByRole('button', { name: 'Отменить решение' }));

    expect(await screen.findByText(LEAD_MADE, {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText('Решение не отменено')).toBeInTheDocument();
    expect(
      within(bodyRows()[1]!).getByRole('button', { name: 'Отменить решение' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Приняты · 3' })).toBeChecked();
  });
});

describe('«Отказ:» вместо «судья в наблюдении отрезал бы:»', () => {
  it('старая и новая причина — одним видом; «стало бы» и слова судьи у решения — тоже', async () => {
    const old = reviewOf(11, 'alt-verzeichnis.example.test', { reason: OLD_CUT });
    const fresh = reviewOf(12, 'neu-verzeichnis.example.test');
    const human = {
      ...decidedBy(reviewOf(13, 'eikaiwa-portal.example.test', { reason: PORTAL }), true),
      judge_reason: `${BEFORE}${PORTAL_WHY}`,
    };
    const would = reviewOf(14, 'academias-hub.example.test', {
      lane: 'accept',
      verdict: 'accept',
      reason: 'magazyn rowerów — własny sklep',
      change: {
        was: 'accept',
        now: 'review',
        would: true,
        reason: `${BEFORE}${HUB_WHY}`,
        quote: null,
      },
    });
    await open('all', {
      [`GET ${SITES}?lane=all&page=1`]: { body: pageOf('all', [old, fresh, human, would]) },
    });
    const [first, second, third, fourth] = bodyRows();

    expect(within(first!).getByText(CUT)).toBeInTheDocument();
    expect(within(second!).getByText(CUT)).toBeInTheDocument();
    expect(within(third!).getByText(`Судья: ${PORTAL}`)).toBeInTheDocument();
    expect(within(fourth!).getByText(`стало бы: Отказ: ${HUB_WHY}`)).toBeInTheDocument();
    expect(screen.queryByText(/отрезал бы/)).toBeNull();
    // Отказ судьи — мнение: сайт «на проверке», и подпись полосы говорит, что решает человек.
    for (const row of [first!, second!]) {
      expect(within(row).getByText('на проверке', { selector: '.mantine-Badge-label' }));
      expect(within(row).getByText('решает человек')).toBeInTheDocument();
    }
    expect(within(fourth!).queryByText('решает человек')).toBeNull();
  });
});

describe('функция показа причины', () => {
  it('меняет только нашу приписку в начале — слова судьи после неё на любом языке как есть', () => {
    const said = [
      WORKSHOP_WHY,
      PORTAL_WHY,
      HUB_WHY,
      'справочник клиник — списки чужих врачей',
      'ร้านค้าออนไลน์ของตัวเอง',
    ];
    for (const words of said) {
      expect(reasonWords(`${BEFORE}${words}`)).toBe(`Отказ: ${words}`);
      expect(reasonWords(`Отказ: ${words}`)).toBe(`Отказ: ${words}`);
      // Своё слово модели не трогается — ни «отрезал бы» в середине, ни чужое начало.
      expect(reasonWords(words)).toBe(words);
      expect(reasonWords(`${words}; ${BEFORE}x`)).toBe(`${words}; ${BEFORE}x`);
    }
    expect(reasonWords('судья в наблюдении: отказ не отсекает сайт')).toBe(
      'судья в наблюдении: отказ не отсекает сайт',
    );
    expect(reasonWords('принял человек')).toBe('принял человек');
  });
});
