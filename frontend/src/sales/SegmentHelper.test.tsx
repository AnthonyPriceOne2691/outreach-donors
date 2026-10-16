/**
 * Помощник описания сегмента (`SegmentHelper.tsx`): «Помочь с описанием» под полем описания — на
 * «Сборе базы» и в окне гипотезы — и проверка описания при смете. Проверяется то, ради чего он:
 * черновик уходит помощнику как есть, ответ — словами под кнопкой, в поле текст кладёт только
 * «Взять», сохраняет человек; расплывчатое описание при смете — предупреждение с предложением, и
 * «Взять в описание» открывает правку с ним. Ответы сервера — записанные (`serve()`),
 * незаписанный запрос роняет тест; ниши и тексты выдуманы.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { operationTitle } from '../api/labels';
import type { HypothesesView, HypothesisCard } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import type { SegmentEstimate, SegmentHome } from './segmentApi';
import { HELP_CONCRETE, HELP_LOOSE, HELP_ROUTE } from './segmentHelpFixtures';
import { verdictOf } from './SegmentHelper';

const SCREEN_WAIT = { timeout: 5000 };
const HELPER = '/api/sales/segment/helper';
const EDIT = '/api/sales/hypotheses/7';
const SEGMENT_TEXT = 'Сайты про бухгалтерию в Сан-Паулу.';
const DRAFT = 'Сайты, связанные с ветеринарными клиниками в Дублине';
const FAILED =
  'Помощник не ответил (модель не ответила: модель, сеть (можно повторить): timeout) — ' +
  'попробуйте ещё раз чуть позже; описание можно написать и самому, подсказка «Как описать ' +
  'сегмент» — под полем';

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

function card(description: string | null): HypothesisCard {
  return {
    id: 7,
    name: 'бухгалтерия SP',
    description,
    created_at: '2026-10-16T10:00:00+00:00',
    leads: { new: 0, ready: 0, rejected: 0 },
    total: 0,
  };
}

const HYPOTHESES: HypothesesView = { rows: [card(SEGMENT_TEXT)], total: 1, module_enabled: true };

const HOME: SegmentHome = {
  hypothesis_id: 7,
  segment: SEGMENT_TEXT,
  mode: 'shadow',
  countries: ['br', 'ie', 'kz'],
  languages: ['pt', 'en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [],
};

const ESTIMATE: SegmentEstimate = {
  keywords: ['contabilidade são paulo'],
  excluded: [],
  keyword_lines: { 'contabilidade são paulo': 1 },
  rejected: {},
  notes: {},
  segment: SEGMENT_TEXT,
  digest: 'seen-sp',
  country: 'br',
  language: 'pt',
  depth_pages: 1,
  mode: 'shadow',
  serp_mode: 'live',
  serp_page_usd: 0.002,
  serp_wait_s: 10,
  results: 10,
  share: 0.5,
  share_runs: 0,
  hosts: 5,
  known: 0,
  sites: 5,
  serp_usd: 0.002,
  tokens: 5500,
  tokens_usd: 0.0033,
  tokens_left: null,
  hunter_searches: 0,
  hunter_checks: 0,
  lines: [
    'Выдача — быстрая (Live): ключей 1 × страниц 1 — результатов до 10 ≈ $0,0020.',
    'Судья сегмента: сайтов ≈ 5, токенов модели ≈ 5 500 — ≈ $0,0033.',
  ],
  refusal: null,
};

type Route = Answer | ((call: Call) => Answer);

async function openTab(
  routes: Record<string, Route>,
  path = '/sales?tab=collect',
  ready = 'Сегмент гипотезы',
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/segment?hypothesis=7': { body: HOME },
    'POST /api/sales/segment/estimate': { body: ESTIMATE },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready, {}, SCREEN_WAIT);
  return recorded;
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

/** Правка описания на «Сборе базы» с черновиком вставкой: по букве набор шёл бы дольше срока. */
async function editDraft(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.click(screen.getByRole('button', { name: 'Изменить' }));
  const field = screen.getByRole('textbox', { name: 'Описание сегмента' });
  await user.clear(field);
  await user.paste(text);
  return field;
}

async function countEstimate(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('textbox', { name: 'Ключи' }));
  await user.paste('contabilidade são paulo');
  await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
  await screen.findByText(ESTIMATE.lines[1] ?? '', {}, SCREEN_WAIT);
}

describe('помощник описания: «Помочь с описанием» на «Сборе базы»', () => {
  it('черновик уходит как есть, ответ — под кнопкой, «Взять» кладёт текст в поле, сохраняет человек', async () => {
    const recorded = await openTab({
      [HELP_ROUTE]: { body: HELP_LOOSE },
      [`PATCH ${EDIT}`]: { body: { hypothesis_id: 7, description: HELP_LOOSE.description } },
    });
    const user = userEvent.setup();

    const field = await editDraft(user, DRAFT);
    await user.click(screen.getByRole('button', { name: 'Помочь с описанием' }));

    expect(await screen.findByText(verdictOf(HELP_LOOSE), {}, SCREEN_WAIT)).toBeInTheDocument();
    // Что именно собьёт судью — словами черновика: человек видит, что править.
    expect(screen.getByText('Что собьёт судью')).toBeInTheDocument();
    for (const problem of HELP_LOOSE.problems) {
      expect(screen.getByText(problem)).toBeInTheDocument();
    }
    expect(screen.getByText(HELP_LOOSE.description)).toBeInTheDocument();
    for (const question of HELP_LOOSE.unclear) {
      expect(screen.getByText(question)).toBeInTheDocument();
    }
    expect(calls(recorded, 'POST', HELPER).map((call) => call.body)).toEqual([
      { description: DRAFT },
    ]);
    expect(field).toHaveValue(DRAFT);

    await user.click(screen.getByRole('button', { name: 'Взять этот текст' }));

    expect(field).toHaveValue(HELP_LOOSE.description);
    expect(screen.queryByText(verdictOf(HELP_LOOSE))).not.toBeInTheDocument();
    expect(calls(recorded, 'PATCH', EDIT)).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(
      () =>
        expect(calls(recorded, 'PATCH', EDIT).map((call) => call.body)).toEqual([
          { description: HELP_LOOSE.description },
        ]),
      SCREEN_WAIT,
    );
  });

  it('пустое поле — кнопка выключена и сказано почему', async () => {
    await openTab({});
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Изменить' }));
    await user.clear(screen.getByRole('textbox', { name: 'Описание сегмента' }));

    expect(screen.getByRole('button', { name: 'Помочь с описанием' })).toBeDisabled();
    expect(
      screen.getByText('Напишите черновик — помощник перепишет его так, как читает судья'),
    ).toBeInTheDocument();
  });

  it('конкретный черновик — так и сказано; поправленное после ответа поле — помечено', async () => {
    await openTab({ [HELP_ROUTE]: { body: HELP_CONCRETE } });
    const user = userEvent.setup();

    const field = await editDraft(user, DRAFT);
    await user.click(screen.getByRole('button', { name: 'Помочь с описанием' }));
    expect(await screen.findByText(verdictOf(HELP_CONCRETE), {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.queryByText('Что уточнить в черновике')).not.toBeInTheDocument();
    expect(screen.queryByText('Что собьёт судью')).not.toBeInTheDocument();

    await user.type(field, '!');

    expect(
      screen.getByText('Ответ — на черновик до правки: поле с тех пор поправили.'),
    ).toBeInTheDocument();
  });

  it('помощник не ответил — отказ словами сервера, поле не тронуто', async () => {
    await openTab({ [HELP_ROUTE]: { status: 502, body: { detail: FAILED } } });
    const user = userEvent.setup();

    const field = await editDraft(user, DRAFT);
    await user.click(screen.getByRole('button', { name: 'Помочь с описанием' }));

    expect(await screen.findByText(FAILED, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.getByText('Помощник не ответил')).toBeInTheDocument();
    expect(field).toHaveValue(DRAFT);
  });
});

describe('помощник описания: проверка при смете', () => {
  it('расплывчатое описание — предупреждение с предложением; «Взять в описание» открывает правку с ним', async () => {
    const recorded = await openTab({ [HELP_ROUTE]: { body: HELP_LOOSE } });
    const user = userEvent.setup();

    await countEstimate(user);
    const warning = (
      await screen.findByText('Описание сегмента расплывчатое', {}, SCREEN_WAIT)
    ).closest<HTMLElement>('[role="alert"]');
    expect(warning).not.toBeNull();
    const alert = warning as HTMLElement;
    expect(within(alert).getByText(HELP_LOOSE.description)).toBeInTheDocument();
    expect(within(alert).getByText(HELP_LOOSE.problems[0] ?? '')).toBeInTheDocument();
    expect(calls(recorded, 'POST', HELPER).map((call) => call.body)).toEqual([
      { description: SEGMENT_TEXT },
    ]);

    await user.click(within(alert).getByRole('button', { name: 'Взять в описание' }));

    const field = await screen.findByRole('textbox', { name: 'Описание сегмента' }, SCREEN_WAIT);
    expect(field).toHaveValue(HELP_LOOSE.description);
    await waitFor(() => expect(field).toHaveFocus(), SCREEN_WAIT);
    expect(calls(recorded, 'PATCH', EDIT)).toEqual([]);
  });

  it('конкретное описание — смета без предупреждения; пересчёт помощника второй раз не спрашивает', async () => {
    const recorded = await openTab({ [HELP_ROUTE]: { body: HELP_CONCRETE } });
    const user = userEvent.setup();

    await countEstimate(user);
    await waitFor(() => expect(calls(recorded, 'POST', HELPER)).toHaveLength(1), SCREEN_WAIT);
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await waitFor(
      () => expect(calls(recorded, 'POST', '/api/sales/segment/estimate')).toHaveLength(2),
      SCREEN_WAIT,
    );

    expect(screen.queryByText('Описание сегмента расплывчатое')).not.toBeInTheDocument();
    expect(screen.queryByText('Помощник проверяет описание сегмента…')).not.toBeInTheDocument();
    expect(calls(recorded, 'POST', HELPER)).toHaveLength(1);
  });

  it('«Скрыть» убирает предупреждение по этому описанию', async () => {
    await openTab({ [HELP_ROUTE]: { body: HELP_LOOSE } });
    const user = userEvent.setup();

    await countEstimate(user);
    await screen.findByText('Описание сегмента расплывчатое', {}, SCREEN_WAIT);
    await user.click(screen.getByRole('button', { name: 'Скрыть' }));

    expect(screen.queryByText('Описание сегмента расплывчатое')).not.toBeInTheDocument();
  });

  it('помощник не ответил — строка словами, смета на месте', async () => {
    await openTab({ [HELP_ROUTE]: { status: 502, body: { detail: FAILED } } });
    const user = userEvent.setup();

    await countEstimate(user);

    expect(
      await screen.findByText(`Описание сегмента помощник не проверил: ${FAILED}`, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText(ESTIMATE.lines[0] ?? '')).toBeInTheDocument();
  });
});

describe('помощник описания: расход', () => {
  it('операция помощника на экране «Расход» — своими словами, а не «прочее»', () => {
    expect(operationTitle('sales_segment_helper')).toBe('помощник описания сегмента');
  });
});

describe('помощник описания: окно новой гипотезы', () => {
  it('черновик описания — помощнику, «Взять» — в поле окна, «Завести» шлёт взятое', async () => {
    const made = card(HELP_LOOSE.description);
    const recorded = await openTab(
      {
        [HELP_ROUTE]: { body: HELP_LOOSE },
        'POST /api/sales/hypotheses': { status: 201, body: { ...made, id: 8, name: 'клиники IE' } },
      },
      '/sales?tab=hypotheses',
      'бухгалтерия SP',
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Новая гипотеза' }));
    const dialog = await screen.findByRole('dialog', { name: 'Новая гипотеза' }, SCREEN_WAIT);

    await user.type(within(dialog).getByRole('textbox', { name: /Имя/ }), 'клиники IE');
    const description = within(dialog).getByRole('textbox', { name: /Описание/ });
    await user.click(description);
    await user.paste(DRAFT);
    await user.click(within(dialog).getByRole('button', { name: 'Помочь с описанием' }));
    await within(dialog).findByText(verdictOf(HELP_LOOSE), {}, SCREEN_WAIT);
    await user.click(within(dialog).getByRole('button', { name: 'Взять этот текст' }));
    await user.click(within(dialog).getByRole('button', { name: 'Завести' }));

    await waitFor(
      () =>
        expect(calls(recorded, 'POST', '/api/sales/hypotheses').map((call) => call.body)).toEqual([
          { name: 'клиники IE', description: HELP_LOOSE.description },
        ]),
      SCREEN_WAIT,
    );
    expect(calls(recorded, 'POST', HELPER).map((call) => call.body)).toEqual([
      { description: DRAFT },
    ]);
  });
});
