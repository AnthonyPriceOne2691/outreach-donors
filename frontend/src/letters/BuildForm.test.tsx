/**
 * Числа сборки очереди: поле показывает то, что уйдёт, и нуля в сроках добивок не бывает.
 *
 * Аудит 10.10.2026: «Добивка» принимала 0, а форма подставляла 0 на месте неизвестного
 * умолчания — «через 0 дней» отправляло обе добивки вслед за первым письмом. Проверка QA
 * 10.10.2026: очищенная «Добивка 1» стояла пустой, а уходили 7 дней; «За раз» после букв —
 * пустым, а уходили 50; умолчание вставало в поле посреди набора, и цифры дописывались к нему.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call, Recorded } from '../test/server';
import { serve } from '../test/server';
import { followupDays } from './BuildForm';

const VIEW = {
  stage: 'donors',
  letters: [],
  followup_default: [7, 14],
  letter_default: { subject: 'Guest article on {{host}}', zones: [] },
  blocked_by: [],
  transport: { name: 'sendgrid', real: true, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 3, принятых: 3, 'с адресом': 3, 'ещё не писали': 3 },
  queued_total: 0,
  batch_max: 200,
};

const RUN = {
  id: 18,
  status: 'done',
  country: 'us',
  keywords: 100,
  estimated_units: 14983,
  actual_units: 16998,
  estimate_error: 0.134,
  stats: null,
  started_at: '2026-09-23T16:21:00Z',
  alive_at: '2026-09-23T16:40:00Z',
  hosts: 535,
  reviewed: 0,
  disagreements: 0,
  queue: { pending: 380, accepted: 14 },
  reason: null,
};

async function openForm(): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/letters': { body: VIEW },
    'GET /api/runs/with-accepted': { body: [RUN] },
    'POST /api/letters/build': { body: { job_id: 'letters-build-donors-links' } },
    'GET /api/jobs/letters-build-donors-links': {
      body: {
        job_id: 'letters-build-donors-links',
        kind: 'сборка писем',
        state: 'queued',
        title: 'в очереди',
        error: null,
        report: null,
        retries_left: 3,
        next_try_at: null,
        ended_at: null,
      },
    },
    ...NO_STUCK_LETTERS,
  });
  renderWith(<AppRoutes />, '/letters');
  // У пустой очереди сборка раскрыта.
  await screen.findByRole('button', { name: 'Собрать очередь' });
  return recorded;
}

/** Собрать: кампания, прогон — и что ушло на сервер. */
async function build(user: ReturnType<typeof userEvent.setup>, recorded: Recorded) {
  await user.type(screen.getByLabelText('Кампания'), 'Май');
  await user.click(await screen.findByLabelText(/№18/));
  await user.click(screen.getByRole('button', { name: 'Собрать очередь' }));
  await waitFor(() =>
    expect(recorded.calls.some((call: Call) => call.path === '/api/letters/build')).toBe(true),
  );
  return recorded.calls.find((call: Call) => call.path === '/api/letters/build')?.body;
}

describe('сроки добивок', () => {
  it('ноль в добивке становится сутками — на экране и в запросе', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    const first = screen.getByLabelText('Добивка 1, дней');

    await user.clear(first);
    await user.type(first, '0');
    await user.tab();

    expect(first).toHaveValue('1 дн.');
    expect(await build(user, recorded)).toMatchObject({ followup_days: [1, 14] });
  });

  it('очищенная добивка при уходе из поля показывает умолчание — его и отправляем', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    const first = screen.getByLabelText('Добивка 1, дней');

    await user.clear(first);
    expect(first).toHaveValue('');
    await user.tab();

    expect(first).toHaveValue('7 дн.');
    expect(await build(user, recorded)).toMatchObject({ followup_days: [7, 14] });
  });

  it('больше 90 — граница в поле, граница и уходит', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    const second = screen.getByLabelText('Добивка 2, дней');

    await user.clear(second);
    await user.type(second, '120');
    await user.tab();

    expect(second).toHaveValue('90 дн.');
    expect(await build(user, recorded)).toMatchObject({ followup_days: [7, 90] });
  });

  it('сроки без умолчания сервера — короче, но не нулём', () => {
    expect(followupDays([null, null], [7, 14])).toEqual([7, 14]);
    expect(followupDays([3, null], [7, 14])).toEqual([3, 14]);
    expect(followupDays([null, null], [])).toEqual([]);
    expect(followupDays([5, null], [])).toEqual([5]);
  });
});

describe('«За раз»', () => {
  it('пустое поле при уходе из него показывает 50 — их и отправляем', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    const limit = screen.getByLabelText('Писем за раз');

    await user.clear(limit);
    await user.tab();

    expect(limit).toHaveValue('50');
    expect(await build(user, recorded)).toMatchObject({ limit: 50 });
  });

  it('набор не сбрасывается на умолчание и не дописывается к нему', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    const limit = screen.getByLabelText('Писем за раз');
    await user.clear(limit);
    await user.type(limit, '30');

    // Очистили набранное: умолчание не встаёт в поле посреди набора — было «50», а «2»
    // после него давало «502».
    await user.clear(limit);
    expect(limit).toHaveValue('');
    await user.type(limit, '2');
    expect(limit).toHaveValue('2');
    // Целое число: точку поле не берёт, к набранному она не прилипает.
    await user.type(limit, '.5');
    await user.tab();

    expect(limit).toHaveValue('25');
    expect(await build(user, recorded)).toMatchObject({ limit: 25 });
  });

  it('больше 500 — граница в поле, граница и уходит', async () => {
    const recorded = await openForm();
    const user = userEvent.setup();
    const limit = screen.getByLabelText('Писем за раз');

    await user.clear(limit);
    await user.type(limit, '1000');
    await user.tab();

    expect(limit).toHaveValue('500');
    expect(await build(user, recorded)).toMatchObject({ limit: 500 });
  });
});
