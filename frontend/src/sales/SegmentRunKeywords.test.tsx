/**
 * Ключи прогона в отчёте таблицей (`SegmentRunKeywords`): по ключу на строку, сколько новых для
 * гипотезы сайтов он принёс и сколько из них принято; почти повторы — группой под ключом, на
 * который похожи, с одной строкой «похожи на «…»»; значки покупки выдачи у ключа; прочие пометки,
 * снятые и отсеянные — один раз под таблицей; галочек нет. На узком окне числа — строкой под
 * ключом, а таблица — в ширину карточки: нижней границы ширины нет, длинное слово переносится где
 * угодно; у сервера без сайтов по ключу колонок чисел нет, а не нули.
 *
 * Ответы сервера — записанные (`serve()`); ключи — немецкой и финской ниш, домены выдуманы.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { HELP_QUIET } from './segmentHelpFixtures';
import type { SegmentHome, SegmentRunDetail, SegmentRunRow } from './segmentApi';

const SCREEN_WAIT = { timeout: 5000 };
const OPERATOR = 'поисковый оператор: так пользователи не ищут';
const FIRM = 'похоже на название фирмы («GmbH»): выдача найдёт её реестры и карточки, а не сегмент';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 9,
      name: 'Zahnärzte DE',
      description: 'Zahnarztpraxen, die selbst Patienten behandeln.',
      created_at: '2026-10-03T10:00:00+00:00',
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
  id: 44,
  created_at: '2026-10-15T09:30:00+00:00',
  finished_at: '2026-10-15T09:41:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'de',
  language: 'en',
  depth_pages: 5,
  keywords: 5,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: 'sales-segment-44',
};

const HOME: SegmentHome = {
  hypothesis_id: 9,
  segment: 'Zahnarztpraxen, die selbst Patienten behandeln.',
  mode: 'shadow',
  countries: ['de'],
  languages: ['ru', 'en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [RUN],
};

const DETAIL: SegmentRunDetail = {
  run: RUN,
  hypothesis: 'Zahnärzte DE',
  keywords: [
    'zahnarzt berlin',
    'zahnarzt berlin mitte',
    'zahnarzt berlin kreuzberg',
    'kieferorthopäde berlin',
    'praxis müller gmbh',
  ],
  estimate_lines: ['смета'],
  spend: {
    keywords: {
      taken: [
        'zahnarzt berlin',
        'zahnarzt berlin mitte',
        'zahnarzt berlin kreuzberg',
        'kieferorthopäde berlin',
        'praxis müller gmbh',
      ],
      notes: {
        'zahnarzt berlin mitte': 'похож на «zahnarzt berlin» — общих слов 2 из 3',
        'zahnarzt berlin kreuzberg': 'похож на «zahnarzt berlin» — общих слов 2 из 3',
        'praxis müller gmbh': FIRM,
      },
      rejected: { 'site:praxis.example.test zahnarzt': OPERATOR },
      lost: ['zahnarzt berlin kreuzberg'],
      empty: ['kieferorthopäde berlin'],
      excluded: ['zahnarzt kosten'],
      twins: {
        'zahnarzt berlin mitte': 'zahnarzt berlin',
        'zahnarzt berlin kreuzberg': 'zahnarzt berlin',
      },
      remarks: { 'praxis müller gmbh': FIRM },
      sites: { 'zahnarzt berlin': 14, 'zahnarzt berlin mitte': 2, 'praxis müller gmbh': 3 },
      accepted: { 'zahnarzt berlin': 11, 'zahnarzt berlin mitte': 1 },
    },
    rows: [],
  },
  summary: { hosts: 19, accepted: 12, review: 4, rejected: 3, no_address: 0, leads: 10 },
  lanes: { pending: 0, accept: 12, review: 4, reject: 3 },
  rejected: [],
  no_address: [],
  waiting_address: 0,
  leads: { new: 0, ready: 10, rejected: 0 },
  lead_reasons: {},
  unanswered: 0,
  report: {},
};

async function openReport(detail: SegmentRunDetail = DETAIL): Promise<HTMLElement> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/segment?hypothesis=9': { body: HOME },
    'GET /api/sales/segment/runs/44': { body: detail },
    ...HELP_QUIET,
  });
  renderWith(<AppRoutes />, '/sales?tab=collect');
  return screen.findByRole('region', { name: 'Ключи прогона' }, SCREEN_WAIT);
}

/** Окно телефона: узкий вид ключей (`max-width: 36em`). Заглушку снимает `restoreAllMocks` после
 *  теста (`test/setup.ts`). */
function narrowWindow() {
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: query.includes('max-width: 36em'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

/** Уже какой ширины таблица уезжает в свою прокрутку — переменная контейнера прокрутки. */
function minWidthOf(block: HTMLElement): string | undefined {
  const scroll = within(block)
    .getByRole('table')
    .closest<HTMLElement>('[style*="--table-min-width"]');
  return scroll?.style.getPropertyValue('--table-min-width');
}

/** Строки таблицы без шапки — текстом ячеек. */
function rowsOf(block: HTMLElement): string[][] {
  return within(within(block).getByRole('table'))
    .getAllByRole('row')
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole('cell')
        .map((cell) => cell.textContent ?? ''),
    );
}

describe('ключи прогона таблицей в отчёте', () => {
  it('ключ — строкой с числами; почти повторы — группой под своим ключом; без галочек', async () => {
    const block = await openReport();

    expect(
      within(block).getByText('· взято 5 из 7 · снято на запуске 1 · отсеяно 1'),
    ).toBeVisible();
    expect(
      within(within(block).getByRole('table'))
        .getAllByRole('columnheader')
        .map((cell) => cell.textContent),
    ).toEqual(['Ключ', 'Новых сайтов', 'Принято']);
    expect(rowsOf(block)).toEqual([
      ['zahnarzt berlin', '14', '11'],
      ['Похожи на «zahnarzt berlin»: выдача может частично совпасть.'],
      ['zahnarzt berlin mitte', '2', '1'],
      ['zahnarzt berlin kreuzberg выдача не пришла', '0', '0'],
      ['kieferorthopäde berlin ничего не нашлось', '0', '0'],
      ['praxis müller gmbh', '3', '0'],
    ]);
    // Пометка почти повтора — одна строка группы, а не у каждого ключа.
    expect(within(block).queryByText(/общих слов/)).toBeNull();
    expect(within(block).queryByRole('checkbox')).toBeNull();
    // С колонками чисел уже 300 px (18,75 rem) таблица уезжает в прокрутку: ключу — хоть 84 px.
    expect(minWidthOf(block)).toContain('18.75rem');
  });

  it('под таблицей — что значат числа и значок, пометка фирмы, снятые и отсеянные', async () => {
    const block = await openReport();

    const notes = block.querySelector('.runKeysNotes');
    expect([...(notes?.children ?? [])].map((line) => line.textContent)).toEqual([
      'Новые сайты — которых в гипотезе ещё не было; сайт, найденный несколькими ключами, ' +
        'засчитан первому из них по списку.',
      '«выдача не пришла» — заплачено, а провайдер не отдал.',
      `praxis müller gmbh — ${FIRM}.`,
      'Снят на запуске, выдачу не покупали: zahnarzt kosten.',
      `Отсеян «site:praxis.example.test zahnarzt»: ${OPERATOR}.`,
    ]);
  });

  it('на узком окне числа — строкой под ключом, колонок чисел нет', async () => {
    narrowWindow();
    const block = await openReport();

    expect(
      within(within(block).getByRole('table'))
        .getAllByRole('columnheader')
        .map((cell) => cell.textContent),
    ).toEqual(['Ключ']);
    expect(rowsOf(block)[0]).toEqual(['zahnarzt berlinновых сайтов 14 · принято 11']);
  });

  it('на узком окне таблица — в ширину карточки: границы ширины нет, длинное слово переносится', async () => {
    narrowWindow();
    // Длинное слово без пробелов (финская ниша) — в ключе, в строке группы и в пометке фирмы.
    const long = 'hammaslääkäriajanvarausjärjestelmä';
    const head = `${long} helsinki`;
    const firm =
      'похоже на название фирмы («Oy»): выдача найдёт её реестры и карточки, а не сегмент';
    const keywords = {
      ...DETAIL.spend!.keywords,
      taken: [head, `${head} keskusta`, `${long} Oy`],
      notes: {},
      lost: [],
      empty: [],
      twins: { [`${head} keskusta`]: head },
      remarks: { [`${long} Oy`]: firm },
      sites: { [head]: 7 },
      accepted: { [head]: 5 },
    };
    const block = await openReport({ ...DETAIL, spend: { keywords, rows: [] } });

    // В одну колонку таблице беречь нечего: в 300 px она стояла в карточке 282 px на 390 px и
    // уезжала в свою прокрутку, обрезая значки и строку группы.
    expect(minWidthOf(block)).toBe('0rem');
    expect(rowsOf(block)).toEqual([
      [`${head}новых сайтов 7 · принято 5`],
      [`Похож на «${head}»: выдача может частично совпасть.`],
      [`${head} keskustaновых сайтов 0 · принято 0`],
      [`${long} Oyновых сайтов 0 · принято 0`],
    ]);
    expect(within(block).getByText(`${long} Oy — ${firm}.`)).toBeVisible();
    // Перенос где угодно — свойством всего блока: ключ, строка группы и пометки под таблицей.
    expect(block).toHaveStyle({ overflowWrap: 'anywhere' });
  });

  it('сервер без сайтов по ключу и групп — колонок чисел нет, пометки идут словами под таблицей', async () => {
    const keywords = { ...DETAIL.spend!.keywords };
    delete keywords.twins;
    delete keywords.remarks;
    delete keywords.sites;
    delete keywords.accepted;
    const block = await openReport({ ...DETAIL, spend: { keywords, rows: [] } });

    expect(rowsOf(block).map((row) => row.length)).toEqual([1, 1, 1, 1, 1]);
    expect(
      within(block).getByText(
        'zahnarzt berlin mitte, zahnarzt berlin kreuzberg — похож на «zahnarzt berlin» — общих ' +
          'слов 2 из 3.',
      ),
    ).toBeVisible();
    expect(within(block).queryByText(/Новые сайты —/)).toBeNull();
  });
});
