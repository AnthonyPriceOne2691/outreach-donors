/**
 * «Переписать шаблонные · N» — письма очереди, оставшиеся шаблоном из-за отказа модели.
 *
 * Проверяется то, ради чего кнопка: её нет, пока таких писем нет; число на ней — число сервера;
 * переписывание уходит только после подтверждения, где сказано, сколько писем и что это вызовы
 * модели; на сервер уходят этап и аудитория; итог задачи — словами; пока очередь уходит сама,
 * кнопка не нажимается и говорит почему; без права на отправку её нет.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { SENDING_WHY, confirmText, rewriteLine } from './RewriteTemplated';

const TEMPLATED = {
  id: 1,
  host: 'one.example.test',
  email: 'info@one.example.test',
  campaign: 'Автокруг',
  status: 'queued',
  subject: 'Placement on one.example.test',
  body: 'Hi,\n\nWe saw your link.\n\nBest regards,\nAlex',
  uniqueness: 0,
  verdict: 'отличие 0% ниже коридора 15–25%: письмо слишком похоже на шаблон',
  followups: [],
};

const VIEW = {
  letters: [TEMPLATED, { ...TEMPLATED, id: 2, host: 'two.example.test' }],
  followup_default: [7, 14],
  letter_default: { subject: 'Placement on {{host}}', zones: [] },
  blocked_by: [],
  transport: { name: 'sendgrid', real: true, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { 'ещё не писали': 0 },
  queued_total: 2,
  batch_max: 200,
  templated: 2,
};

const JOB = 'letters-rewrite-donors-links';

const DONE = {
  job_id: JOB,
  kind: 'переписывание шаблонных писем',
  state: 'done',
  title: 'готово',
  error: null,
  report: {
    found: 2,
    rewritten: 1,
    tokens: 120,
    off_corridor: 0,
    left: { 'у модели нет кредитов — пополните счёт OpenAI': 1 },
    notes: {},
    stopped: null,
    remaining: 1,
  },
  retries_left: null,
  next_try_at: null,
  ended_at: null,
};

const RUNNING = {
  available: true,
  running: true,
  since: '2026-11-23T07:05:00Z',
  started_by: 'отправитель@site.com',
  sent: 0,
  state: null,
  passed_at: null,
  off: null,
  stopped_at: null,
  stopped_by: null,
  stop_reason: null,
};

function open(
  view: Record<string, unknown> = {},
  routes: Record<string, unknown> = {},
  who: unknown = ADMIN,
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/letters': { body: { ...VIEW, ...view } },
    'GET /api/runs/with-accepted': { body: [] },
    ...NO_STUCK_LETTERS,
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/letters');
  return recorded;
}

describe('переписать шаблонные письма', () => {
  it('переписывание уходит после подтверждения, и итог сказан словами', async () => {
    const recorded = open(
      {},
      {
        'POST /api/letters/rewrite': { body: { job_id: JOB, letters: 2 } },
        [`GET /api/jobs/${JOB}`]: { body: DONE },
      },
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Переписать шаблонные · 2' }));
    const ask = await screen.findByRole('dialog');
    expect(within(ask).getByText(confirmText(2))).toBeInTheDocument();
    expect(within(ask).getByText(/2 письма очереди, оставшихся шаблоном/)).toBeInTheDocument();
    expect(within(ask).getByText(/вызов модели/)).toBeInTheDocument();
    await user.click(within(ask).getByRole('button', { name: 'Переписать' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.path === '/api/letters/rewrite')?.body).toEqual({
        stage: 'donors',
      }),
    );
    expect(
      await screen.findByText(
        'Переписано 1 из 2. Не переписано: у модели нет кредитов — пополните счёт OpenAI — 1. ' +
          'Шаблоном в очереди осталось 1 письмо.',
      ),
    ).toBeInTheDocument();
  });

  it('«Отмена» ничего не отправляет', async () => {
    const recorded = open();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Переписать шаблонные · 2' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отмена' }),
    );

    expect(recorded.calls.some((call) => call.path === '/api/letters/rewrite')).toBe(false);
  });

  it('шаблонных писем нет — кнопки нет', async () => {
    open({ templated: 0 });

    await screen.findByRole('button', { name: 'Отправить очередь · 2' });
    expect(screen.queryByRole('button', { name: /Переписать шаблонные/ })).toBeNull();
  });

  it('очередь уходит сама — кнопка не нажимается и говорит почему', async () => {
    open(
      { autosend: RUNNING },
      {
        'GET /api/letters/forecast?stage=donors&days=31&back=0': {
          body: { queue_eta: { day: null, sending_days: null, hours: null, words: '' } },
        },
      },
    );

    const button = await screen.findByRole('button', { name: 'Переписать шаблонные · 2' });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(SENDING_WHY);
  });

  it('без права на отправку кнопки нет', async () => {
    open({}, {}, OPERATOR);

    await screen.findByRole('heading', { name: 'Очередь' });
    expect(screen.queryByRole('button', { name: /Переписать шаблонные/ })).toBeNull();
  });

  it('отказ сервера — уведомлением со словами сервера', async () => {
    open(
      {},
      {
        'POST /api/letters/rewrite': {
          status: 409,
          body: { detail: 'Шаблонных писем в этой очереди нет — переписывать нечего' },
        },
      },
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Переписать шаблонные · 2' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Переписать' }),
    );

    expect(
      await screen.findByText('Шаблонных писем в этой очереди нет — переписывать нечего'),
    ).toBeInTheDocument();
  });
});

describe('у каждой вкладки — свои шаблонные', () => {
  it('«Бизнесам ниши» называет свои и шлёт свою аудиторию', async () => {
    const niche = { ...VIEW, stage: 'advertisers', audience: 'niche', templated: 1 };
    const recorded = open(
      {},
      {
        'GET /api/letters?stage=advertisers&audience=niche': { body: niche },
        'POST /api/letters/rewrite': {
          body: { job_id: 'letters-rewrite-advertisers-niche', letters: 1 },
        },
        'GET /api/jobs/letters-rewrite-advertisers-niche': {
          body: { ...DONE, job_id: 'letters-rewrite-advertisers-niche' },
        },
      },
    );
    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'Переписать шаблонные · 2' });

    await user.click(screen.getByRole('radio', { name: 'Бизнесам ниши' }));
    await user.click(await screen.findByRole('button', { name: 'Переписать шаблонные · 1' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Переписать' }),
    );

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.path === '/api/letters/rewrite')?.body).toEqual({
        stage: 'advertisers',
        audience: 'niche',
      }),
    );
    expect(localStorage.getItem('letters:last-rewrite:advertisers:niche')).toBe(
      'letters-rewrite-advertisers-niche',
    );
  });
});

describe('итог переписывания строкой', () => {
  it('всё переписано — без «не переписано» и без остатка', () => {
    expect(rewriteLine({ found: 68, rewritten: 68, left: {}, stopped: null, remaining: 0 })).toBe(
      'Переписано 68 из 68.',
    );
  });

  it('остановка и остаток — словами', () => {
    expect(
      rewriteLine({
        found: 8,
        rewritten: 0,
        left: { 'модель вернула текст шаблона': 1 },
        stopped: 'потолок расхода на модель за день достигнут',
        remaining: 8,
      }),
    ).toBe(
      'Переписано 0 из 8. Не переписано: модель вернула текст шаблона — 1. ' +
        'Остановлено: потолок расхода на модель за день достигнут. ' +
        'Шаблоном в очереди осталось 8 писем.',
    );
  });
});
