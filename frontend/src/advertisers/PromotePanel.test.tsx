/**
 * «К письму»: перевод в рекламодатели и поиск им адресов — кнопками.
 *
 * Проверяется то, ради чего панель: видно, сколько переведёт кнопка;
 * перевод уходит на сервер и его итог сказан словами; поиск адресов,
 * поставленный переводом, виден ходом; без права `run` кнопок нет,
 * а «переводить некого» объяснено, а не выключено молча.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { PromoteResult } from '../api/advertisers';
import { OPERATOR, PROMOTE_ROUTES, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { PromotePanel, promotedLine } from './PromotePanel';

const READY = { ready: 2, fresh: 2, advertisers: 0, with_address: 0 };

const PROMOTED: PromoteResult = {
  report: {
    рассмотрено: 2,
    заведено: 2,
    обновлено: 0,
    'донор в стоп-листе поставщиков': 0,
    'адресат в общем стоп-листе': 0,
    'снято с уже заведённых': 0,
    'подтверждено человеком': 1,
  },
  fresh: ['bought.example', 'yes.example'],
  pending: 2,
  contacts_job_id: 'job-7',
};

const SEARCHING = {
  job_id: 'job-7',
  kind: 'поиск контактов',
  state: 'running',
  title: 'идёт',
  error: null,
  report: null,
  retries_left: null,
  next_try_at: null,
  ended_at: null,
};

function open(routes: Record<string, unknown> = {}, who: unknown = OPERATOR) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    ...PROMOTE_ROUTES,
    'GET /api/auth/me': { body: who },
    ...(routes as Record<string, never>),
  });
  renderWith(<PromotePanel />);
  return recorded;
}

describe('к письму', () => {
  it('показывает, сколько переведёт кнопка, и переводит', async () => {
    const recorded = open({
      'GET /api/advertisers/promotion': { body: READY },
      'POST /api/advertisers/promote': { body: PROMOTED },
      'GET /api/jobs/job-7': { body: SEARCHING },
    });
    const user = userEvent.setup();

    expect(await screen.findByText(/к переводу: 2, новых 2/)).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Перевести в рекламодатели' }));

    const said = 'Перевод: заведено 2, обновлено 0; ищем адреса: 2.';
    expect((await screen.findAllByText(said)).length).toBeGreaterThan(0);
    expect(recorded.calls.some((call) => call.path === '/api/advertisers/promote')).toBe(true);
    // Поиск адресов поставил сам перевод — его ход виден, а не тишина.
    expect(await screen.findByText('Поиск контактов: идёт')).toBeInTheDocument();
  });

  it('переводить некого — кнопка закрыта, и сказано почему', async () => {
    open();

    expect(await screen.findByRole('button', { name: 'Перевести в рекламодатели' })).toBeDisabled();
    expect(screen.getByText(/Переводить некого: в «куплено» пусто/)).toBeInTheDocument();
  });

  it('без права на запуск — без кнопок: поиск адресов тратит деньги', async () => {
    open(
      {
        'GET /api/advertisers/promotion': { body: READY },
        'GET /api/advertisers/contacts': {
          body: { pending: 3, running: false, job_id: null, last: null, workers: 1 },
        },
      },
      { ...OPERATOR, permissions: ['view'] },
    );

    expect(await screen.findByText(/к переводу: 2/)).toBeInTheDocument();
    expect(await screen.findByText('3')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Перевести в рекламодатели' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'найти адреса всем ждущим' }),
    ).not.toBeInTheDocument();
  });

  it('«найти» ищет адреса рекламодателям, а не донорам', async () => {
    const recorded = open({
      'GET /api/advertisers/contacts': {
        body: { pending: 3, running: false, job_id: null, last: null, workers: 1 },
      },
      'POST /api/advertisers/contacts': { body: { job_id: 'job-7', pending: 3 } },
      'GET /api/jobs/job-7': { body: SEARCHING },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'найти адреса всем ждущим' }));

    await waitFor(() =>
      expect(
        recorded.calls.find(
          (call) => call.path === '/api/advertisers/contacts' && call.method === 'POST',
        )?.body,
      ).toEqual({ limit: 100 }),
    );
    expect(recorded.calls.some((call) => call.path === '/api/contacts')).toBe(false);
  });
});

describe('панель не пропадает молча', () => {
  it('не загрузилось — сказано словами', async () => {
    open({
      'GET /api/advertisers/promotion': { status: 500, body: { detail: 'база не ответила' } },
    });

    expect(await screen.findByText('«К письму» не загрузилось')).toBeInTheDocument();
  });
});

describe('итог перевода словами', () => {
  it('называет отсеянных стоп-листами и снятых', () => {
    const line = promotedLine({
      ...PROMOTED,
      report: {
        ...PROMOTED.report,
        'донор в стоп-листе поставщиков': 1,
        'адресат в общем стоп-листе': 2,
        'снято с уже заведённых': 1,
      },
      pending: 0,
    });

    expect(line).toBe(
      'Перевод: заведено 2, обновлено 0, отсеяно стоп-листами 3, снято с заведённых 1; адреса есть у всех.',
    );
  });

  it('пустой перевод — «переводить некого», а не нули', () => {
    expect(promotedLine({ ...PROMOTED, report: { рассмотрено: 0 } })).toBe('Переводить некого.');
  });
});
