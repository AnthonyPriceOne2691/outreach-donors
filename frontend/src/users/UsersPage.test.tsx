/**
 * Админка: что показано и что уходит на сервер.
 *
 * Отдельно проверяется разовый пароль: он показывается один раз, и окно
 * не должно закрываться случайно — иначе сотрудник ждёт пароль, которого
 * уже нет, а восстановить его нечем.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Call } from '../test/server';
import { ADMIN, NEWCOMER, OPERATOR, TOKEN_KEY, card } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const LIST = [card(ADMIN), card(OPERATOR), card(NEWCOMER)];

function patches(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'PATCH');
}

async function openUsers(routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/users': { body: LIST },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/users');
  await screen.findByText('оператор@site.com');
  return recorded;
}

describe('учётки', () => {
  it('показывает, кто не сменил разовый пароль', async () => {
    await openUsers();

    const row = screen.getByText('новичок@site.com').closest('tr');
    expect(within(row as HTMLElement).getByText('не сменил разовый пароль')).toBeInTheDocument();
  });

  it('заводит учётку и показывает разовый пароль один раз', async () => {
    const issued = {
      user: card({ ...OPERATOR, id: 9, email: 'новый@site.com' }, { must_change_password: true }),
      password: 'SrSoH3WhmVXhaGxw',
      note: 'Пароль показан один раз. При входе система потребует его сменить.',
    };
    const recorded = await openUsers({ 'POST /api/users': { status: 201, body: issued } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Завести учётку' }));
    await user.type(await screen.findByLabelText('Почта'), 'новый@site.com');
    await user.click(screen.getByRole('button', { name: 'Завести' }));

    expect(await screen.findByText('SrSoH3WhmVXhaGxw')).toBeInTheDocument();
    expect(screen.getByText(/показан один раз/)).toBeInTheDocument();
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
      email: 'новый@site.com',
      role: 'operator',
    });

    // Окно не закрывается по Esc: случайное нажатие стоило бы нового сброса.
    await user.keyboard('{Escape}');
    expect(screen.getByText('SrSoH3WhmVXhaGxw')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Записал, закрыть' }));
    await waitFor(() => {
      expect(screen.queryByText('SrSoH3WhmVXhaGxw')).not.toBeInTheDocument();
    });
  });

  it('отключение учётки уходит правкой, а не удалением', async () => {
    const recorded = await openUsers({
      'PATCH /api/users/2': { body: card(OPERATOR, { is_active: false }) },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Учётка оператор@site.com включена'));

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    expect(patches(recorded.calls)[0]?.body).toEqual({ is_active: false });
  });

  it('отказ сервера показывается целиком', async () => {
    await openUsers({
      'PATCH /api/users/1': {
        status: 409,
        body: {
          detail:
            'Нельзя снять права с самого себя. Попросите другого админа — иначе выйти обратно будет некому',
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Учётка админ@site.com включена'));

    expect(await screen.findByText(/Попросите другого админа/)).toBeInTheDocument();
  });

  it('точечное право выдаётся исключением поверх роли', async () => {
    const recorded = await openUsers({
      'PATCH /api/users/2': { body: card(OPERATOR, { overrides: { send: true } }) },
    });
    const user = userEvent.setup();

    const row = screen.getByText('оператор@site.com').closest('tr');
    await user.click(within(row as HTMLElement).getByRole('button', { name: /Права/ }));
    await user.click(await screen.findByLabelText('Отправлять письма'));

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    expect(patches(recorded.calls)[0]?.body).toEqual({ permissions: { send: true } });
  });

  it('сброс пароля показывает новый разовый', async () => {
    await openUsers({
      'POST /api/users/2/password': {
        body: {
          user: card(OPERATOR, { must_change_password: true }),
          password: 'QwErTy12QwErTy34',
          note: 'Пароль показан один раз. При входе система потребует его сменить.',
        },
      },
    });
    const user = userEvent.setup();

    const row = screen.getByText('оператор@site.com').closest('tr');
    await user.click(within(row as HTMLElement).getByRole('button', { name: 'Сбросить пароль' }));

    expect(await screen.findByText('QwErTy12QwErTy34')).toBeInTheDocument();
  });
});
