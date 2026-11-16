/**
 * «Ждём ответа» на экране «Письма» (16.11.2026): переписки этапа без ответа — те, что ушли из
 * «Диалогов». Проверяется то, ради чего блок: свёрнут в строку с числом; в списке — только
 * переписки своего этапа без ответа, недавние сверху; у строки — домен ссылкой на переписку,
 * адрес, рассылка, состояние словами «Диалогов», писем ушло и когда; первые сто — и «Показать
 * ещё 100»; пусто — словами и без раскрытия; не загрузился — отказ словами; у рекламодателей —
 * свой этап, один список на обе аудитории; на телефоне строка — карточкой с подписями.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { ThreadCard } from '../api/types';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Answer, Call } from '../test/server';
import { serve } from '../test/server';
import { awaitingOf } from './AwaitingReplies';

const VIEW = {
  letters: [],
  followup_default: [7, 14],
  letter_default: { subject: 'Guest article on {{host}}', zones: [] },
  blocked_by: [],
  transport: { name: 'sendgrid', real: true, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 2, 'с адресом': 2, 'ещё не писали': 0 },
};

const DONORS = 'GET /api/threads?stage=donors&answered=false';
const ADVERTISERS = 'GET /api/threads?stage=advertisers&answered=false';

function thread(id: number, extra: Partial<ThreadCard> = {}): ThreadCard {
  return {
    id,
    host: `site-${id}.example.com`,
    contact_email: `editor@site-${id}.example.com`,
    campaign: 'Ноябрь',
    stage: 'donors',
    state: 'waiting',
    answered: false,
    messages_sent: 1,
    last_event_at: new Date(2026, 10, 10, 9, 0).toISOString(),
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
    ...extra,
  };
}

async function open(path: string, routes: Record<string, Answer> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/letters': { body: VIEW },
    'GET /api/letters?stage=advertisers': { body: { ...VIEW, stage: 'advertisers' } },
    'GET /api/letters?stage=advertisers&audience=niche': {
      body: { ...VIEW, stage: 'advertisers' },
    },
    'GET /api/runs/with-accepted': { body: [] },
    ...NO_STUCK_LETTERS,
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText('Очередь пуста');
  return recorded;
}

const asked = (recorded: { calls: Call[] }, route: string) =>
  recorded.calls.filter((call) => `${call.method} ${call.path}` === route).length;

/** Раскрыть блок: кнопка — заголовок с числом. */
async function unfold(count: string) {
  const user = userEvent.setup();
  const head = await screen.findByRole('button', { name: `Ждём ответа · ${count}` });
  expect(head).toHaveAttribute('aria-expanded', 'false');
  await user.click(head);
  expect(head).toHaveAttribute('aria-expanded', 'true');
  // Раскрытие ставит видимость кадром позже — таблицу ждём, а не берём сразу.
  await screen.findByRole('table', { name: 'Переписки без ответа' });
  return user;
}

const table = () => screen.getByRole('table', { name: 'Переписки без ответа' });
/** Строки таблицы — выборкой, а не поиском по ролям: на двухстах пятидесяти строках поиск по
 *  ролям обходит всё дерево и под нагрузкой полного прогона не укладывался в срок теста. */
const rows = () => [...table().querySelectorAll<HTMLElement>('tbody > tr')];

describe('ждём ответа', () => {
  it('свёрнут в строку с числом; строка — домен ссылкой, адрес, рассылка, состояние, когда', async () => {
    await open('/letters?stage=donors', {
      [DONORS]: {
        body: [
          thread(7, {
            last_event_at: new Date(2026, 10, 16, 9, 30).toISOString(),
            messages_sent: 2,
          }),
          thread(8, { state: 'queued', messages_sent: 0, last_event_at: null }),
          thread(9, { state: 'bounced', contact_email: null }),
        ],
      },
    });

    await unfold('3');

    // Недавние сверху, письмо в очереди — без событий — внизу.
    const [recent, bounced, queued] = rows();
    const first = within(recent!);
    expect(first.getByRole('link', { name: 'site-7.example.com' })).toHaveAttribute(
      'href',
      '/threads/7',
    );
    expect(first.getByText('editor@site-7.example.com')).toBeInTheDocument();
    expect(first.getByText('Ноябрь')).toBeInTheDocument();
    expect(first.getByText('ждём ответа')).toBeInTheDocument();
    expect(first.getByText('2')).toBeInTheDocument();
    expect(first.getByText('16.11.2026, 09:30')).toBeInTheDocument();
    expect(within(bounced!).getByText('отказ доставки')).toBeInTheDocument();
    expect(within(bounced!).getByText('адрес не определён')).toBeInTheDocument();
    expect(within(queued!).getByText('в очереди')).toBeInTheDocument();
    expect(within(queued!).getByText('—')).toBeInTheDocument();
    // Что сюда попадает — в «i» у заголовка.
    expect(
      screen.getByRole('button', { name: 'Что попадает в «Ждём ответа»' }),
    ).toBeInTheDocument();
  });

  it('только переписки своего этапа без ответа — даже если сервер прислал лишнее', () => {
    const listed = awaitingOf(
      [
        thread(1),
        thread(2, { answered: true, state: 'replied' }),
        thread(3, { stage: 'advertisers' }),
        thread(4, { last_event_at: new Date(2026, 10, 12, 9, 0).toISOString() }),
        { ...thread(5), answered: undefined } as unknown as ThreadCard,
      ],
      'donors',
    );

    expect(listed.map((card) => card.id)).toEqual([4, 1]);
  });

  it('первые сто — и «Показать ещё 100», пока есть остаток', async () => {
    const many = Array.from({ length: 250 }, (_, at) => thread(at + 1));
    await open('/letters?stage=donors', { [DONORS]: { body: many } });

    const user = await unfold('250');
    expect(rows()).toHaveLength(100);
    expect(screen.getByText('осталось 150')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать ещё\u00a0100' }));
    expect(rows()).toHaveLength(200);
    expect(screen.queryByText(/осталось/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать ещё\u00a050' }));
    expect(rows()).toHaveLength(250);
    expect(screen.queryByRole('button', { name: /Показать ещё/ })).not.toBeInTheDocument();
    // Двести пятьдесят строк таблицы в jsdom — секунды под нагрузкой полного прогона.
  }, 15_000);

  it('пусто — словами и без раскрытия', async () => {
    await open('/letters?stage=donors');

    expect(await screen.findByText('Ждём ответа · 0')).toBeInTheDocument();
    expect(screen.getByText(/Переписок без ответа нет/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Ждём ответа/ })).not.toBeInTheDocument();
  });

  it('не загрузился — отказ словами, а не пустое место', async () => {
    await open('/letters?stage=donors', {
      [DONORS]: { status: 500, body: { detail: 'База не ответила' } },
    });

    expect(await screen.findByText('Переписки без ответа не загрузились')).toBeInTheDocument();
    expect(screen.getByText('База не ответила')).toBeInTheDocument();
  });

  it('у рекламодателей — свой этап, один список на обе аудитории', async () => {
    const offer = thread(31, { stage: 'advertisers', host: 'brand.example.com' });
    const recorded = await open('/letters?stage=advertisers', { [ADVERTISERS]: { body: [offer] } });
    const user = await unfold('1');

    expect(within(table()).getByRole('link', { name: 'brand.example.com' })).toHaveAttribute(
      'href',
      '/threads/31',
    );
    await user.click(screen.getByRole('radio', { name: 'Бизнесам ниши' }));
    await screen.findByRole('radio', { name: 'Бизнесам ниши', checked: true });

    // Та же строка, раскрытая, — и без второго запроса: список у этапа один.
    expect(within(table()).getByRole('link', { name: 'brand.example.com' })).toBeInTheDocument();
    await waitFor(() => expect(asked(recorded, ADVERTISERS)).toBe(1));
    expect(asked(recorded, DONORS)).toBe(0);
  });

  it('на телефоне строка — карточкой: домен первым, у значений подписи колонок', async () => {
    await open('/letters?stage=donors', { [DONORS]: { body: [thread(7)] } });
    await unfold('1');

    const row = rows()[0]!;
    expect(row.closest('.phoneCards')).not.toBeNull();
    const labels = [...row.querySelectorAll('td[data-label]')].map((cell) =>
      cell.getAttribute('data-label'),
    );
    expect(labels).toEqual(['Адрес', 'Рассылка', 'Состояние', 'Писем ушло', 'Последнее событие']);
  });
});
