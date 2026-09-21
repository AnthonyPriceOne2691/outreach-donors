/**
 * Стоп-лист: что экран показывает и что он не даёт сделать молча.
 *
 * Главное здесь — разница между решением адресата и нашим собственным.
 * Снять отписку можно, но объяснение обязательно: это единственное
 * место сервиса, где человек разрешает написать тому, кто просил
 * не писать.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Call } from '../test/server';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const STOP_LIST = {
  rows: [
    {
      id: 1,
      host: 'donor.example.test',
      email: null,
      reason: 'unsubscribed',
      stage: null,
      created_by: 'страница отписки',
      created_at: '2026-09-21T10:00:00+00:00',
      donor_decision: true,
    },
    {
      id: 2,
      host: null,
      email: 'sales@supplier.example.test',
      reason: 'supplier',
      stage: null,
      created_by: 'анна@site.com',
      created_at: '2026-09-20T10:00:00+00:00',
      donor_decision: false,
    },
  ],
  total: 2,
  donor_decisions: 1,
};

async function openStopList(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/suppressions': { body: STOP_LIST },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/suppressions');
  await screen.findByText('donor.example.test');
  return recorded;
}

describe('стоп-лист', () => {
  it('решение адресата видно в строке, а не в подсказке', async () => {
    await openStopList();

    // По этому слову человек решает, можно ли снимать запись молча.
    // Ищем в таблице: «поставщик» есть ещё и в выборе причины над ней.
    const table = screen.getByRole('table');
    expect(within(table).getByText('отписался')).toBeInTheDocument();
    expect(within(table).getByText('поставщик')).toBeInTheDocument();
    expect(within(table).getByText('страница отписки')).toBeInTheDocument();
  });

  it('снятие отписки требует причины', async () => {
    await openStopList();
    const user = userEvent.setup();

    await user.click(screen.getAllByRole('button', { name: 'Снять' })[0]!);

    expect(await screen.findByText('Это решение адресата')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Снять запись' })).toBeDisabled();
  });

  it('причина уходит на сервер вместе со снятием', async () => {
    const recorded = await openStopList({
      'POST /api/suppressions/1/remove': { body: { ...STOP_LIST.rows[0] } },
      'GET /api/suppressions': { body: STOP_LIST },
    });
    const user = userEvent.setup();

    await user.click(screen.getAllByRole('button', { name: 'Снять' })[0]!);
    await user.type(await screen.findByLabelText('Почему снимаем'), 'написал «пишите»');
    await user.click(screen.getByRole('button', { name: 'Снять запись' }));

    await screen.findByText(/снова уходят/);
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent).toHaveLength(1);
    expect(sent[0]?.body).toEqual({ reason: 'написал «пишите»' });
  });

  it('свою запись снимают без объяснений', async () => {
    await openStopList();
    const user = userEvent.setup();

    await user.click(screen.getAllByRole('button', { name: 'Снять' })[1]!);

    expect(await screen.findByText(/Запись завели мы сами/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Почему снимаем')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Снять запись' })).toBeEnabled();
  });

  it('оператор видит список, но не правит его', async () => {
    await openStopList({}, OPERATOR);

    expect(screen.getByText('donor.example.test')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Снять' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Больше не писать' })).not.toBeInTheDocument();
  });

  it('новая запись уходит доменом или адресом', async () => {
    const recorded = await openStopList({
      'POST /api/suppressions': { body: { ...STOP_LIST.rows[1], id: 3 } },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Домен или адрес'), 'supplier.example.test');
    await user.click(screen.getByRole('button', { name: 'Больше не писать' }));

    await screen.findByText(/сняты с очереди/);
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({ target: 'supplier.example.test', reason: 'manual' });
  });
});
