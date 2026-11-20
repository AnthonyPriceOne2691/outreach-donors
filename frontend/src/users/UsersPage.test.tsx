/**
 * Админка: что показано и что уходит на сервер.
 *
 * Отдельно проверяется разовый пароль: он показывается один раз, и окно
 * не должно закрываться случайно — иначе сотрудник ждёт пароль, которого
 * уже нет, а восстановить его нечем.
 *
 * Роль, права, включение, отключение и сброс чужого пароля уходят с паролем админа
 * (аудит 20.11.2026, №10): каждое действие идёт через окно «Ваш пароль».
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { UserEvent } from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AccessPatch, Permission, UserCard } from '../api/types';
import { ADMIN, NEWCOMER, OPERATOR, TOKEN_KEY, card } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve, type Call } from '../test/server';

const LIST = [card(ADMIN), card(OPERATOR), card(NEWCOMER)];

/** Пароль админа, которым он подтверждает действие. */
const MINE = 'пароль-админа';

function patches(calls: Call[]): Call[] {
  return calls.filter((call) => call.method === 'PATCH');
}

/** Окно подтверждения паролем админа: найти по вопросу, вписать пароль, нажать глагол. */
async function confirmWith(user: UserEvent, question: RegExp, verb: string): Promise<HTMLElement> {
  const ask = await screen.findByRole('dialog', { name: question, hidden: true });
  await user.type(within(ask).getByLabelText('Ваш пароль'), MINE);
  await user.click(within(ask).getByRole('button', { name: verb, hidden: true }));
  return ask;
}

/** Окно прав учётки — по строке её почты (своя почта есть и в шапке: строка — та, что в таблице). */
async function openRights(user: UserEvent, email: string): Promise<void> {
  const row = screen
    .getAllByText(email)
    .map((text) => text.closest('tr'))
    .find((found) => found !== null);
  await user.click(within(row as HTMLElement).getByRole('button', { name: /Права/ }));
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

  it('отключение учётки уходит правкой с паролем админа, а не удалением', async () => {
    const recorded = await openUsers({
      'PATCH /api/users/2': { body: card(OPERATOR, { is_active: false }) },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Учётка оператор@site.com включена'));
    // Отключение — с подтверждением, и в нём сказано, что будет (аудит 09.10.2026).
    const ask = await screen.findByRole('dialog', { name: /^Отключить/, hidden: true });
    expect(within(ask).getByText(/перестанет пускать сразу/)).toBeInTheDocument();
    expect(patches(recorded.calls)).toHaveLength(0);
    await confirmWith(user, /^Отключить/, 'Отключить');

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    expect(patches(recorded.calls)[0]?.body).toEqual({ is_active: false, current_password: MINE });
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /^Отключить/, hidden: true })).toBeNull();
    });
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

  it('неверный пароль админа — окно остаётся, отказ словами, правки нет', async () => {
    const refused = 'Неверный пароль — подтвердите действие своим паролем';
    const recorded = await openUsers({
      'PATCH /api/users/2': {
        status: 403,
        body: { detail: refused },
        headers: { 'X-Access-Refusal': 'confirm-password' },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Учётка оператор@site.com включена'));
    const ask = await confirmWith(user, /^Отключить/, 'Отключить');

    expect(await within(ask).findByText(refused)).toBeInTheDocument();
    // Сеанс цел: это не «входа нет», и окно ждёт пароль заново — набранный стёрт.
    expect(within(ask).getByLabelText('Ваш пароль')).toHaveValue('');
    expect(patches(recorded.calls)).toHaveLength(1);
    expect(screen.getByLabelText('Учётка оператор@site.com включена')).toBeChecked();
    expect(screen.queryByText('Вход для сотрудников')).not.toBeInTheDocument();
  });

  it('пустой пароль не уходит на сервер', async () => {
    const recorded = await openUsers();
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Учётка оператор@site.com включена'));
    const ask = await screen.findByRole('dialog', { name: /^Отключить/, hidden: true });
    await user.click(within(ask).getByRole('button', { name: 'Отключить', hidden: true }));

    expect(await within(ask).findByText(/Впишите свой пароль/)).toBeInTheDocument();
    expect(patches(recorded.calls)).toHaveLength(0);
  });

  it('включение — тоже с паролем админа', async () => {
    const off = card(OPERATOR, { is_active: false, permissions: [] });
    const recorded = await openUsers({
      'GET /api/users': { body: [card(ADMIN), off, card(NEWCOMER)] },
      'PATCH /api/users/2': { body: card(OPERATOR) },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Учётка оператор@site.com включена'));
    await confirmWith(user, /^Включить/, 'Включить');

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    expect(patches(recorded.calls)[0]?.body).toEqual({ is_active: true, current_password: MINE });
  });

  it('в правах — все восемь, а не пять: продажи, домены и цены тоже выдаются', async () => {
    await openUsers();
    const user = userEvent.setup();

    const row = screen.getByText('оператор@site.com').closest('tr');
    await user.click(within(row as HTMLElement).getByRole('button', { name: /Права/ }));

    // Переключатели прав — по роли, а не по подписи: подпись «Продажи» носит и группа меню.
    for (const name of ['Подтверждать цены', 'Домены рассылки', 'Продажи', 'Заводить учётки']) {
      expect(await screen.findByRole('switch', { name, hidden: true })).toBeInTheDocument();
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
    await confirmWith(user, /^Отключить/, 'Отключить');

    expect(await screen.findByText(/Попросите другого админа/)).toBeInTheDocument();
  });

  it('точечное право выдаётся исключением поверх роли', async () => {
    const recorded = await openUsers({
      'PATCH /api/users/2': { body: card(OPERATOR, { overrides: { send: true } }) },
    });
    const user = userEvent.setup();

    await openRights(user, 'оператор@site.com');
    await user.click(await screen.findByLabelText('Отправлять письма'));
    const ask = await screen.findByRole('dialog', { name: /^Изменить права/, hidden: true });
    expect(within(ask).getByText('«Отправлять письма» — выдать отдельно от роли.')).toBeTruthy();
    await confirmWith(user, /^Изменить права/, 'Изменить');

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    expect(patches(recorded.calls)[0]?.body).toEqual({
      permissions: { send: true },
      current_password: MINE,
    });
  });

  it('смена роли — с паролем админа и словами, что на что меняется', async () => {
    const recorded = await openUsers({
      'PATCH /api/users/2': { body: card({ ...OPERATOR, role: 'admin' }) },
    });
    const user = userEvent.setup();

    // Пункты есть у выбора роли каждой строки: берём список того, что открыли.
    const field = screen.getByRole('textbox', { name: 'Роль оператор@site.com' });
    await user.click(field);
    const list = within(
      document.getElementById(field.getAttribute('aria-controls') ?? '') as HTMLElement,
    );
    await user.click(await list.findByRole('option', { name: 'Админ', hidden: true }));
    const ask = await screen.findByRole('dialog', { name: /^Сменить роль/, hidden: true });
    expect(within(ask).getByText(/Оператор → Админ/)).toBeInTheDocument();
    expect(patches(recorded.calls)).toHaveLength(0);
    await confirmWith(user, /^Сменить роль/, 'Сменить роль');

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    expect(patches(recorded.calls)[0]?.body).toEqual({ role: 'admin', current_password: MINE });
  });

  it('сброс пароля — с паролем админа, показывает новый разовый', async () => {
    const recorded = await openUsers({
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
    // Окно говорит правду: сброс закрывает и открытые сеансы (проверка прода 10.10.2026).
    expect(within(ask).getByText(/открытые сеансы перестанут действовать/)).toBeInTheDocument();
    await confirmWith(user, /^Сбросить пароль/, 'Сбросить');

    expect(await screen.findByText('QwErTy12QwErTy34')).toBeInTheDocument();
    const posted = recorded.calls.find((call) => call.path === '/api/users/2/password');
    expect(posted?.body).toEqual({ current_password: MINE });
  });

  it('у отключённой учётки — «права не действуют», а не «Права (0)» и роль, что ничего не даёт', async () => {
    // Проверка прода 10.10.2026: «Права (0)» и «как у роли» у каждого права читались
    // «роль ничего не даёт», а не действует ничего потому, что учётка отключена.
    const off = card(OPERATOR, { is_active: false, permissions: [] });
    await openUsers({ 'GET /api/users': { body: [card(ADMIN), off, card(NEWCOMER)] } });
    const user = userEvent.setup();

    const row = screen.getByText('оператор@site.com').closest('tr') as HTMLElement;
    expect(within(row).queryByRole('button', { name: 'Права (0)' })).not.toBeInTheDocument();
    await user.click(within(row).getByRole('button', { name: 'Права не действуют' }));

    expect(
      await screen.findByText(/ни одно право не действует, пока её не включат/),
    ).toBeInTheDocument();
  });

  it('своя строка — «Сменить пароль», а не сброс: сброс себе закрыл бы этот сеанс', async () => {
    await openUsers();

    // Своя почта есть и в шапке («Вошли как»): строка — та, что в таблице.
    const own = screen
      .getAllByText('админ@site.com')
      .map((text) => text.closest('tr'))
      .find((found) => found !== null) as HTMLElement;
    expect(within(own).getByRole('link', { name: 'Сменить пароль' })).toHaveAttribute(
      'href',
      '/password',
    );
    expect(within(own).queryByRole('button', { name: 'Сбросить пароль' })).not.toBeInTheDocument();
    const other = screen.getByText('оператор@site.com').closest('tr') as HTMLElement;
    expect(within(other).getByRole('button', { name: 'Сбросить пароль' })).toBeInTheDocument();
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

    await openRights(user, 'админ@site.com');
    await user.click(await screen.findByLabelText('Заводить учётки'));
    await confirmWith(user, /^Изменить права/, 'Изменить');

    expect(await screen.findByText(/Попросите другого админа/)).toBeInTheDocument();
    expect(patches(recorded.calls).map((call) => call.body)).toEqual([
      { permissions: { users: false }, current_password: MINE },
    ]);
    await user.click(screen.getByRole('button', { name: 'Отмена', hidden: true }));
    await openRights(user, 'админ@site.com');
    expect(await screen.findByLabelText('Заводить учётки')).toBeChecked();
  });

  it('два переключателя подряд — выданы оба права, второй не затирает первый', async () => {
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

    const user = userEvent.setup();

    // Вторая правка собирается из ответа на первую, а не из списка до неё (QA 10.10.2026).
    await openRights(user, 'оператор@site.com');
    await user.click(await screen.findByLabelText('Домены рассылки'));
    await confirmWith(user, /^Изменить права/, 'Изменить');
    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(1));
    await openRights(user, 'оператор@site.com');
    await user.click(await screen.findByLabelText('Заводить учётки'));
    await confirmWith(user, /^Изменить права/, 'Изменить');

    await waitFor(() => expect(patches(recorded.calls)).toHaveLength(2));
    expect(patches(recorded.calls).map((call) => call.body)).toEqual([
      { permissions: { senders: true }, current_password: MINE },
      { permissions: { senders: true, users: true }, current_password: MINE },
    ]);
    expect(overrides).toEqual({ senders: true, users: true });
  });
});
