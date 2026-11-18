/**
 * Домен IDN в «Диалогах» (18.11.2026): база хранит имя хоста в ASCII, а список, шапка переписки
 * и поиск — как его пишут люди: «пример.рф», а не «xn--e1afmkfd.xn--p1ai». Данные выдуманные.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

function thread(id: number, host: string, email: string) {
  return {
    id,
    host,
    contact_email: email,
    campaign: 'Демонстрация',
    stage: 'donors',
    state: 'waiting',
    messages_sent: 1,
    last_event_at: `2026-09-1${id}T10:00:00+00:00`,
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  };
}

const IDN = thread(1, 'xn--e1afmkfd.xn--p1ai', 'info@xn--e1afmkfd.xn--p1ai');
const THREADS = [IDN, thread(2, 'green-blog.example.test', 'hello@green-blog.example.test')];

function open(path: string) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads': { body: THREADS },
    'GET /api/replies/calibration': { body: { versions: [] } },
    'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
    'GET /api/threads/1': {
      body: { card: IDN, letters: [], incoming: [], corridor: { min: 0.15, max: 0.25 } },
    },
  });
  renderWith(<AppRoutes />, path);
}

/** Строки списка — их сайты, сверху вниз. */
function hosts(): string[] {
  return within(screen.getByRole('navigation', { name: 'Список диалогов' }))
    .queryAllByRole('link')
    .map((row) => row.querySelector('.threadRowHost')?.textContent ?? '');
}

describe('домен IDN в «Диалогах»', () => {
  it('строка списка — домен и адрес раскодированы', async () => {
    open('/threads');

    const row = (await screen.findByText('пример.рф')).closest('a')!;
    expect(within(row).getByText('info@пример.рф')).toBeInTheDocument();
    expect(row).not.toHaveTextContent('xn--');
  });

  it('поиск по-русски находит диалог, у которого в данных `xn--`', async () => {
    open('/threads');
    const user = userEvent.setup();
    await screen.findByText('пример.рф');

    await user.type(screen.getByLabelText('Поиск по донору или адресу'), 'Пример');

    expect(hosts()).toEqual(['пример.рф']);
  });

  it('поиск по ASCII-записи находит тот же диалог', async () => {
    open('/threads');
    const user = userEvent.setup();
    await screen.findByText('пример.рф');

    await user.type(screen.getByLabelText('Поиск по донору или адресу'), 'xn--e1a');

    expect(hosts()).toEqual(['пример.рф']);
  });

  it('шапка переписки — домен и адрес раскодированы', async () => {
    open('/threads/1');

    const title = await screen.findByRole('heading', { name: 'пример.рф' });
    const head = title.closest<HTMLElement>('.threadHead')!;
    expect(within(head).getByText(/^info@пример\.рф · кампания «Демонстрация»/)).toBeVisible();
    expect(head).not.toHaveTextContent('xn--');
  });
});
