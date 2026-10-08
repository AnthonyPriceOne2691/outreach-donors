/**
 * Вкладка «Очередь писем»: подключены ли продажи — словами сервера, сборка задачей,
 * пачка — общей отправкой очереди этапа продаж. Ответы сервера — записанные (`serve()`),
 * незаписанный запрос роняет тест; адреса и имена выдуманы.
 *
 * Проверяется то, ради чего вкладка: не подключены — кнопки закрыты, и названо всё,
 * чего не хватает; отказ сервера на нажатие виден словами; на сервер уходит гипотеза и
 * число писем, а пачка — общей кнопкой почты с этапом продаж; число на кнопке пачки —
 * письма всех гипотез, и вкладка говорит это до нажатия; итог задачи — словами; без права
 * отправки кнопки пачки нет.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView, SalesQueueView } from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { queueLine } from './queueData';

const SCREEN_WAIT = { timeout: 5000 };
const QUEUE = '/api/sales/queue';
const ALL_MISSING = ['первого письма', 'первой добивки', 'второй добивки'];

function hypothesis(id: number, name: string) {
  return {
    id,
    name,
    description: null,
    created_at: '2026-10-01T10:00:00+00:00',
    leads: { new: 0, ready: 3, rejected: 0 },
    total: 3,
  };
}

const HYPOTHESES: HypothesesView = {
  rows: [hypothesis(5, 'тестовая гипотеза'), hypothesis(8, 'вторая тестовая')],
  total: 2,
};

const READY: SalesQueueView = {
  hypothesis_id: 5,
  connected: true,
  missing: [],
  chains: [
    { language: 'ru', source: 'common', missing: ALL_MISSING, version: 'chain-ru0000000000' },
    { language: 'en', source: 'own', missing: [], version: 'chain-en0000000000' },
  ],
  unwritten: 4,
  queued: 2,
  stage_queued: 7,
  limit_max: 200,
};

const OFF: SalesQueueView = {
  ...READY,
  connected: false,
  missing: [
    'продажи выключены: SALES_ENABLED не включён',
    'не задан физический адрес — вкладка «Отправитель»',
  ],
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

function job(id: string, report: Record<string, unknown>) {
  return {
    job_id: id,
    kind: 'сборка очереди продаж',
    state: 'done',
    title: 'готово',
    error: null,
    report,
    retries_left: null,
    next_try_at: null,
    ended_at: null,
  };
}

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

async function openQueue(
  routes: Routes = {},
  view: SalesQueueView = READY,
  who: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [`GET ${QUEUE}?hypothesis=5`]: { body: view },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=queue');
  await screen.findByText('Лидов без письма', {}, SCREEN_WAIT);
  return recorded;
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

describe('очередь писем продаж', () => {
  it('подключены: цепочки и числа словами сервера, кнопки открыты', async () => {
    await openQueue();

    expect(screen.getByText('продажи подключены')).toBeInTheDocument();
    expect(
      screen.getByText(
        'общая цепочка: нет первого письма, первой добивки, второй добивки — лиды на этом языке ждут.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('своя цепочка гипотезы полна — лиды на этом языке получат письма.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
    // Пачка берёт очередь этапа целиком: число — письма всех гипотез, а не этой.
    expect(screen.getByRole('button', { name: 'Отправить очередь · 7' })).toBeEnabled();
  });

  it('не подключены: всё, чего не хватает, названо, кнопки закрыты', async () => {
    await openQueue({}, OFF);

    const warning = screen.getByText('Продажи к почте не подключены').closest('[role="alert"]');
    if (!(warning instanceof HTMLElement)) throw new Error('плашки нет');
    expect(
      within(warning)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(OFF.missing);
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отправить очередь · 7' })).toBeDisabled();
  });

  it('сборка уходит гипотезой и числом писем, итог задачи — словами, очередь перечитана', async () => {
    const recorded = await openQueue({
      [`POST ${QUEUE}`]: { body: { job_id: 'job-b' } },
      'GET /api/jobs/job-b': {
        body: job('job-b', {
          campaign_id: 3,
          prepared: 2,
          refreshed: 1,
          tokens_spent: 74,
          off_corridor: 1,
          waiting: { 'стоп-лист': 1, 'вне коридора отличия — ждёт следующей сборки': 1 },
          stopped: null,
        }),
      },
    });
    const user = userEvent.setup();

    const limit = screen.getByRole('textbox', { name: 'Писем за раз' });
    await user.clear(limit);
    await user.type(limit, '17');
    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    await waitFor(() => expect(calls(recorded, 'POST', QUEUE)).toHaveLength(1), SCREEN_WAIT);
    expect(calls(recorded, 'POST', QUEUE)[0]?.body).toEqual({ hypothesis_id: 5, limit: 17 });
    expect(
      await screen.findByText(
        'Новых писем 2, собрано заново 1. Ждут — стоп-лист: 1; ' +
          'вне коридора отличия — ждёт следующей сборки: 1.',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
    await waitFor(
      () => expect(calls(recorded, 'GET', `${QUEUE}?hypothesis=5`).length).toBeGreaterThan(1),
      SCREEN_WAIT,
    );
  });

  it('отказ сервера на сборку — словами у кнопки', async () => {
    const refusal =
      'Очередь продаж не собрана: продажи к почте ещё не подключены — не задана подпись';
    await openQueue({ [`POST ${QUEUE}`]: { status: 409, body: { detail: refusal } } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));

    const alert = (await screen.findByText('Очередь не собрана', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent(refusal);
  });

  it('у гипотезы нечего собирать — кнопка закрыта и сказано почему', async () => {
    await openQueue({}, { ...READY, unwritten: 0, queued: 0 });

    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    expect(
      screen.getByText('Лидов без письма и писем в очереди у гипотезы нет — собирать нечего.'),
    ).toBeInTheDocument();
  });

  it('другая гипотеза — своя очередь с сервера', async () => {
    const recorded = await openQueue({
      [`GET ${QUEUE}?hypothesis=8`]: { body: { ...READY, hypothesis_id: 8, unwritten: 11 } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'Гипотеза' }));
    await user.click(
      await screen.findByRole('option', { name: 'вторая тестовая', hidden: true }, SCREEN_WAIT),
    );

    await waitFor(
      () => expect(calls(recorded, 'GET', `${QUEUE}?hypothesis=8`)).toHaveLength(1),
      SCREEN_WAIT,
    );
    expect(await screen.findByText('11', {}, SCREEN_WAIT)).toBeInTheDocument();
  });

  it('гипотез нет — сказано, откуда берётся очередь', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: { rows: [], total: 0 } },
      'GET /api/sales/kb': { body: KB },
    });
    renderWith(<AppRoutes />, '/sales?tab=queue');

    expect(
      await screen.findByText(
        'Гипотез пока нет. Очередь собирается из лидов гипотезы — загрузите базу.',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
  });
});

describe('пачка продаж', () => {
  it('уходит этапом продаж после подтверждения, итог сказан словами', async () => {
    const recorded = await openQueue({
      'POST /api/letters/send-queue': { body: { job_id: 'job-s', queued: 7 } },
      'GET /api/jobs/job-s': {
        body: job('job-s', { sent: 5, refused: { 'стоп-лист': 2 }, stopped: null, left: 0 }),
      },
    });
    const user = userEvent.setup();

    // Пачка — общая кнопка почты: что она берёт очередь всех гипотез, говорит вкладка.
    expect(
      screen.getByText(
        'Пачка берёт очередь продаж целиком — письма всех гипотез, не только выбранной.',
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 7' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/В очереди 7 писем/)).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 7' }));

    await waitFor(
      () =>
        expect(calls(recorded, 'POST', '/api/letters/send-queue')[0]?.body).toEqual({
          stage: 'sales',
        }),
      SCREEN_WAIT,
    );
    expect(
      await screen.findByText('Ушло 5, стоп-лист — 2, осталось в очереди 0.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('отказ сервера — словами', async () => {
    const refusal = 'Очередь писем не отправлена: продажи к почте ещё не подключены';
    await openQueue({
      'POST /api/letters/send-queue': { status: 409, body: { detail: refusal } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 7' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 7' }));

    expect(await screen.findByText(refusal, {}, SCREEN_WAIT)).toBeInTheDocument();
  });

  it('отмена ничего не отправляет', async () => {
    const recorded = await openQueue();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 7' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отмена' }),
    );

    expect(calls(recorded, 'POST', '/api/letters/send-queue')).toEqual([]);
  });

  it('без права на отправку кнопки пачки нет, сборка есть', async () => {
    await openQueue({}, READY, OPERATOR);

    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Отправить очередь/ })).not.toBeInTheDocument();
  });
});

describe('итог сборки словами', () => {
  it('называет, почему сборка остановилась', () => {
    expect(
      queueLine({ prepared: 0, refreshed: 0, waiting: {}, stopped: 'потолок расхода на модель' }),
    ).toBe('Новых писем 0, собрано заново 0. Остановлено: потолок расхода на модель');
  });
});
