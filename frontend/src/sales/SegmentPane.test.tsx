/**
 * Вкладка «Сбор базы» — прогон гипотезы (срез 6.1): описание сегмента, смета до запуска,
 * запуск, отчёт прогона и решение человека по «на проверку». Ответы сервера — записанные
 * (`serve()`), незаписанный запрос роняет тест; домены и адреса выдуманы.
 *
 * Проверяется то, ради чего вкладка: смета — словами сервера до кнопки, запуск открыт только
 * по смете тех же значений, отказ сметы закрывает запуск; отчёт называет полосы, причины
 * отсева и «без адреса» словами; «Принять» и «Отказ» уходят на сервер; остановленный прогон
 * продолжается кнопкой; без права «run» кнопок трат нет, а сказано, кто запускает.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { PERMISSION_TITLES } from '../api/labels';
import type { HypothesesView } from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { MODE_WORDS } from './segmentData';
import { HELP_QUIET } from './segmentHelpFixtures';
import type {
  SegmentCandidate,
  SegmentEstimate,
  SegmentHome,
  SegmentRunDetail,
  SegmentRunRow,
} from './segmentApi';

const SCREEN_WAIT = { timeout: 5000 };
const SEGMENT_TEXT = 'Интернет-магазины велосипедов: продают велосипеды у себя на сайте.';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 5,
      name: 'сайты EN',
      description: SEGMENT_TEXT,
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 0, ready: 3, rejected: 0 },
      total: 3,
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
  id: 12,
  created_at: '2026-10-11T09:30:00+00:00',
  finished_at: '2026-10-11T09:41:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'us',
  language: 'en',
  depth_pages: 1,
  keywords: 3,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: 'sales-segment-12',
};

const HOME: SegmentHome = {
  hypothesis_id: 5,
  segment: SEGMENT_TEXT,
  mode: 'shadow',
  countries: ['de', 'kz', 'us'],
  languages: ['ru', 'en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [RUN],
};

const DETAIL: SegmentRunDetail = {
  run: RUN,
  hypothesis: 'сайты EN',
  keywords: ['купить горный велосипед'],
  estimate_lines: ['Выдача: ключей 3 × страниц 1 — результатов до 30, ≈ $0,0018.'],
  summary: { hosts: 7, accepted: 3, review: 1, rejected: 3, no_address: 1, leads: 2 },
  lanes: { pending: 0, accept: 3, review: 1, reject: 3 },
  rejected: [
    { words: 'платформа из денилиста: это площадка, а не сайт компании', count: 2 },
    { words: 'пишет о чужих, сам не продаёт', count: 1 },
  ],
  no_address: [{ words: 'адреса на сайте нет', count: 1 }],
  waiting_address: 0,
  leads: { new: 0, ready: 1, rejected: 1 },
  lead_reasons: { duplicate: 1 },
  unanswered: 0,
  report: {},
};

/** Сайт «на проверку» — в списке сайтов прогона: отчёт отдаёт только числа. */
const REVIEW_SITE: SegmentCandidate = {
  id: 41,
  host: 'velo-news-sample.org',
  page_url: 'https://velo-news-sample.org/catalog',
  page_title: 'Новости велоспорта',
  keyword: 'купить горный велосипед',
  lane: 'review',
  verdict: 'reject',
  reason: 'судья в наблюдении отрезал бы: пишет о чужих, сам не продаёт',
  quote: 'Новости велоспорта',
  unanswered: false,
  decided_by_user: null,
};

const ESTIMATE: SegmentEstimate = {
  keywords: ['купить горный велосипед', 'велосипед для города'],
  excluded: [],
  keyword_lines: { 'купить горный велосипед': 1, 'велосипед для города': 2 },
  rejected: { 'site:example.com велосипед': 'поисковый оператор: так пользователи не ищут' },
  notes: {},
  segment: SEGMENT_TEXT,
  digest: 'seen-bikes',
  country: 'us',
  language: 'en',
  depth_pages: 1,
  mode: 'shadow',
  serp_mode: 'live',
  serp_page_usd: 0.002,
  serp_wait_s: 10,
  results: 20,
  share: 0.85,
  share_runs: 0,
  hosts: 17,
  known: 0,
  sites: 17,
  serp_usd: 0.0014,
  tokens: 25500,
  tokens_usd: 0.0153,
  tokens_left: null,
  hunter_searches: 0,
  hunter_checks: 0,
  lines: [
    'Выдача: ключей 2 × страниц 1 — результатов до 20, ≈ $0,0014.',
    'Судья сегмента: сайтов до 17, токенов модели ≈ 25 500.',
  ],
  refusal: null,
};

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openTab(
  routes: Routes = {},
  who: Me = ADMIN,
  home: SegmentHome = HOME,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    ...HELP_QUIET,
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/segment?hypothesis=5': { body: home },
    'GET /api/sales/segment/runs/12': { body: DETAIL },
    'GET /api/jobs/sales-segment-12': {
      body: {
        job_id: 'sales-segment-12',
        kind: 'сбор базы продаж',
        state: 'done',
        title: 'готово',
        error: null,
        report: { leads: { created: 2 } },
        retries_left: null,
        next_try_at: null,
        ended_at: null,
      },
    },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=collect');
  await screen.findByText('Сегмент гипотезы', {}, SCREEN_WAIT);
  return recorded;
}

/** У гипотезы с прогонами форма нового прогона свёрнута в кнопку — раскрываем. */
async function openForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Новый прогон' }));
  await screen.findByRole('button', { name: 'Посчитать смету' }, SCREEN_WAIT);
}

/** Ключи — вставкой, одним изменением поля: по букве набор на нагруженной машине шёл бы
 *  дольше срока теста, а проверяется не клавиатура. */
async function typeKeywords(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.click(screen.getByRole('textbox', { name: 'Ключи' }));
  await user.paste(text);
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

describe('сбор базы: прогон гипотезы', () => {
  it('описание сегмента, форма и отчёт последнего прогона — словами сервера', async () => {
    await openTab();

    expect(screen.getByText(SEGMENT_TEXT)).toBeInTheDocument();
    expect(await screen.findByText('Прогон №12', {}, SCREEN_WAIT)).toBeInTheDocument();
    // Неразрывный пробел в словах («отказал бы») поиск по тексту сводит к обычному — и здесь так же.
    expect(screen.getByText(`${MODE_WORDS.shadow.replace(/\s+/g, ' ')}.`)).toBeInTheDocument();
    // Причины — одной свёрнутой строкой, раскрываются по нажатию.
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'Почему отсечены и без адреса' }));
    const rejected = await screen.findByRole('list', { name: 'Отсечены — почему' });
    expect(
      within(rejected)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      'платформа из денилиста: это площадка, а не сайт компании — 2',
      'пишет о чужих, сам не продаёт — 1',
    ]);
    const noAddress = screen.getByRole('list', { name: 'Без адреса — почему' });
    expect(within(noAddress).getByRole('listitem')).toHaveTextContent('адреса на сайте нет — 1');
    expect(
      screen.getByText(
        'Лидов заведено 2: готовы к письмам 1, отклонены очисткой 1 (дубль — 1), ждут очистки 0.',
      ),
    ).toBeInTheDocument();
    // «На проверку» — строкой со ссылкой на вкладку решений в окне сайтов, а не таблицей.
    expect(screen.getByRole('link', { name: 'На проверку 1 — решить' })).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('смета — до запуска: ключи, рынок, язык и глубина уходят на сервер, слова — его', async () => {
    const recorded = await openTab({
      'POST /api/sales/segment/estimate': { body: ESTIMATE },
    });
    const user = userEvent.setup();
    await openForm(user);

    expect(screen.getByRole('button', { name: 'Запустить прогон' })).toBeDisabled();
    await typeKeywords(user, 'купить горный велосипед');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(
      await screen.findByText(
        'Судья сегмента: сайтов до 17, токенов модели ≈ 25 500.',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
    expect(calls(recorded, 'POST', '/api/sales/segment/estimate')[0]?.body).toEqual({
      hypothesis_id: 5,
      keywords: 'купить горный велосипед',
      country: 'us',
      language: null,
      depth_pages: 1,
      serp_mode: 'live',
      excluded: [],
    });
    expect(
      within(screen.getByRole('list', { name: 'Ключи сметы' }))
        .getByText('поисковый оператор: так пользователи не ищут')
        .closest('li'),
    ).toHaveTextContent('отсеян: site:example.com велосипед');
    expect(screen.getByRole('button', { name: 'Запустить прогон' })).toBeEnabled();
  });

  it('правка поля после сметы закрывает запуск: запустить то, чего не видел, нельзя', async () => {
    await openTab({ 'POST /api/sales/segment/estimate': { body: ESTIMATE } });
    const user = userEvent.setup();
    await openForm(user);
    await typeKeywords(user, 'купить горный велосипед');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await waitFor(
      () => expect(screen.getByRole('button', { name: 'Запустить прогон' })).toBeEnabled(),
      SCREEN_WAIT,
    );

    await typeKeywords(user, '\nвелосипед для города');

    expect(screen.getByRole('button', { name: 'Запустить прогон' })).toBeDisabled();
    expect(screen.queryByText('Судья сегмента: сайтов до 17, токенов модели ≈ 25 500.')).toBeNull();
  });

  it('смета выше потолка: отказ словами, запуск закрыт', async () => {
    const refusal =
      'смета модели выше остатка дневного потолка: ≈ 25 500 токенов при остатке 9 001 — ' +
      'уменьшите число ключей или глубину, или запустите завтра';
    await openTab({
      'POST /api/sales/segment/estimate': { body: { ...ESTIMATE, refusal } },
    });
    const user = userEvent.setup();
    await openForm(user);

    await typeKeywords(user, 'купить горный велосипед');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText(refusal, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.getByText('Прогон не запустится')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Запустить прогон' })).toBeDisabled();
  });

  it('запуск уходит той же формой, что смета, и открывает отчёт нового прогона', async () => {
    const started = {
      ...RUN,
      id: 13,
      stage: 'queued' as const,
      finished_at: null,
      job_id: 'sales-segment-13',
    };
    const recorded = await openTab({
      'POST /api/sales/segment/estimate': { body: ESTIMATE },
      'POST /api/sales/segment/runs': { body: { run_id: 13, job_id: 'sales-segment-13' } },
      'GET /api/sales/segment/runs/13': {
        body: { ...DETAIL, run: started, summary: { ...DETAIL.summary, hosts: 0 } },
      },
      'GET /api/jobs/sales-segment-13': {
        body: {
          job_id: 'sales-segment-13',
          kind: 'сбор базы продаж',
          state: 'queued',
          title: 'в очереди',
          error: null,
          report: null,
          retries_left: 3,
          next_try_at: null,
          ended_at: null,
        },
      },
    });
    const user = userEvent.setup();
    await openForm(user);

    await typeKeywords(user, 'купить горный велосипед');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    const start = await screen.findByRole('button', { name: 'Запустить прогон' }, SCREEN_WAIT);
    await waitFor(() => expect(start).toBeEnabled(), SCREEN_WAIT);
    await user.click(start);

    await waitFor(
      () => expect(calls(recorded, 'POST', '/api/sales/segment/runs')).toHaveLength(1),
      SCREEN_WAIT,
    );
    // Запуск — той же формой, что смета, и с отпечатком сметы, которую видел человек.
    expect(calls(recorded, 'POST', '/api/sales/segment/runs')[0]?.body).toEqual({
      ...(calls(recorded, 'POST', '/api/sales/segment/estimate')[0]?.body as object),
      digest: 'seen-bikes',
    });
    expect(await screen.findByText('Прогон №13', {}, SCREEN_WAIT)).toBeInTheDocument();
  });

  it('решение по «на проверку» уходит на сервер, отчёт перечитывается', async () => {
    const site = {
      ...REVIEW_SITE,
      decided_by: 'model',
      decided_at: '2026-10-11T09:35:00+00:00',
      model: 'gpt-5-mini',
      undoable: false,
      judge_reason: null,
      outcome: null,
      change: null,
    };
    const recorded = await openTab({
      'GET /api/sales/segment/runs/12/sites?lane=review&page=1': {
        body: {
          run_id: 12,
          lane: 'review',
          decider: null,
          page: 1,
          limit: 50,
          total: 1,
          lanes: { all: 7, accept: 3, review: 1, reject: 3, pending: 0, changed: 0 },
          deciders: { rule: 0, model: 1, human: 0, none: 0 },
          rows: [site],
        },
      },
      'POST /api/sales/segment/candidates/41/decision': {
        body: { ...REVIEW_SITE, lane: 'accept', decided_by_user: ADMIN.email },
      },
    });
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole('link', { name: 'На проверку 1 — решить' }, SCREEN_WAIT),
    );
    const review = await screen.findByRole('table', {}, SCREEN_WAIT);

    await user.click(within(review).getByRole('button', { name: 'Принять' }));

    await waitFor(
      () =>
        expect(calls(recorded, 'POST', '/api/sales/segment/candidates/41/decision')).toHaveLength(
          1,
        ),
      SCREEN_WAIT,
    );
    expect(calls(recorded, 'POST', '/api/sales/segment/candidates/41/decision')[0]?.body).toEqual({
      accept: true,
    });
    await waitFor(
      () => expect(calls(recorded, 'GET', '/api/sales/segment/runs/12').length).toBeGreaterThan(1),
      SCREEN_WAIT,
    );
  });

  it('остановленный прогон: причина словами и «Продолжить прогон»', async () => {
    const stopped = {
      ...RUN,
      stage: 'stopped' as const,
      finished_at: null,
      error: 'судья остановлен: дневной потолок вызовов модели продаж выбран: 977 из 977 токенов',
    };
    const recorded = await openTab({
      'GET /api/sales/segment/runs/12': { body: { ...DETAIL, run: stopped } },
      'POST /api/sales/segment/runs/12/resume': {
        body: { run_id: 12, job_id: 'sales-segment-12' },
      },
    });
    const user = userEvent.setup();

    expect(await screen.findByText(stopped.error, {}, SCREEN_WAIT)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Продолжить прогон' }));

    await waitFor(
      () => expect(calls(recorded, 'POST', '/api/sales/segment/runs/12/resume')).toHaveLength(1),
      SCREEN_WAIT,
    );
  });

  it('принятые без поиска адреса — кнопка с их числом', async () => {
    await openTab({
      'GET /api/sales/segment/runs/12': { body: { ...DETAIL, waiting_address: 4 } },
    });

    expect(
      await screen.findByRole('button', { name: 'Найти адреса принятым · 4' }, SCREEN_WAIT),
    ).toBeEnabled();
  });

  it('без права «run» трат нет: смету видно, а у кнопки — кто запускает', async () => {
    const stopped = { ...RUN, stage: 'stopped' as const, error: 'остановлен' };
    await openTab(
      { 'GET /api/sales/segment/runs/12': { body: { ...DETAIL, run: stopped } } },
      { ...OPERATOR, permissions: ['sales', 'view'] },
      { ...HOME, runs: [stopped] },
    );
    await openForm(userEvent.setup());

    expect(screen.queryByRole('button', { name: 'Запустить прогон' })).toBeNull();
    expect(
      screen.getByText(`Запускает сотрудник с правом «${PERMISSION_TITLES.run}».`),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        `Продолжает прогон сотрудник с правом «${PERMISSION_TITLES.run}».`,
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
  });

  it('нет описания сегмента — предупреждение, и описание пишется в гипотезу', async () => {
    const recorded = await openTab(
      {
        'PATCH /api/sales/hypotheses/5': { body: { hypothesis_id: 5, description: 'Магазины.' } },
      },
      ADMIN,
      { ...HOME, segment: null, runs: [] },
    );
    const user = userEvent.setup();

    expect(screen.getByText('Описания сегмента нет')).toBeInTheDocument();
    expect(screen.getByText(/Прогонов у гипотезы пока нет/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Описать сегмент' }));
    await user.type(screen.getByRole('textbox', { name: 'Описание сегмента' }), 'Магазины.');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(
      () => expect(calls(recorded, 'PATCH', '/api/sales/hypotheses/5')).toHaveLength(1),
      SCREEN_WAIT,
    );
    expect(calls(recorded, 'PATCH', '/api/sales/hypotheses/5')[0]?.body).toEqual({
      description: 'Магазины.',
    });
  });
});
