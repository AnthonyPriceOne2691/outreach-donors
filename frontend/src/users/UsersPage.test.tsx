/**
 * Админка: что показано и что уходит на сервер.
 *
 * Отдельно проверяется разовый пароль: он показывается один раз, и окно
 * не должно закрываться случайно — иначе сотрудник ждёт пароль, которого
 * уже нет, а восстановить его нечем.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AccessPatch, Permission, UserCard } from '../api/types';
import { ADMIN, NEWCOMER, OPERATOR, TOKEN_KEY, card } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve, type Call } from '../test/server';

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
    // Отключение — с подтверждением, и в нём сказано, что будет (аудит 09.10.2026).
    const ask = await screen.findByRole('dialog', { name: /^Отключить/, hidden: true });
    expect(within(ask).getByText(/перестанет пускать сразу/)).toBeInTheDocument();
    expect(patches(recorded.calls)).toHaveLength(0);
    await user.click(within(ask).getByRole('button', { name: 'Отключить', hidden: true }));

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    expect(patches(recorded.calls)[0]?.body).toEqual({ is_active: false });
  });

  it('отмена в подтверждении ничего не отправляет', async () => {
    const recorded = await openUsers();
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Учётка оператор@site.com включена'));
    const ask = await screen.findByRole('dialog', { name: /^Отключить/, hidden: true });
    await user.click(within(ask).getByRole('button', { name: 'Отмена', hidden: true }));

    expect(patches(recorded.calls)).toHaveLength(0);
    expect(screen.getByLabelText('Учётка оператор@site.com включена')).toBeChecked();
  });

  it('в правах — все восемь, а не пять: продажи, домены и цены тоже выдаются', async () => {
    await openUsers();
    const user = userEvent.setup();

    const row = screen.getByText('оператор@site.com').closest('tr');
    await user.click(within(row as HTMLElement).getByRole('button', { name: /Права/ }));

    for (const name of ['Подтверждать цены', 'Домены рассылки', 'Продажи', 'Заводить учётки']) {
      expect(await screen.findByLabelText(name)).toBeInTheDocument();
    }
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
    const ask = await screen.findByRole('dialog', { name: /^Отключить/, hidden: true });
    await user.click(within(ask).getByRole('button', { name: 'Отключить', hidden: true }));

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
    const ask = await screen.findByRole('dialog', { name: /^Сбросить пароль/, hidden: true });
    expect(within(ask).getByText(/новый разовый покажется один раз/)).toBeInTheDocument();
    await user.click(within(ask).getByRole('button', { name: 'Сбросить', hidden: true }));

    expect(await screen.findByText('QwErTy12QwErTy34')).toBeInTheDocument();
  });

  it('отказ сервера в праве на себя — переключатель стоит, как стоял, отказ словами', async () => {
    const recorded = await openUsers({
      'PATCH /api/users/1': {
        status: 409,
        body: {
          detail:
            'Нельзя снять права с самого себя. Попросите другого админа — иначе выйти обратно будет некому',
        },
      },
    });
    const user = userEvent.setup();

    // Своя почта есть и в шапке («Вошли как»): строка — та, что в таблице.
    const row = screen
      .getAllByText('админ@site.com')
      .map((text) => text.closest('tr'))
      .find((found) => found !== null);
    await user.click(within(row as HTMLElement).getByRole('button', { name: /Права/ }));
    await user.click(await screen.findByLabelText('Заводить учётки'));

    expect(await screen.findByText(/Попросите другого админа/)).toBeInTheDocument();
    expect(patches(recorded.calls).map((call) => call.body)).toEqual([
      { permissions: { users: false } },
    ]);
    expect(screen.getByLabelText('Заводить учётки')).toBeChecked();
  });

  it('два быстрых переключателя — выданы оба права, второй не затирает первый', async () => {
    /** Оператор, каким его отдаёт сервер с исключениями: роль плюс выданное, минус отобранное. */
    const operatorWith = (overrides: Partial<Record<Permission, boolean>>): UserCard => {
      const kept = OPERATOR.permissions.filter((key) => overrides[key] !== false);
      const given = (Object.keys(overrides) as Permission[]).filter((key) => overrides[key]);
      return card(OPERATOR, { overrides, permissions: [...new Set([...kept, ...given])].sort() });
    };
    // Сервер с памятью: правка заменяет исключения целиком, список отдаёт последние.
    let overrides: Partial<Record<Permission, boolean>> = {};
    const recorded = await openUsers({
      'GET /api/users': () => ({ body: [card(ADMIN), operatorWith(overrides), card(NEWCOMER)] }),
      'PATCH /api/users/2': (call: Call) => {
        overrides = (call.body as AccessPatch).permissions ?? overrides;
        return { body: operatorWith(overrides) };
      },
    });

    const row = screen.getByText('оператор@site.com').closest('tr');
    await userEvent
      .setup()
      .click(within(row as HTMLElement).getByRole('button', { name: /Права/ }));
    const senders = await screen.findByLabelText('Домены рассылки');
    // Второй щелчок — пока первая правка ещё в пути (QA 10.10.2026).
    fireEvent.click(senders);
    fireEvent.click(screen.getByLabelText('Заводить учётки'));

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(2));
    expect(patches(recorded.calls).map((call) => call.body)).toEqual([
      { permissions: { senders: true } },
      { permissions: { senders: true, users: true } },
    ]);
    await waitFor(() => expect(screen.getByLabelText('Заводить учётки')).toBeChecked());
    expect(screen.getByLabelText('Домены рассылки')).toBeChecked();
    expect(overrides).toEqual({ senders: true, users: true });
  });
});
