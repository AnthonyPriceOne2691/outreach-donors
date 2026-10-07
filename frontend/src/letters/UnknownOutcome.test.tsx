/**
 * «Исход неизвестен» на экране писем — письма, зависшие в «отправляется».
 *
 * Проверяется то, ради чего блок: зависшие письма видны с тем, по чему их ищут
 * в журнале платформы; решение уходит на сервер только после окна, которое
 * говорит, чем оно кончится, — и «Вернуть в очередь» прямо предупреждает
 * о втором письме; отказ сервера — словами, и список после него освежается;
 * без права на отправку кнопок нет; пустой список — блока нет.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, OPERATOR, TOKEN_KEY } from '../test/fixtures';
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

const FIRST = {
  id: 5,
  host: 'digest-weekly.example.test',
  email: 'editor@digest-weekly.example.test',
  sender_email: 'outreach1@mail-alpha.example.test',
  campaign: 'Запуск',
  what: 'первое письмо',
  since: '2026-10-07T09:30:00Z',
  thread_id: 31,
};

const FOLLOWUP = {
  ...FIRST,
  id: 6,
  host: 'city-news.example.test',
  email: 'info@city-news.example.test',
  what: 'добивка 1',
  thread_id: 32,
};

function stuck(letters: unknown[], stage = 'donors') {
  return { body: { stage, letters, after_minutes: 5 } };
}

function open(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/letters': { body: VIEW },
    'GET /api/letters?stage=advertisers': { body: { ...VIEW, stage: 'advertisers' } },
    'GET /api/runs/with-accepted': { body: [] },
    ...NO_STUCK_LETTERS,
    'GET /api/letters/unknown': stuck([FIRST, FOLLOWUP]),
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/letters');
  return recorded;
}

const asked = (recorded: { calls: Call[] }, path: string) =>
  recorded.calls.filter((call) => call.method === 'GET' && call.path === path).length;

/** Строка письма в блоке — по домену. */
async function rowOf(host: string): Promise<HTMLElement> {
  const name = await screen.findByText(host);
  const row = name.closest('.glassQuiet');
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe('исход неизвестен', () => {
  it('зависшие письма видны с тем, по чему их искать в журнале платформы', async () => {
    open();

    expect(await screen.findByText('Исход неизвестен · 2')).toBeInTheDocument();
    expect(screen.getByText(/больше 5 минут назад, и связь оборвалась/)).toBeInTheDocument();
    const row = await rowOf('digest-weekly.example.test');
    expect(within(row).getByText('первое письмо')).toBeInTheDocument();
    expect(
      within(row).getByText(
        /Кому editor@digest-weekly\.example\.test, с ящика outreach1@mail-alpha\.example\.test/,
      ),
    ).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'Переписка' })).toHaveAttribute(
      'href',
      '/threads/31',
    );
    expect(within(await rowOf('city-news.example.test')).getByText('добивка 1')).toBeVisible();
  });

  it('«Ушло» — только после окна, и сервер говорит, что стало с письмом', async () => {
    let decided = false;
    const recorded = open({
      'GET /api/letters/unknown': () => stuck(decided ? [FOLLOWUP] : [FIRST, FOLLOWUP]),
      'POST /api/letters/5/resolve': () => {
        decided = true;
        return {
          body: {
            id: 5,
            status: 'sent',
            said: 'отмечено ушедшим — сроки добивок идут от начала передачи',
          },
        };
      },
    });
    const user = userEvent.setup();

    const row = await rowOf('digest-weekly.example.test');
    await user.click(within(row).getByRole('button', { name: 'Ушло' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(/только если нашли письмо в журнале платформы/),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Ушло' }));

    expect(
      await screen.findByText(
        'digest-weekly.example.test: отмечено ушедшим — сроки добивок идут от начала передачи',
      ),
    ).toBeInTheDocument();
    const posts = recorded.calls.filter((call) => call.method === 'POST');
    expect(posts.map((call) => [call.path, call.body])).toEqual([
      ['/api/letters/5/resolve', { outcome: 'sent' }],
    ]);
    expect(await screen.findByText('Исход неизвестен · 1')).toBeInTheDocument();
    // Вернувшееся письмо встаёт в очередь — она освежается вместе со списком.
    expect(asked(recorded, '/api/letters')).toBeGreaterThan(1);
  });

  it('«Вернуть в очередь» прямо предупреждает о втором письме', async () => {
    const recorded = open({
      'POST /api/letters/6/resolve': {
        body: {
          id: 6,
          status: 'queued',
          said: 'добивка вернулась в цепочку и уйдёт с ближайшим проходом добивок',
        },
      },
    });
    const user = userEvent.setup();

    const row = await rowOf('city-news.example.test');
    await user.click(within(row).getByRole('button', { name: 'Вернуть в очередь' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText('Если оно всё же ушло, адресат получит его второй раз'),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/только если письма нет в журнале/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Вернуть в очередь' }));

    expect(await screen.findByText(/добивка вернулась в цепочку/)).toBeInTheDocument();
    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        outcome: 'queued',
      }),
    );
  });

  it('отмена ничего не решает', async () => {
    const recorded = open();
    const user = userEvent.setup();

    const row = await rowOf('digest-weekly.example.test');
    await user.click(within(row).getByRole('button', { name: 'Вернуть в очередь' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отмена' }),
    );

    expect(recorded.calls.some((call) => call.method === 'POST')).toBe(false);
  });

  it('отказ сервера — словами, и список освежается', async () => {
    const recorded = open({
      'POST /api/letters/5/resolve': {
        status: 409,
        body: {
          detail:
            'Письмо №5 секундой раньше вышло из «отправляется» другим путём — обновите ' +
            'экран: его исход уже записан',
        },
      },
    });
    const user = userEvent.setup();

    const row = await rowOf('digest-weekly.example.test');
    await user.click(within(row).getByRole('button', { name: 'Ушло' }));
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Ушло' }),
    );

    expect(await screen.findByText(/секундой раньше вышло из «отправляется»/)).toBeVisible();
    await waitFor(() => expect(asked(recorded, '/api/letters/unknown')).toBeGreaterThan(1));
  });

  it('без права на отправку список виден, кнопок нет', async () => {
    open({}, OPERATOR);

    await rowOf('digest-weekly.example.test');
    expect(screen.getByText(/Решает человек с правом на отправку/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ушло' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Вернуть в очередь' })).not.toBeInTheDocument();
  });

  it('пусто — блока нет', async () => {
    const recorded = open({ 'GET /api/letters/unknown': stuck([]) });

    await screen.findByText('Очередь пуста');
    await waitFor(() => expect(asked(recorded, '/api/letters/unknown')).toBe(1));
    expect(screen.queryByText(/Исход неизвестен/)).not.toBeInTheDocument();
  });

  it('не загрузился — отказ словами, а не пустое место', async () => {
    open({
      'GET /api/letters/unknown': { status: 500, body: { detail: 'База не ответила' } },
    });

    expect(
      await screen.findByText('Письма с неизвестным исходом не загрузились'),
    ).toBeInTheDocument();
  });

  it('у рекламодателей — свой список', async () => {
    const offer = { ...FIRST, id: 9, host: 'brand.example.test', what: 'первое письмо' };
    const recorded = open({
      'GET /api/letters/unknown?stage=advertisers': stuck([offer], 'advertisers'),
    });
    const user = userEvent.setup();

    await screen.findByText('Исход неизвестен · 2');
    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));

    expect(await screen.findByText('Исход неизвестен · 1')).toBeInTheDocument();
    expect(await rowOf('brand.example.test')).toBeVisible();
    expect(asked(recorded, '/api/letters/unknown?stage=advertisers')).toBe(1);
  });
});
