/**
 * «Доставка откладывается» на экране писем (29.10.2026): письма ушли, а сервер получателя их
 * пока не принимает. Проверяется то, ради чего блок: у каждого письма — причина словами
 * сервера, попытки и часы, кому и переписка; сколько всего — в заголовке; откладываемых нет —
 * блока нет; не загрузился — отказ словами; у вкладки рекламодателей — свой список.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DeferredLetter } from '../api/delivery';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

const VIEW = {
  letters: [],
  followup_default: [7, 14],
  letter_default: { subject: 'Guest article on {{host}}', zones: [] },
  blocked_by: [],
  transport: { name: 'sendgrid', real: true, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 2, 'с адресом': 2, 'ещё не писали': 0 },
};

const LETTER_404: DeferredLetter = {
  id: 404,
  host: 'ads-weekly.example.test',
  email: 'ads@ads-weekly.example.test',
  campaign: 'Октябрь',
  thread_id: 31,
  deferral: {
    kind: 'unreachable',
    words: 'сервер получателя не отвечает (порт 25, тайм-аут)',
    attempts: 14,
    since: new Date(2026, 9, 8, 10, 26).toISOString(),
    last: new Date(2026, 9, 8, 16, 22).toISOString(),
  },
};

function deferred(letters: DeferredLetter[], total = letters.length, stage = 'donors') {
  return { body: { stage, letters, total } };
}

function open(routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/letters': { body: VIEW },
    'GET /api/letters?stage=advertisers': { body: { ...VIEW, stage: 'advertisers' } },
    'GET /api/runs/with-accepted': { body: [] },
    ...NO_STUCK_LETTERS,
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/letters');
  return recorded;
}

const asked = (recorded: { calls: Call[] }, path: string) =>
  recorded.calls.filter((call) => call.method === 'GET' && call.path === path).length;

async function rowOf(host: string): Promise<HTMLElement> {
  const name = await screen.findByText(host);
  const row = name.closest('.glassQuiet');
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe('доставка откладывается', () => {
  it('у письма — причина, попытки и часы, кому и переписка', async () => {
    open({ 'GET /api/letters/deferred': deferred([LETTER_404], 3) });

    expect(await screen.findByText('Доставка откладывается · 3')).toBeInTheDocument();
    const row = within(await rowOf('ads-weekly.example.test'));
    expect(
      row.getByText(
        'Сервер получателя не отвечает (порт 25, тайм-аут) · 14 попыток с 08.10, 10:26, ' +
          'последняя 08.10, 16:22',
      ),
    ).toBeInTheDocument();
    expect(row.getByText('Кому ads@ads-weekly.example.test · Октябрь')).toBeInTheDocument();
    expect(row.getByRole('link', { name: 'Переписка' })).toHaveAttribute('href', '/threads/31');
    expect(screen.getByText('И ещё 2 — выше те, что откладываются дольше.')).toBeInTheDocument();
  });

  it('откладываемых нет — блока нет', async () => {
    const recorded = open();

    await screen.findByText('Очередь пуста');
    await waitFor(() => expect(asked(recorded, '/api/letters/deferred')).toBe(1));
    expect(screen.queryByText(/Доставка откладывается/)).not.toBeInTheDocument();
  });

  it('не загрузился — отказ словами, а не пустое место', async () => {
    open({ 'GET /api/letters/deferred': { status: 500, body: { detail: 'База не ответила' } } });

    expect(
      await screen.findByText('Письма с отложенной доставкой не загрузились'),
    ).toBeInTheDocument();
  });

  it('у рекламодателей — свой список', async () => {
    const offer = { ...LETTER_404, id: 9, host: 'brand.example.test', thread_id: null };
    const recorded = open({
      'GET /api/letters/deferred': deferred([LETTER_404]),
      'GET /api/letters/deferred?stage=advertisers': deferred([offer], 1, 'advertisers'),
    });
    const user = userEvent.setup();

    await screen.findByText('Доставка откладывается · 1');
    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));

    const row = within(await rowOf('brand.example.test'));
    expect(row.queryByRole('link', { name: 'Переписка' })).not.toBeInTheDocument();
    expect(asked(recorded, '/api/letters/deferred?stage=advertisers')).toBe(1);
  });
});
