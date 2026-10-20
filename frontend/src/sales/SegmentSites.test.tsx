/**
 * Все сайты прогона и пересуд — в окне «Сайты прогона» отчёта прогона, подсказка к описанию
 * сегмента. Ответы сервера — записанные (`serve()`), незаписанный запрос роняет тест; домены
 * выдуманы.
 *
 * Проверяется то, ради чего экран: окно закрыто и без запроса, пока его не открыли; вкладки —
 * полосы с числами сервера, вид — в адресе; строка называет полосу, судью, кто решил и итог,
 * лид — ссылкой; страницы и «показаны N из M»; решение «на проверку» — строка стоит на месте;
 * пересуд — смета в окне поверх окна сайтов, запуск по её отпечатку, ход и «было → стало»; без
 * права «run» — кто пересуживает; шаблон описания — в пустое поле.
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
import type { SegmentHome, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import { SEGMENT_TEMPLATE } from './SegmentHint';
import type { RejudgeView, SegmentSite, SegmentSitesPage } from './SegmentSitesApi';

const WAIT = { timeout: 5000 };
const RUN_ID = 21;
const SITES = `/api/sales/segment/runs/${RUN_ID}/sites`;
const REJUDGE = `/api/sales/segment/runs/${RUN_ID}/rejudge`;

const run: SegmentRunRow = {
  id: RUN_ID,
  created_at: '2026-10-12T08:00:00+00:00',
  finished_at: '2026-10-12T08:09:00+00:00',
  created_by: 'seller@ours.example.test',
  country: 'de',
  language: 'en',
  depth_pages: 2,
  keywords: 5,
  mode: 'enforce',
  stage: 'done',
  error: null,
  job_id: null,
};

const home: SegmentHome = {
  hypothesis_id: 7,
  segment: 'Мастерские мебели на заказ: делают и продают свою мебель.',
  mode: 'enforce',
  countries: ['de'],
  languages: ['en'],
  max_keywords: 100,
  max_depth: 10,
  runs: [run],
};

const lanes = { pending: 1, accept: 3, review: 2, reject: 4 };

function detailOf(report: Record<string, unknown> = {}): SegmentRunDetail {
  return {
    run,
    hypothesis: 'мебель DE',
    keywords: ['möbel nach maß'],
    estimate_lines: [],
    summary: { hosts: 10, accepted: 3, review: 2, rejected: 4, no_address: 0, leads: 1 },
    lanes,
    rejected: [],
    no_address: [],
    waiting_address: 0,
    leads: { new: 0, ready: 1, rejected: 0 },
    lead_reasons: {},
    unanswered: 0,
    report,
  };
}

function siteOf(id: number, host: string, extra: Partial<SegmentSite> = {}): SegmentSite {
  return {
    id,
    host,
    page_url: `https://${host}/`,
    page_title: host,
    keyword: 'möbel nach maß',
    lane: 'accept',
    verdict: 'accept',
    decided_by: 'model',
    decided_by_user: null,
    decided_at: '2026-10-12T08:05:00+00:00',
    reason: 'мастерская сама делает мебель',
    quote: 'Möbel nach Maß aus eigener Werkstatt',
    unanswered: false,
    model: 'gpt-5-mini',
    undoable: false,
    judge_reason: null,
    outcome: null,
    change: null,
    ...extra,
  };
}

const counts = { all: 10, accept: 3, review: 2, reject: 4, pending: 1, changed: 0 };

function pageOf(rows: SegmentSite[], extra: Partial<SegmentSitesPage> = {}): SegmentSitesPage {
  return {
    run_id: RUN_ID,
    lane: 'all',
    decider: null,
    page: 1,
    limit: 50,
    total: rows.length,
    lanes: counts,
    deciders: { rule: 1, model: 8, human: 0, none: 1 },
    rows,
    ...extra,
  };
}

const hypotheses: HypothesesView = {
  rows: [
    {
      id: 7,
      name: 'мебель DE',
      description: home.segment,
      created_at: '2026-10-10T10:00:00+00:00',
      leads: { new: 0, ready: 1, rejected: 0 },
      total: 1,
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

async function open(routes: Routes = {}, who: Me = ADMIN, path = ''): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: hypotheses },
    'GET /api/sales/kb': { body: kb },
    'GET /api/sales/segment?hypothesis=7': { body: home },
    [`GET /api/sales/segment/runs/${RUN_ID}`]: { body: detailOf() },
    [`GET ${SITES}?lane=all&page=1`]: { body: pageOf([siteOf(1, 'tischler-sample.example.test')]) },
    ...routes,
  });
  renderWith(<AppRoutes />, `/sales?tab=collect&hypothesis=7${path}`);
  await screen.findByText(`Прогон №${RUN_ID}`, {}, WAIT);
  return recorded;
}

/** Окно «Сайты прогона» — кнопкой у заголовка прогона. */
async function openWindow(): Promise<HTMLElement> {
  await userEvent.setup().click(screen.getByRole('link', { name: 'Сайты прогона · 10' }));
  return screen.findByRole('dialog', { name: `Сайты прогона №${RUN_ID}` }, WAIT);
}

function gets(recorded: Recorded, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === 'GET' && call.path === path);
}

describe('сайты прогона', () => {
  it('окно закрыто — без запроса; «Сайты прогона · N» открывает его на «Все»', async () => {
    const recorded = await open({
      [`GET ${SITES}?lane=all&page=1`]: {
        body: pageOf([
          siteOf(1, 'werkstatt-sample.example.test', {
            outcome: {
              kind: 'lead',
              words: 'лид заведён',
              lead_id: 5,
              lead_status: 'ready',
              lead_reason: null,
            },
          }),
          siteOf(2, 'branchenbuch-sample.example.test', {
            lane: 'reject',
            verdict: 'reject',
            decided_by: 'human',
            decided_by_user: 'seller@ours.example.test',
            reason: 'отказал человек',
          }),
        ]),
      },
    });

    expect(gets(recorded, `${SITES}?lane=all&page=1`)).toHaveLength(0);
    expect(screen.queryByRole('dialog')).toBeNull();
    const dialog = await openWindow();

    const table = await within(dialog).findByRole('table', {}, WAIT);
    const lead = within(table).getByRole('link', { name: 'лид · готов' });
    expect(lead).toHaveAttribute('href', '/sales?search=werkstatt-sample.example.test');
    expect(within(table).getAllByText('«Möbel nach Maß aus eigener Werkstatt»'));
    expect(within(table).getByText('человек · seller')).toHaveAttribute(
      'title',
      'seller@ours.example.test',
    );
    expect(within(dialog).getByRole('radio', { name: 'Все · 10' })).toBeChecked();
    expect(within(dialog).getByRole('radio', { name: 'На проверку · 2' })).toBeInTheDocument();
    expect(within(dialog).getByText('Показаны 1–2 из 2.')).toBeInTheDocument();
  });

  it('вкладка уходит на сервер и в адрес; на «На проверку» — решение, строка стоит на месте', async () => {
    const review = siteOf(3, 'portal-sample.example.test', {
      lane: 'review',
      verdict: 'reject',
      reason: 'судья в наблюдении отрезал бы: справочник компаний',
    });
    const recorded = await open(
      {
        [`GET ${SITES}?lane=review&page=1`]: {
          body: pageOf([review], { lane: 'review', total: 2 }),
        },
        'POST /api/sales/segment/candidates/3/decision': {
          body: {
            ...review,
            lane: 'reject',
            decided_by: 'human',
            decided_by_user: ADMIN.email,
            reason: 'отказал человек',
            undoable: true,
            judge_reason: review.reason,
          },
        },
      },
      ADMIN,
      `&sites_run=${RUN_ID}&sites=review`,
    );
    const user = userEvent.setup();

    const table = await screen.findByRole('table', {}, WAIT);
    await user.click(within(table).getByRole('button', { name: 'Отказ' }));

    await waitFor(
      () => expect(within(table).getByText('решено: отсечён')).toBeInTheDocument(),
      WAIT,
    );
    expect(within(table).getByText('portal-sample.example.test', { exact: false }));
    expect(screen.getByRole('radio', { name: 'На проверку · 1' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Отсечены · 5' })).toBeInTheDocument();
    const posted = recorded.calls.find((call) => call.method === 'POST');
    expect(posted?.body).toEqual({ accept: false });
    expect(gets(recorded, `${SITES}?lane=review&page=1`)).toHaveLength(1);
  });

  it('страницы считает сервер: «показаны 51–53 из 53», вторая страница — своим запросом', async () => {
    const many = Array.from({ length: 50 }, (_, n) => siteOf(100 + n, `site-${n}.example.test`));
    const rest = Array.from({ length: 3 }, (_, n) => siteOf(200 + n, `tail-${n}.example.test`));
    await open(
      {
        [`GET ${SITES}?lane=accept&page=1`]: { body: pageOf(many, { total: 53, lane: 'accept' }) },
        [`GET ${SITES}?lane=accept&page=2`]: {
          body: pageOf(rest, { total: 53, lane: 'accept', page: 2 }),
        },
      },
      ADMIN,
      `&sites_run=${RUN_ID}&sites=accept`,
    );
    const user = userEvent.setup();

    expect(await screen.findByText('Показаны 1–50 из 53.', {}, WAIT)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('Показаны 51–53 из 53.', {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText('tail-0.example.test', { exact: false })).toBeInTheDocument();
  });

  it('пустая вкладка говорит почему, а не молчит', async () => {
    await open(
      { [`GET ${SITES}?lane=pending&page=1`]: { body: pageOf([], { lane: 'pending' }) } },
      ADMIN,
      `&sites_run=${RUN_ID}&sites=pending`,
    );

    expect(
      await screen.findByText('Судья прошёл все сайты прогона.', {}, WAIT),
    ).toBeInTheDocument();
  });
});

const estimate: RejudgeView['estimate'] = {
  sites: 9,
  model: 8,
  per_site: 1077,
  tokens: 8616,
  tokens_left: null,
  kept_human: 0,
  with_lead: 1,
  pending: 1,
  digest: 'digest-of-segment',
  mode: 'enforce',
  resumes: false,
  done: 0,
  lines: [
    'Пересудить сайтов: 9 — по тексту, который видел прошлый суд, мимо кэша вердиктов; выдачу второй раз не покупаем.',
    'К модели — 8, токенов ≈ 8 616 (≈ 1 077 на сайт по прошлым вердиктам).',
  ],
  refusal: null,
};

describe('пересуд прогона', () => {
  it('смета в окне, запуск по её отпечатку, ход и «было → стало»', async () => {
    let posted = false;
    let after = 0;
    const going: RejudgeView = {
      run_id: RUN_ID,
      estimate,
      last: {
        state: 'going',
        by: ADMIN.email,
        asked_at: '2026-10-14T09:00:00+00:00',
        started_at: '2026-10-14T09:00:02+00:00',
        finished_at: null,
        total: 9,
        done: 4,
        human: 0,
        kept_human: 0,
        pending: 1,
        moved: [],
        tokens: 4308,
        unanswered: 0,
        failed: 0,
        error: null,
      },
      going: true,
    };
    const done: RejudgeView = {
      ...going,
      last: {
        ...going.last!,
        state: 'done',
        done: 9,
        finished_at: '2026-10-14T09:01:30+00:00',
        moved: [
          { was: 'accept', now: 'reject', would: false, count: 3 },
          { was: 'accept', now: 'reject', would: true, count: 1 },
        ],
      },
      going: false,
    };
    const recorded = await open({
      [`GET ${REJUDGE}`]: () => {
        if (!posted) return { body: { run_id: RUN_ID, estimate, last: null, going: false } };
        after += 1;
        return { body: after === 1 ? going : done };
      },
      [`POST ${REJUDGE}`]: () => {
        posted = true;
        return { status: 202, body: { run_id: RUN_ID, job_id: 'sales-segment-rejudge-21' } };
      },
    });
    const user = userEvent.setup();

    const sites = await openWindow();
    await user.click(screen.getByRole('button', { name: 'Пересудить прогон' }));
    const dialog = await screen.findByRole(
      'dialog',
      { name: `Пересудить прогон №${RUN_ID}` },
      WAIT,
    );
    expect(await within(dialog).findByText(estimate.lines[1]!, {}, WAIT)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Пересудить · 9' }));

    await waitFor(
      () =>
        expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
          digest: 'digest-of-segment',
        }),
      WAIT,
    );
    expect(await within(sites).findByText('Пересуд: 4 из 9', {}, WAIT)).toBeInTheDocument();
    expect(
      await screen.findByText(/сменили полосу 3 из 9/, {}, { timeout: 8000 }),
    ).toBeInTheDocument();
    const moves = screen.getByRole('list', { name: 'Было → стало' });
    expect(
      within(moves)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['принят → отсечён — 3', 'стало бы у сайтов с лидом: принят → отсечён — 1']);
  }, 15_000);

  it('отказ сметы — словами в окне, кнопка закрыта', async () => {
    const refusal = 'прогон идёт — пересуд после его итога';
    await open({
      [`GET ${REJUDGE}`]: {
        body: { run_id: RUN_ID, estimate: { ...estimate, refusal }, last: null, going: false },
      },
    });
    const user = userEvent.setup();

    await openWindow();
    await user.click(screen.getByRole('button', { name: 'Пересудить прогон' }));

    const dialog = await screen.findByRole(
      'dialog',
      { name: `Пересудить прогон №${RUN_ID}` },
      WAIT,
    );
    expect(await within(dialog).findByText(refusal, {}, WAIT)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Пересудить · 9' })).toBeDisabled();
  });

  it('пропавшая задача — «оборвался», а не вечное «идёт»', async () => {
    const lost: RejudgeView = {
      run_id: RUN_ID,
      estimate,
      last: {
        state: 'going',
        by: ADMIN.email,
        asked_at: null,
        started_at: null,
        finished_at: null,
        total: 9,
        done: 2,
        human: 0,
        kept_human: 0,
        pending: 0,
        moved: [],
        tokens: 0,
        unanswered: 0,
        failed: 0,
        error: null,
      },
      going: false,
    };
    await open({
      [`GET /api/sales/segment/runs/${RUN_ID}`]: {
        body: detailOf({ rejudge: { state: 'going' } }),
      },
      [`GET ${REJUDGE}`]: { body: lost },
    });

    // Оборвался — видно и в отчёте, не открывая окна: ждёт человека.
    expect(await screen.findByText('Пересуд оборвался', {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText(/сделано 2 из 9/)).toBeInTheDocument();
    // Описание с тех пор поправили (смета — не продолжение): пересуд начнётся заново.
    expect(screen.getByText(/пересуд начнётся заново/)).toBeInTheDocument();
    const dialog = await openWindow();
    expect(within(dialog).getByText('Пересуд оборвался')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Пересудить прогон' })).toBeInTheDocument();
  });

  it('остановленный потолком пересуд того же описания — «Продолжить пересуд» с места остановки', async () => {
    const left = {
      ...estimate,
      sites: 7,
      model: 6,
      done: 2,
      resumes: true,
      lines: [
        'Продолжить пересуд с места остановки: осталось 7 из 9 — то же описание и снимок полос прошлого запуска: пересуженные сайты к модели второй раз не пойдут, «было» у них прежнее.',
      ],
    };
    const stopped: RejudgeView = {
      run_id: RUN_ID,
      estimate: left,
      last: {
        state: 'stopped',
        by: ADMIN.email,
        asked_at: null,
        started_at: null,
        finished_at: '2026-10-12T09:00:00+00:00',
        total: 9,
        done: 2,
        human: 0,
        kept_human: 0,
        pending: 0,
        moved: [],
        tokens: 2154,
        unanswered: 0,
        failed: 0,
        error: 'пересуд остановлен: дневной потолок вызовов модели продаж выбран',
      },
      going: false,
    };
    await open({
      [`GET /api/sales/segment/runs/${RUN_ID}`]: {
        body: detailOf({ rejudge: { state: 'stopped' } }),
      },
      [`GET ${REJUDGE}`]: { body: stopped },
    });
    const user = userEvent.setup();

    expect(await screen.findByText('Пересуд остановлен', {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText(/пойдёт с места остановки/)).toBeInTheDocument();
    await openWindow();
    await user.click(screen.getByRole('button', { name: 'Продолжить пересуд' }));
    const dialog = await screen.findByRole('dialog', {
      name: `Продолжить пересуд прогона №${RUN_ID}`,
    });
    expect(within(dialog).getByText(/осталось 7 из 9/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Продолжить · 7' })).toBeEnabled();
  });

  it('без права «run» — кто пересуживает, а кнопки нет', async () => {
    await open({}, { ...OPERATOR, permissions: ['sales', 'view'] });
    const dialog = await openWindow();

    expect(within(dialog).queryByRole('button', { name: 'Пересудить прогон' })).toBeNull();
    expect(
      within(dialog).getByText(`Пересуживает сотрудник с правом «${PERMISSION_TITLES.run}».`),
    ).toBeInTheDocument();
  });
});

describe('подсказка к описанию сегмента', () => {
  it('шаблон — в пустое поле; кто подходит, кто нет и чем отличить', async () => {
    await open();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Изменить' }));
    const field = screen.getByRole('textbox', { name: 'Описание сегмента' });
    expect(screen.queryByRole('button', { name: 'Вставить шаблон' })).toBeNull();
    await user.clear(field);
    await user.click(screen.getByRole('button', { name: 'Вставить шаблон' }));

    expect(field).toHaveValue(SEGMENT_TEMPLATE);
    await user.click(screen.getByRole('button', { name: 'Как описать сегмент' }));
    // В jsdom высота раскрытия не анимируется: раскрыто — по `aria-expanded`.
    expect(screen.getByRole('button', { name: 'Скрыть подсказку' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(
      screen.getByText(/справочники и каталоги, агрегаторы и маркетплейсы/),
    ).toBeInTheDocument();
  });
});
