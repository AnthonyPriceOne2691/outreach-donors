/**
 * Очистка лидов кнопкой — на вкладке лидов и в итоге загрузки (до неё — только команда
 * консоли, и лид не становился «готов к письмам»). Ответы сервера — записанные (`serve()`),
 * незаписанный запрос роняет тест; адреса выдуманы.
 *
 * Проверяется то, ради чего кнопка: перед запуском экран спрашивает сервер; живая проверка —
 * окно с числом расхода от сервера, «Отмена» ничего не ставит; выдуманная — без окна, сразу в
 * очередь; отказ — словами там, где нажали; ход задачи — строкой, по окончании лиды
 * перечитаны; без права запуска — объяснение, а не кнопка.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView, IntakeView, LeadsView, SalesCleanView } from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { cleanJobKey, cleanLine } from './cleanData';
import { ImportOutcome } from './ImportReport';

const SCREEN_WAIT = { timeout: 5000 };
const CLEAN = '/api/sales/clean';
const ASK = `${CLEAN}?hypothesis=1`;
const LEADS = '/api/sales/leads?hypothesis=1';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 1,
      name: 'сайты EN',
      description: null,
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 2, ready: 1, rejected: 0 },
      total: 3,
    },
    {
      id: 2,
      name: 'сервисы RU',
      description: null,
      created_at: '2026-10-02T10:00:00+00:00',
      leads: { new: 0, ready: 4, rejected: 0 },
      total: 4,
    },
  ],
  total: 2,
  module_enabled: true,
};

const VIEW: LeadsView = {
  rows: [
    {
      id: 1,
      email: 'ivan@acme.example.test',
      name: null,
      position: null,
      company: null,
      host: 'acme.example.test',
      country: null,
      timezone: null,
      language: null,
      hypothesis_id: 1,
      hypothesis: 'сайты EN',
      source: 'import',
      status: 'new',
      rejection_reason: null,
      cleaning_note: null,
      verification_status: null,
      created_at: '2026-10-03T10:00:00+00:00',
    },
  ],
  total: 1,
  page: 1,
  limit: 20,
  states: { new: 2, ready: 1, rejected: 0 },
  reasons: { duplicate: 0 },
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

const FIXTURE: SalesCleanView = { hypothesis_id: 1, waiting: 2, paid: false };
/** Живая проверка: сервер называет свежее число — не то, что в списке гипотез (2). */
const LIVE: SalesCleanView = { hypothesis_id: 1, waiting: 7, paid: true };

function job(id: string, state: string, report: Record<string, unknown> | null = null) {
  return {
    job_id: id,
    kind: 'очистка лидов продаж',
    state,
    title: state === 'done' ? 'готово' : 'идёт',
    error: null,
    report,
    retries_left: null,
    next_try_at: null,
    ended_at: null,
  };
}

const REPORT = {
  checked: 2,
  ready: 1,
  rejected: { duplicate: 1 },
  unverified: 0,
  mx_unknown: 0,
  verified: 1,
  paid_units: 0,
  verifier: 'fixture',
  stopped: null,
};

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openLeads(
  routes: Routes = {},
  { path = '/sales?hypothesis=1', who = ADMIN }: { path?: string; who?: Me } = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    [`GET ${LEADS}`]: { body: VIEW },
    'GET /api/sales/leads': { body: VIEW },
    'GET /api/sales/leads?hypothesis=2': { body: VIEW },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText('ivan@acme.example.test', {}, SCREEN_WAIT);
  return recorded;
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

/** Залитая ли кнопка: у залитой нет другого вида (`data-variant` — нет или `filled`). */
function isFilled(button: HTMLElement): boolean {
  const variant = button.getAttribute('data-variant');
  return variant === null || variant === 'filled';
}

describe('очистка на вкладке лидов', () => {
  it('у выбранной гипотезы с новыми — строка и кнопка; кнопка не залитая', async () => {
    await openLeads();

    expect(screen.getByText('2 лида ждут очистки')).toBeInTheDocument();
    const clean = screen.getByRole('button', { name: 'Очистить' });
    // Залитая на вкладке — «Загрузить базу»; вторая залитая спорила бы с ней.
    expect(isFilled(clean)).toBe(false);
  });

  it.each([
    ['гипотеза не выбрана — очистка идёт по одной гипотезе', '/sales'],
    ['у гипотезы нет новых — очищать нечего', '/sales?hypothesis=2'],
  ])('%s: строки нет', async (_why, path) => {
    await openLeads({}, { path });

    expect(screen.queryByText(/ждут очистки/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Очистить' })).not.toBeInTheDocument();
  });

  it('выдуманная проверка — без окна, сразу в очередь; итог словами, лиды перечитаны', async () => {
    const recorded = await openLeads({
      [`GET ${ASK}`]: { body: FIXTURE },
      [`POST ${CLEAN}`]: { status: 202, body: { job_id: 'job-c' } },
      'GET /api/jobs/job-c': { body: job('job-c', 'done', REPORT) },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Очистить' }));

    await waitFor(() => expect(calls(recorded, 'POST', CLEAN)).toHaveLength(1), SCREEN_WAIT);
    // Перед запуском экран спросил сервер — и пошёл без окна: проверка не платная.
    expect(calls(recorded, 'GET', ASK)).toHaveLength(1);
    expect(calls(recorded, 'POST', CLEAN)[0]?.body).toEqual({ hypothesis_id: 1 });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      await screen.findByText('Очистка лидов продаж: готово', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Проверено 2: готово 1, отклонено 1 (дубль — 1), не проверено 0.'),
    ).toBeInTheDocument();
    // Кончилась — лиды и счётчики раздела перечитаны, а номер задачи забыт: итог не висит
    // на вкладке неделю после перезагрузки.
    await waitFor(() => expect(calls(recorded, 'GET', LEADS).length).toBeGreaterThan(1));
    expect(calls(recorded, 'GET', '/api/sales/hypotheses').length).toBeGreaterThan(1);
    expect(localStorage.getItem(cleanJobKey(1))).toBeNull();
  });

  it('вторая очистка гипотезы — строка об исходе новой: номер задачи постоянный', async () => {
    const reports = [REPORT, { ...REPORT, checked: 1, ready: 1, rejected: {} }];
    let started = 0;
    const recorded = await openLeads({
      [`GET ${ASK}`]: { body: FIXTURE },
      [`POST ${CLEAN}`]: () => {
        started += 1;
        return { status: 202, body: { job_id: 'sales-clean-1' } };
      },
      'GET /api/jobs/sales-clean-1': () => ({
        body: job('sales-clean-1', 'done', reports[Math.max(0, started - 1)] ?? REPORT),
      }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Очистить' }));
    expect(
      await screen.findByText(/^Проверено 2: готово 1, отклонено 1/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Очистить' }));

    // Под тем же номером — новая задача: строка спросила о ней заново, а не держит прежний итог.
    expect(
      await screen.findByText(/^Проверено 1: готово 1, отклонено 0/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(calls(recorded, 'POST', CLEAN)).toHaveLength(2);
  });

  it('живая проверка — окно с числом сервера; «Отмена» ничего не ставит', async () => {
    const recorded = await openLeads({
      [`GET ${ASK}`]: { body: LIVE },
      [`POST ${CLEAN}`]: { status: 202, body: { job_id: 'job-l' } },
      'GET /api/jobs/job-l': { body: job('job-l', 'running') },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Очистить' }));
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    expect(
      within(dialog).getByText('Проверка адресов платная: до 7 запросов Hunter.'),
    ).toBeInTheDocument();
    // Фокус — на «Отмене»: Enter по привычке денег не тратит.
    const cancel = within(dialog).getByRole('button', { name: 'Отмена' });
    await waitFor(() => expect(cancel).toHaveFocus(), SCREEN_WAIT);
    await user.click(cancel);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(calls(recorded, 'POST', CLEAN)).toEqual([]);

    await user.click(screen.getByRole('button', { name: 'Очистить' }));
    const again = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    await user.click(within(again).getByRole('button', { name: 'Очистить' }));

    await waitFor(() => expect(calls(recorded, 'POST', CLEAN)).toHaveLength(1), SCREEN_WAIT);
    expect(await screen.findByText('Очистка лидов продаж: идёт', {}, SCREEN_WAIT)).toBeVisible();
    // Идущая очистка переживает перезагрузку: номер задачи помнится по гипотезе.
    expect(localStorage.getItem(cleanJobKey(1))).toBe('job-l');
  });

  it('пока смотрели, лидов очистили — ни окна, ни запуска: раздел перечитан, строка ушла', async () => {
    let asked = false;
    const recorded = await openLeads({
      [`GET ${ASK}`]: () => {
        asked = true;
        return { body: { ...LIVE, waiting: 0 } };
      },
      // После вопроса сервер знает: новых у гипотезы уже нет.
      'GET /api/sales/hypotheses': () => ({
        body: asked
          ? {
              ...HYPOTHESES,
              rows: [
                { ...HYPOTHESES.rows[0], leads: { new: 0, ready: 3, rejected: 0 } },
                HYPOTHESES.rows[1],
              ],
            }
          : HYPOTHESES,
      }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Очистить' }));

    await waitFor(() => expect(screen.queryByText(/ждут очистки/)).not.toBeInTheDocument());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(calls(recorded, 'POST', CLEAN)).toEqual([]);
  });

  it('отказ на запуск при живой проверке — словами в окне, окно остаётся', async () => {
    const running = 'Очистка лидов этой гипотезы уже идёт — дождитесь её итога';
    await openLeads({
      [`GET ${ASK}`]: { body: LIVE },
      [`POST ${CLEAN}`]: { status: 409, body: { detail: running } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Очистить' }));
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    await user.click(within(dialog).getByRole('button', { name: 'Очистить' }));

    const alert = (await within(dialog).findByText('Очистка не запущена', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent(running);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // Одним местом: две одинаковые плашки — в окне и под кнопкой — читались бы двумя отказами.
    expect(screen.getAllByText('Очистка не запущена')).toHaveLength(1);
  });

  it('отказ на запуск при выдуманной проверке — словами под кнопкой', async () => {
    const running = 'Очистка лидов этой гипотезы уже идёт — дождитесь её итога';
    await openLeads({
      [`GET ${ASK}`]: { body: FIXTURE },
      [`POST ${CLEAN}`]: { status: 409, body: { detail: running } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Очистить' }));

    const alert = (await screen.findByText('Очистка не запущена', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent(running);
  });

  it('проверяльщик не настроен — отказ сервера словами у кнопки, в очередь ничего', async () => {
    const misconfigured =
      'Очистка не запустится: проверка адресов не настроена — настраивает администратор';
    const recorded = await openLeads({
      [`GET ${ASK}`]: { status: 409, body: { detail: misconfigured } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Очистить' }));

    expect(await screen.findByText(misconfigured, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(calls(recorded, 'POST', CLEAN)).toEqual([]);
  });

  it('без права запуска — объяснение на месте, кнопки нет', async () => {
    await openLeads({}, { who: { ...OPERATOR, permissions: ['sales', 'view'] } });

    expect(screen.getByText('2 лида ждут очистки')).toBeInTheDocument();
    expect(
      screen.getByText('Очистку запускает сотрудник с правом «запускать прогоны».'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Очистить' })).not.toBeInTheDocument();
  });
});

const OUTCOME: IntakeView = {
  source: 'leads.csv',
  header: true,
  columns: ['Почта'],
  sample: [],
  mapping: { email: 0 },
  needs_mapping: false,
  rows: 100,
  accepted: 97,
  rejected: 3,
  leads: [],
  problems: [],
  loaded: 97,
};

describe('очистка в итоге загрузки', () => {
  it('очистка — главная кнопка итога, «К лидам» — обычная; команд консоли нет', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN } });

    renderWith(
      <ImportOutcome
        outcome={OUTCOME}
        hypothesisId={1}
        hypothesisName="сайты EN"
        waiting={97}
        onAgain={() => undefined}
      />,
      '/sales/import',
    );

    const clean = await screen.findByRole('button', { name: 'Очистить' }, SCREEN_WAIT);
    expect(screen.getByText('97 лидов ждут очистки')).toBeInTheDocument();
    expect(isFilled(clean)).toBe(true);
    expect(isFilled(screen.getByRole('link', { name: 'К лидам' }))).toBe(false);
    expect(document.body.textContent).not.toMatch(/outreach /);
  });
});

describe('итог очистки словами', () => {
  it('платные проверки и остановка платной части — сказаны', () => {
    expect(
      cleanLine({
        ...REPORT,
        checked: 5,
        ready: 1,
        rejected: { duplicate: 1, no_mail: 2 },
        unverified: 1,
        paid_units: 1,
        stopped: 'квота исчерпана: monthly',
      }),
    ).toBe(
      'Проверено 5: готово 1, отклонено 3 (домен не принимает почту — 2, дубль — 1), ' +
        'не проверено 1. Платных проверок адреса: 1. Платная часть остановлена: квота ' +
        'исчерпана: monthly — непроверенные остались новыми, повторите очистку, когда ' +
        'причина снята.',
    );
  });

  it('непроверенные без остановки — повторит следующая; пустой проход — так и сказано', () => {
    expect(cleanLine({ ...REPORT, ready: 0, rejected: {}, unverified: 2 })).toBe(
      'Проверено 2: готово 0, отклонено 0, не проверено 2. ' +
        'Непроверенные остались новыми — следующая очистка повторит их.',
    );
    expect(cleanLine({ ...REPORT, checked: 0 })).toBe(
      'Лидов, ждущих очистки, не было — проверять нечего.',
    );
  });
});
