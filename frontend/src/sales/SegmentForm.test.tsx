/**
 * Форма нового прогона сбора (`SegmentForm`): режим выдачи — умолчание сервиса (сервер не назвал —
 * быстрый), «Стандарт» на выбор, приоритетный — только когда он умолчание сервиса — уходит в смету
 * и запуск; смета называет взятые ключи чипами с пометками и отсеянные с причиной (снятие
 * галочкой — `SegmentKeywords.test.tsx`); правка описания сегмента снимает посчитанную смету —
 * судья судит по описанию, и запустить посчитанное по прежнему нельзя. Ответы сервера —
 * записанные (`serve()`), незаписанный запрос роняет тест; ключи и домены выдуманы.
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
import type { SegmentEstimate, SegmentHome } from './segmentApi';

const SCREEN_WAIT = { timeout: 5000 };
const SEGMENT_TEXT = 'Перевозчики грузов: свои машины, доставка по городу и между городами.';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 7,
      name: 'перевозчики',
      description: SEGMENT_TEXT,
      created_at: '2026-10-02T10:00:00+00:00',
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

const HOME: SegmentHome = {
  hypothesis_id: 7,
  segment: SEGMENT_TEXT,
  mode: 'shadow',
  countries: ['de', 'kz', 'us'],
  languages: ['ru', 'en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [],
};

const NEAR =
  'похож на «доставка грузов» — общих слов 2 из 3: выдача может частично совпасть, ключ взят';
const REPEAT = 'повтор строки 1: для выдачи это тот же запрос — второй раз за него не платим';

const ESTIMATE: SegmentEstimate = {
  keywords: ['доставка грузов', 'доставка грузов казань'],
  excluded: [],
  keyword_lines: { 'доставка грузов': 1, 'доставка грузов казань': 2 },
  rejected: { 'Доставка грузов': REPEAT },
  notes: { 'доставка грузов казань': NEAR },
  segment: SEGMENT_TEXT,
  digest: 'seen-cargo',
  country: 'kz',
  language: 'ru',
  depth_pages: 1,
  mode: 'shadow',
  serp_mode: 'live',
  serp_page_usd: 0.002,
  serp_wait_s: 10,
  results: 20,
  share: 0.475,
  share_runs: 2,
  hosts: 10,
  known: 0,
  sites: 10,
  serp_usd: 0.004,
  tokens: 11000,
  tokens_usd: 0.0066,
  tokens_left: null,
  hunter_searches: 0,
  hunter_checks: 0,
  lines: [
    'Выдача — быстрая (Live): ключей 2 × страниц 1 — результатов до 20, по $0,0020 за страницу ' +
      '≈ $0,0040; ждать ≈ 10 с.',
    'Судья сегмента: сайтов ≈ 10, токенов модели ≈ 11 000 (по 1 100 на сайт) — ≈ $0,0066.',
  ],
  refusal: null,
};

async function openTab(routes: Record<string, Answer | ((call: Call) => Answer)>) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/segment?hypothesis=7': { body: HOME },
    'POST /api/sales/segment/estimate': { body: ESTIMATE },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=collect');
  await screen.findByText('Сегмент гипотезы', {}, SCREEN_WAIT);
  return recorded;
}

async function countEstimate(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.click(screen.getByRole('textbox', { name: 'Ключи' }));
  await user.paste(text);
  await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
  await screen.findByText(ESTIMATE.lines[1] ?? '', {}, SCREEN_WAIT);
}

function asked(recorded: Recorded): unknown[] {
  return recorded.calls
    .filter((call) => call.method === 'POST' && call.path === '/api/sales/segment/estimate')
    .map((call) => (call.body as { serp_mode: unknown }).serp_mode);
}

describe('сбор базы: форма прогона и смета', () => {
  it('выдача — быстрая по умолчанию; «Стандарт» — на выбор; приоритетной в форме нет', async () => {
    const recorded = await openTab({});
    const user = userEvent.setup();

    const mode = screen.getByRole('textbox', { name: 'Выдача' });
    expect(mode).toHaveValue('Быстрая (Live)');
    await countEstimate(user, 'доставка грузов');
    await user.click(mode);
    const options = await screen.findAllByRole('option', {}, SCREEN_WAIT);
    expect(options.map((option) => option.textContent)).toEqual([
      'Быстрая (Live)',
      'Стандарт — дешевле, дольше',
    ]);
    await user.click(screen.getByRole('option', { name: 'Стандарт — дешевле, дольше' }));

    // Другой режим — другая цена: посчитанная смета снята, запуск закрыт.
    expect(screen.queryByText(ESTIMATE.lines[1] ?? '')).toBeNull();
    expect(screen.getByRole('button', { name: 'Запустить прогон' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await waitFor(() => expect(asked(recorded)).toEqual(['live', 'standard']), SCREEN_WAIT);
  });

  it('режим по умолчанию — настройкой сервиса: «Стандарт» у сервиса — «Стандарт» в смете', async () => {
    const recorded = await openTab({
      'GET /api/sales/segment?hypothesis=7': { body: { ...HOME, serp_mode: 'standard' } },
    });
    const user = userEvent.setup();

    const mode = screen.getByRole('textbox', { name: 'Выдача' });
    expect(mode).toHaveValue('Стандарт — дешевле, дольше');
    await countEstimate(user, 'доставка грузов');
    await waitFor(() => expect(asked(recorded)).toEqual(['standard']), SCREEN_WAIT);
    await user.click(mode);
    const options = await screen.findAllByRole('option', {}, SCREEN_WAIT);
    expect(options.map((option) => option.textContent)).toEqual([
      'Быстрая (Live)',
      'Стандарт — дешевле, дольше',
    ]);
  });

  it('приоритетный — в поле, только когда он умолчание сервиса: иначе поле стояло бы пустым', async () => {
    const recorded = await openTab({
      'GET /api/sales/segment?hypothesis=7': { body: { ...HOME, serp_mode: 'priority' } },
    });
    const user = userEvent.setup();

    const mode = screen.getByRole('textbox', { name: 'Выдача' });
    expect(mode).toHaveValue('Приоритет — быстрее, дороже');
    await countEstimate(user, 'доставка грузов');
    await waitFor(() => expect(asked(recorded)).toEqual(['priority']), SCREEN_WAIT);
    await user.click(mode);
    const options = await screen.findAllByRole('option', {}, SCREEN_WAIT);
    expect(options.map((option) => option.textContent)).toEqual([
      'Быстрая (Live)',
      'Стандарт — дешевле, дольше',
      'Приоритет — быстрее, дороже',
    ]);
  });

  it('смета называет взятые ключи чипами с пометкой и отсеянные с причиной — одним списком', async () => {
    await openTab({});
    const user = userEvent.setup();

    await countEstimate(user, 'доставка грузов\nдоставка грузов казань\nДоставка грузов');

    const keys = screen.getByRole('list', { name: 'Ключи сметы' });
    expect(
      within(keys)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      'доставка грузов',
      `доставка грузов казань ${NEAR}`,
      `отсеян: Доставка грузов ${REPEAT}`,
    ]);
    expect(
      within(keys)
        .getAllByRole('checkbox')
        .map((box) => (box as HTMLInputElement).checked),
    ).toEqual([true, true]);
    expect(screen.getByText(/^Взято 2 из 3 · отсеяно 1\./)).toBeInTheDocument();
  });

  it('правка описания сегмента снимает смету: посчитанное по прежнему не запустить', async () => {
    let segment: string | null = SEGMENT_TEXT;
    await openTab({
      'GET /api/sales/segment?hypothesis=7': () => ({ body: { ...HOME, segment } }),
      'PATCH /api/sales/hypotheses/7': (call) => {
        segment = (call.body as { description: string }).description;
        return { body: { hypothesis_id: 7, description: segment } };
      },
    });
    const user = userEvent.setup();
    await countEstimate(user, 'доставка грузов');
    const start = screen.getByRole('button', { name: 'Запустить прогон' });
    await waitFor(() => expect(start).toBeEnabled(), SCREEN_WAIT);

    await user.click(screen.getByRole('button', { name: 'Изменить' }));
    await user.type(
      screen.getByRole('textbox', { name: 'Описание сегмента' }),
      ' Без экспедиторов.',
    );
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await screen.findByText(`${SEGMENT_TEXT} Без экспедиторов.`, {}, SCREEN_WAIT);
    expect(screen.queryByText(ESTIMATE.lines[1] ?? '')).toBeNull();
    expect(screen.getByRole('button', { name: 'Запустить прогон' })).toBeDisabled();
  });
});
