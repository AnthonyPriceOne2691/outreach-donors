/**
 * Второй шанс цитате на экране (судья сегмента v5): сколько сайтов модель спросили второй раз, потому
 * что цитата первого ответа не нашлась в тексте, и у скольких цитата второго ответа нашлась. Числа —
 * сервера: у прогона — `report.judge`, у пересуда — его итог из маршрута пересуда. Ответы сервера —
 * записанные (`serve()`), незаписанный запрос роняет тест; домены и описания выдуманы.
 *
 * Проверяется то, ради чего правка: числа отчёта — строкой под режимом и разбором описания, числа
 * пересуда — в его итоге рядом с токенами; второго вопроса не было (нули) или итог записан до v5
 * (полей нет) — строки нет и ошибки нет; слова одни в обоих местах, сайты — с верным склонением.
 */

import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { formatNumber } from '../format';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { SegmentHome, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import { quoteRetries, quoteRetryWords } from './segmentData';
import { SegmentQuoteRetry } from './SegmentQuoteRetry';
import type { RejudgeLast, RejudgeView } from './SegmentSitesApi';
import { RejudgeStatus } from './SegmentSitesRejudge';

const WAIT = { timeout: 5000 };
const RUN_ID = 41;
const LINE = 'Цитату переспросили у модели: 3 сайта, со второго раза нашлась у 2.';
const SCOPE =
  'описание стран не называет — место сайта не сверяется; виды сайтов — сайты самих компаний';

describe('второй шанс цитате словами', () => {
  it('сколько переспросили и у скольких нашлась', () => {
    expect(quoteRetryWords({ retried: 3, held: 2 })).toBe(
      'цитату переспросили у модели: 3 сайта, со второго раза нашлась у 2',
    );
  });

  it.each([
    [1, '1 сайт'],
    [4, '4 сайта'],
    [5, '5 сайтов'],
    [11, '11 сайтов'],
    [21, '21 сайт'],
    [1250, `${formatNumber(1250)} сайтов`],
  ])('сайты — с верным склонением: %i', (retried, sites) => {
    expect(quoteRetryWords({ retried, held: 1 })).toBe(
      `цитату переспросили у модели: ${sites}, со второго раза нашлась у 1`,
    );
  });

  it('со второго раза не нашлась ни у одного — словами, а не «у 0»', () => {
    expect(quoteRetryWords({ retried: 2, held: 0 })).toBe(
      'цитату переспросили у модели: 2 сайта, со второго раза не нашлась ни у одного',
    );
  });
});

describe('числа второго шанса из итога судьи', () => {
  it('числа сервера — как записаны', () => {
    expect(quoteRetries({ judged: 40, quote_retried: 3, quote_retry_held: 2 })).toEqual({
      retried: 3,
      held: 2,
    });
  });

  it('удач нет в итоге — ноль удач, а не пропавшая строка', () => {
    expect(quoteRetries({ quote_retried: 2 })).toEqual({ retried: 2, held: 0 });
  });

  it.each([
    { quote_retried: 0, quote_retry_held: 0 },
    { judged: 12, tokens: 9000 },
    { quote_retried: '3', quote_retry_held: 1 },
    { quote_retried: -1 },
    {},
    null,
    undefined,
    'не объект',
  ])('второго вопроса не было или итог до v5 — строки нет: %j', (tally) => {
    expect(quoteRetries(tally)).toBeNull();
  });
});

function line(report: Record<string, unknown>) {
  return render(
    <MantineProvider>
      <SegmentQuoteRetry report={report} />
    </MantineProvider>,
  );
}

describe('строка отчёта прогона', () => {
  it('числа `report.judge` — строкой с заглавной и точкой', () => {
    line({ judge: { judged: 40, quote_retried: 3, quote_retry_held: 2 } });

    expect(screen.getByText(LINE)).toBeInTheDocument();
  });

  it.each([
    { judge: { judged: 40, quote_retried: 0, quote_retry_held: 0 } },
    { judge: { judged: 40, tokens: 21000 } },
    { judge: null },
    {},
  ])('нули, прогон до v5 или судья не судил — строки нет: %j', (report) => {
    line(report);

    expect(screen.queryByText(/переспросили/)).not.toBeInTheDocument();
  });
});

const run: SegmentRunRow = {
  id: RUN_ID,
  hypothesis_id: 12,
  created_at: '2026-10-22T08:00:00+00:00',
  finished_at: '2026-10-22T08:06:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'nl',
  language: 'en',
  depth_pages: 1,
  keywords: 3,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: null,
  alive_at: '2026-10-22T08:06:00+00:00',
  active: false,
  progress: null,
  watch: null,
};

const home: SegmentHome = {
  hypothesis_id: 12,
  segment:
    'Велосипедные мастерские: сами чинят и продают велосипеды, без каталогов чужих мастерских.',
  mode: 'shadow',
  countries: ['nl'],
  languages: ['en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [run],
};

const hypotheses: HypothesesView = {
  rows: [
    {
      id: 12,
      name: 'велосипеды NL',
      description: home.segment,
      created_at: '2026-10-21T10:00:00+00:00',
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

function detailOf(report: Record<string, unknown>): SegmentRunDetail {
  return {
    run,
    hypothesis: 'велосипеды NL',
    keywords: ['fietsenmaker amsterdam'],
    estimate_lines: [],
    summary: {
      hosts: 12,
      accepted: 7,
      review: 2,
      rejected: 3,
      pending: 0,
      no_address: 0,
      leads: 0,
    },
    lanes: { accept: 7, review: 2, reject: 3 },
    rejected: [],
    no_address: [],
    waiting_address: 0,
    leads: {},
    lead_reasons: {},
    unanswered: 0,
    report,
  };
}

async function openReport(report: Record<string, unknown>): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: hypotheses },
    'GET /api/sales/kb': { body: kb },
    'GET /api/sales/segment?hypothesis=12': { body: home },
    [`GET /api/sales/segment/runs/${RUN_ID}`]: { body: detailOf(report) },
  });
  renderWith(<AppRoutes />, '/sales?tab=collect&hypothesis=12');
  await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
}

describe('отчёт прогона на экране', () => {
  it('строка — под режимом судьи и разбором описания', async () => {
    await openReport({
      scope: { words: SCOPE },
      judge: { judged: 12, tokens: 26_000, quote_retried: 3, quote_retry_held: 2 },
    });

    const shown = await screen.findByText(LINE, {}, WAIT);
    const scope = screen.getByText(`Как судья понял описание: ${SCOPE}.`);
    const mode = screen.getByText(/^судья в наблюдении/);
    // Порядок в документе: режим → разбор описания → второй вопрос.
    expect(mode.compareDocumentPosition(scope) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(scope.compareDocumentPosition(shown) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('прогон до судьи v5 — строки нет, отчёт целиком', async () => {
    await openReport({ scope: { words: SCOPE }, judge: { judged: 12, tokens: 26_000 } });

    expect(await screen.findByText(`Как судья понял описание: ${SCOPE}.`, {}, WAIT)).toBeVisible();
    expect(screen.queryByText(/переспросили/)).not.toBeInTheDocument();
  });
});

const lastDone: RejudgeLast = {
  state: 'done',
  by: 'seller@ours.example.test',
  asked_at: '2026-10-22T09:00:00+00:00',
  started_at: '2026-10-22T09:00:02+00:00',
  finished_at: '2026-10-22T09:01:30+00:00',
  total: 6,
  done: 6,
  human: 0,
  kept_human: 0,
  pending: 0,
  moved: [{ was: 'review', now: 'accept', would: false, count: 1 }],
  tokens: 15_930,
  unanswered: 0,
  failed: 0,
  error: null,
};

function rejudged(last: RejudgeLast): RejudgeView {
  return {
    run_id: RUN_ID,
    estimate: {
      sites: 0,
      model: 0,
      per_site: 0,
      tokens: 0,
      tokens_left: null,
      kept_human: 0,
      with_lead: 0,
      pending: 0,
      digest: 'digest-of-segment',
      mode: 'shadow',
      resumes: false,
      done: 0,
      lines: [],
      refusal: null,
    },
    last,
    going: false,
  };
}

function rejudgeAside(last: RejudgeLast): string[] {
  render(
    <MantineProvider>
      <RejudgeStatus view={rejudged(last)} onChanged={() => undefined} />
    </MantineProvider>,
  );
  const done = screen.getByRole('status');
  expect(within(done).getByText(/сменили полосу 1 из 6/)).toBeInTheDocument();
  return within(done)
    .getAllByText(/—|переспросили/)
    .map((item) => item.textContent ?? '')
    .filter((text) => !text.startsWith('Пересуд'));
}

describe('итог пересуда', () => {
  it('числа маршрута пересуда — строкой рядом с токенами', () => {
    const lines = rejudgeAside({ ...lastDone, quote_retried: 2, quote_retry_held: 1 });

    expect(lines).toEqual([
      'на проверке → принят — 1',
      'цитату переспросили у модели: 2 сайта, со второго раза нашлась у 1',
      `токенов модели — ${formatNumber(15_930)}`,
    ]);
  });

  it.each([
    { quote_retried: 0, quote_retry_held: 0 },
    {}, // сервер старше полей второго шанса
  ])('нули или полей нет — строки нет, итог прежний: %j', (fields) => {
    const lines = rejudgeAside({ ...lastDone, ...fields });

    expect(lines).toEqual(['на проверке → принят — 1', `токенов модели — ${formatNumber(15_930)}`]);
  });
});
