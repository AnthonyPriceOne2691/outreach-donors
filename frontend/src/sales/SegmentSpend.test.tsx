/**
 * Отчёт прогона сбора: ключи прогона (взятые с пометками, отсеянные с причиной, пропавшие и пустые)
 * и расход статьями рядом со сметой на запуске — словами сервера (`SegmentKeywords`,
 * `SegmentSpend`). Расход и смета — в окне «Расход и смета» у заголовка прогона, в отчёте их нет.
 * Ответы записанные (`serve()`), незаписанный запрос роняет тест; ключи и домены выдуманы.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { SegmentHome, SegmentRunDetail, SegmentRunRow, SegmentRunSpend } from './segmentApi';

const SCREEN_WAIT = { timeout: 5000 };

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 7,
      name: 'перевозчики',
      description: 'Перевозчики грузов.',
      created_at: '2026-10-02T10:00:00+00:00',
      leads: { new: 0, ready: 2, rejected: 0 },
      total: 2,
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
  id: 21,
  created_at: '2026-10-14T09:30:00+00:00',
  finished_at: '2026-10-14T09:41:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'kz',
  language: 'ru',
  depth_pages: 1,
  keywords: 3,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: 'sales-segment-21',
};

const HOME: SegmentHome = {
  hypothesis_id: 7,
  segment: 'Перевозчики грузов.',
  mode: 'shadow',
  countries: ['kz', 'us'],
  languages: ['ru', 'en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [RUN],
};

const SPEND: SegmentRunSpend = {
  keywords: {
    taken: ['доставка грузов', 'доставка грузов казань', 'перевозка мебели'],
    notes: { 'доставка грузов казань': 'похож на «доставка грузов» — общих слов 2 из 3' },
    rejected: { 'site:cargo.example.test': 'поисковый оператор: так пользователи не ищут' },
    lost: ['перевозка мебели'],
    empty: ['доставка грузов казань'],
  },
  rows: [
    {
      item: 'serp',
      title: 'Выдача',
      fact: '$0,0132',
      promised: '≈ $0,0105',
      fact_units: null,
      fact_usd: 0.0132,
      promised_units: null,
      promised_usd: 0.0105,
    },
    {
      item: 'judge',
      title: 'Судья сегмента',
      // Разряды сервер пишет неразрывным пробелом, как экран: ответ — в форме живого.
      fact: 'токенов 40\u00a0211, ≈ $0,0241',
      promised: 'токенов ≈ 61\u00a0600, ≈ $0,0370',
      fact_units: 40211,
      fact_usd: 0.0241,
      promised_units: 61600,
      promised_usd: 0.037,
    },
    {
      item: 'search',
      title: 'Hunter: поиск адреса',
      fact: 'поисков 14; отказов провайдера 3 — их он не засчитывает',
      promised: 'до 56',
      fact_units: 14,
      fact_usd: null,
      promised_units: 56,
      promised_usd: null,
    },
    {
      item: 'check',
      title: 'Hunter: проверка адреса',
      fact: 'адреса ещё не проверяли',
      promised: 'до 56',
      fact_units: null,
      fact_usd: null,
      promised_units: 56,
      promised_usd: null,
    },
    {
      item: 'money',
      title: 'Деньгами: выдача и модель',
      fact: '$0,0373',
      promised: '≈ $0,0475',
      fact_units: null,
      fact_usd: 0.0373,
      promised_units: null,
      promised_usd: 0.0475,
    },
  ],
};

const DETAIL: SegmentRunDetail = {
  run: RUN,
  hypothesis: 'перевозчики',
  keywords: SPEND.keywords.taken,
  estimate_lines: ['смета'],
  spend: SPEND,
  summary: { hosts: 7, accepted: 2, review: 0, rejected: 5, no_address: 0, leads: 2 },
  lanes: { pending: 0, accept: 2, review: 0, reject: 5 },
  rejected: [],
  no_address: [],
  waiting_address: 0,
  leads: { new: 0, ready: 2, rejected: 0 },
  lead_reasons: {},
  unanswered: 0,
  report: {},
};

async function openReport(detail: SegmentRunDetail = DETAIL) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/segment?hypothesis=7': { body: HOME },
    'GET /api/sales/segment/runs/21': { body: detail },
  });
  renderWith(<AppRoutes />, '/sales?tab=collect');
  await screen.findByText('Прогон №21', {}, SCREEN_WAIT);
}

/** Окно «Расход и смета» — кнопкой у заголовка прогона. */
async function openSpend(): Promise<HTMLElement> {
  await userEvent.setup().click(screen.getByRole('button', { name: 'Расход и смета' }));
  return screen.findByRole('dialog', { name: 'Расход и смета прогона №21' }, SCREEN_WAIT);
}

function items(name: string | RegExp): (string | null)[] {
  return within(screen.getByRole('list', { name }))
    .getAllByRole('listitem')
    .map((item) => item.textContent);
}

describe('сбор базы: ключи и расход в отчёте прогона', () => {
  it('ключи прогона: взятые с пометкой, отсеянные с причиной, пропавшие и пустые', async () => {
    await openReport();

    expect(screen.getByText('Ключи прогона')).toBeInTheDocument();
    expect(items('Взято ключей: 3 из 4')).toEqual([
      '«доставка грузов»',
      '«доставка грузов казань» — похож на «доставка грузов» — общих слов 2 из 3',
      '«перевозка мебели»',
    ]);
    expect(items('Отсеяно ключей: 1')).toEqual([
      '«site:cargo.example.test» — поисковый оператор: так пользователи не ищут',
    ]);
    expect(items('Выдача не пришла — заплачено, а провайдер не отдал')).toEqual([
      '«перевозка мебели»',
    ]);
    expect(items('По ключу ничего не нашлось')).toEqual(['«доставка грузов казань»']);
  });

  it('расход статьями: факт и смета на запуске словами сервера; Hunter — не в деньгах', async () => {
    await openReport();
    // В отчёте расхода и сметы нет — они в окне.
    expect(screen.queryByRole('region', { name: 'Расход прогона' })).toBeNull();
    expect(screen.queryByText('Смета на запуске')).toBeNull();
    const dialog = await openSpend();

    const spend = within(dialog).getByRole('region', { name: 'Расход прогона' });
    const table = within(spend).getByRole('table');
    const rows = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) =>
        within(row)
          .getAllByRole('cell')
          .map((cell) => cell.textContent),
      );
    expect(rows).toEqual(SPEND.rows.map((row) => [row.title, row.fact, row.promised]));
    expect(
      within(dialog).getByText(
        'Hunter платится запросами своего тарифа: в деньги не переводится и в сумму не входит.',
      ),
    ).toBeInTheDocument();
    // Смета на запуске — строками сервера, рядом с расходом.
    const promised = within(dialog).getByRole('region', { name: 'Смета на запуске' });
    expect(within(promised).getByText('смета')).toBeInTheDocument();
  });

  it('ответ без ключей и расхода (старые записи) — в окне только смета на запуске', async () => {
    const old: SegmentRunDetail = { ...DETAIL };
    delete old.spend;
    await openReport(old);

    expect(screen.queryByText('Ключи прогона')).toBeNull();
    const dialog = await openSpend();
    expect(within(dialog).queryByRole('region', { name: 'Расход прогона' })).toBeNull();
    expect(within(dialog).getByText('Смета на запуске')).toBeInTheDocument();
  });

  it('ни расхода, ни сметы у прогона — кнопки «Расход и смета» нет', async () => {
    const bare: SegmentRunDetail = { ...DETAIL, estimate_lines: [] };
    delete bare.spend;
    await openReport(bare);

    expect(screen.queryByRole('button', { name: 'Расход и смета' })).toBeNull();
  });
});
