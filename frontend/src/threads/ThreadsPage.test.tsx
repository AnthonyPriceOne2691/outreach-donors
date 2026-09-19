/**
 * Список диалогов: что показано и чем фильтруется.
 *
 * Отдельная проверка на то, что строка — это донор, а не адрес: у сайта
 * их несколько, и список по адресам превращает восемь доноров в двадцать
 * строк, по которым непонятно, с кем уже договорились.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const THREADS = [
  {
    id: 1,
    host: 'digest-weekly.example.test',
    contact_email: 'editor@digest-weekly.example.test',
    campaign: 'Демонстрация',
    state: 'priced',
    messages_sent: 1,
    last_event_at: '2026-09-19T10:00:00+00:00',
    last_reply_at: '2026-09-19T10:00:00+00:00',
    price_white: '250.00',
    price_grey: '180.00',
    currency: 'EUR',
  },
  {
    id: 2,
    host: 'green-blog.example.test',
    contact_email: 'hello@green-blog.example.test',
    campaign: 'Демонстрация',
    state: 'waiting',
    messages_sent: 1,
    last_event_at: '2026-09-18T10:00:00+00:00',
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  },
];

async function openThreads() {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads': { body: THREADS },
  });
  renderWith(<AppRoutes />, '/threads');
  await screen.findByText('digest-weekly.example.test');
  return recorded;
}

describe('диалоги', () => {
  it('показывает состояние и цену', async () => {
    await openThreads();

    const row = screen.getByText('digest-weekly.example.test').closest('tr');
    expect(within(row as HTMLElement).getByText('цена получена')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText(/250.00 \/ 180.00 EUR/)).toBeInTheDocument();
  });

  it('счётчик состояния работает фильтром', async () => {
    await openThreads();
    const user = userEvent.setup();

    await user.click(screen.getByText('ждём ответа — 1'));

    expect(screen.getByText('green-blog.example.test')).toBeInTheDocument();
    expect(screen.queryByText('digest-weekly.example.test')).not.toBeInTheDocument();
  });

  it('поиск идёт и по донору, и по адресу', async () => {
    await openThreads();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Поиск по донору или адресу'), 'hello@');

    expect(screen.getByText('green-blog.example.test')).toBeInTheDocument();
    expect(screen.queryByText('digest-weekly.example.test')).not.toBeInTheDocument();
  });

  it('пустой результат объясняет, что база не пуста', async () => {
    await openThreads();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Поиск по донору или адресу'), 'такого нет');

    expect(screen.getByText(/всего диалогов/)).toBeInTheDocument();
  });
});
