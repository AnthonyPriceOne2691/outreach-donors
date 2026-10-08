/**
 * «Отправить очередь · N» — вся очередь этапа пачкой (слово Anthony 06.10.2026).
 *
 * Проверяется то, ради чего кнопка: число на ней — длина очереди; пачка
 * уходит только после подтверждения, где сказано, что ограничит отправку;
 * на сервер уходит этап; итог задачи — словами, а не «готово»; без почты
 * кнопка закрыта, без права на отправку её нет.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { formatNumber } from '../format';
import { ADMIN, NO_STUCK_LETTERS, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { SendQueue, batchLine } from './SendQueue';

function letter(id: number, host: string) {
  return {
    id,
    host,
    email: `info@${host}`,
    campaign: 'Запуск',
    status: 'queued',
    subject: `Guest article on ${host}`,
    body: 'Hello,\n\nCould you share your rates?\n\nBest regards,\nAlex',
    uniqueness: 0.19,
    verdict: null,
    followups: [],
  };
}

const VIEW = {
  letters: [letter(1, 'one.example.test'), letter(2, 'two.example.test')],
  followup_default: [7, 14],
  letter_default: { subject: 'Guest article on {{host}}', zones: [] },
  blocked_by: [],
  transport: { name: 'sendgrid', real: true, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 2, 'с адресом': 2, 'ещё не писали': 0 },
  queued_total: 2,
  batch_max: 200,
};

const DONE = {
  job_id: 'job-q',
  kind: 'отправка очереди',
  state: 'done',
  title: 'готово',
  error: null,
  report: { sent: 1, refused: { 'стоп-лист': 1 }, stopped: null, left: 1 },
  retries_left: null,
  next_try_at: null,
  ended_at: null,
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

describe('отправка очереди пачкой', () => {
  it('уходит после подтверждения, и итог сказан словами', async () => {
    const recorded = open(
      {},
      {
        'POST /api/letters/send-queue': { body: { job_id: 'job-q', queued: 2 } },
        'GET /api/jobs/job-q': { body: DONE },
      },
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Отправить очередь · 2' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/В очереди 2 письма: каждое уйдёт/)).toBeInTheDocument();
    expect(within(dialog).getByText(/дневной лимит ящиков/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 2' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.path === '/api/letters/send-queue')?.body).toEqual({
        stage: 'donors',
      }),
    );
    expect(
      await screen.findByText('Ушло 1, стоп-лист — 1, осталось в очереди 1.'),
    ).toBeInTheDocument();
  });

  it('очередь длиннее пачки — окно называет потолок, который назвал сервер', async () => {
    open({ batch_max: 1 });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Отправить очередь · 2' }));
    const dialog = await screen.findByRole('dialog');

    expect(
      within(dialog).getByText(/^В очереди 2 письма; одна пачка берёт до 1, остальное — следующей/),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Отправить 1' })).toBeInTheDocument();
  });

  it('кнопка называет всю очередь этапа, а не список экрана: в списке 200, всего 1 000', async () => {
    const shown = Array.from({ length: 200 }, (_, at) => letter(at + 1, `d${at + 1}.example.test`));
    open({ letters: shown, queued_total: 1000, batch_max: 200 });
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', { name: `Отправить очередь · ${formatNumber(1000)}` }),
    );
    const dialog = await screen.findByRole('dialog');

    expect(
      within(dialog).getByText(
        /^В очереди 1\s000 писем; одна пачка берёт до 200, остальное — следующей/,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Отправить 200' })).toBeInTheDocument();
  });

  it('отмена ничего не отправляет', async () => {
    const recorded = open();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Отправить очередь · 2' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отмена' }),
    );

    expect(recorded.calls.some((call) => call.path === '/api/letters/send-queue')).toBe(false);
  });

  it('без почты кнопка закрыта', async () => {
    open({ blocked_by: ['OUTREACH_SENDER_NAME'] });

    expect(await screen.findByRole('button', { name: 'Отправить очередь · 2' })).toBeDisabled();
  });

  it('без права на отправку кнопки нет', async () => {
    open({}, {}, OPERATOR);

    await screen.findByText('В очереди');
    expect(screen.queryByRole('button', { name: /Отправить очередь/ })).not.toBeInTheDocument();
  });
});

describe('итог пачки словами', () => {
  it('называет, почему пачка остановилась', () => {
    expect(batchLine({ sent: 20, refused: {}, stopped: 'Сегодня писать некому', left: 5 })).toBe(
      'Ушло 20, осталось в очереди 5. Остановлено: Сегодня писать некому',
    );
  });
});

describe('пачка любого этапа (4.5a)', () => {
  it('продажи уходят своим этапом, отказ сервера — словами', async () => {
    const refusal = 'Очередь писем не отправлена: продажи к почте ещё не подключены';
    const recorded = serve({
      'POST /api/letters/send-queue': { status: 409, body: { detail: refusal } },
    });
    renderWith(<SendQueue stage="sales" count={2} blocked={false} onFinished={() => undefined} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 2' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отправить 2' }),
    );

    expect(await screen.findByText(refusal)).toBeInTheDocument();
    expect(recorded.calls.find((call) => call.path === '/api/letters/send-queue')?.body).toEqual({
      stage: 'sales',
    });
  });
});
