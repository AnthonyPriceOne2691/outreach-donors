/**
 * Смена своего пароля: что проверяется до сервера и что уходит ему.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { HOME_ROUTES, NEWCOMER, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

async function fill(current: string, next: string, repeat: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Текущий пароль'), current);
  await user.type(screen.getByLabelText('Новый пароль'), next);
  await user.type(screen.getByLabelText('Новый пароль ещё раз'), repeat);
  await user.click(screen.getByRole('button', { name: 'Сменить' }));
}

describe('смена своего пароля', () => {
  it('короткий пароль до сервера не доходит', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({ 'GET /api/auth/me': { body: NEWCOMER } });
    renderWith(<AppRoutes />, '/password');
    await screen.findByText('Смена пароля');

    await fill('разовый', 'коротко', 'коротко');

    expect(screen.getByText(/Не короче 10 знаков/)).toBeInTheDocument();
    expect(recorded.calls.filter((call) => call.method === 'POST')).toHaveLength(0);
  });

  it('несовпадение повторов ловится на месте', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: NEWCOMER } });
    renderWith(<AppRoutes />, '/password');
    await screen.findByText('Смена пароля');

    await fill('разовый', 'три слова подряд', 'три слова подряд!');

    expect(screen.getByText('Пароли не совпадают')).toBeInTheDocument();
  });

  it('старый пароль уходит вместе с новым и открывает работу', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    let changed = false;
    const recorded = serve({
      'GET /api/auth/me': () => ({ body: changed ? OPERATOR : NEWCOMER }),
      'POST /api/auth/password': () => {
        changed = true;
        return { status: 204 };
      },
      ...HOME_ROUTES,
    });
    renderWith(<AppRoutes />, '/password');
    await screen.findByText('Смена пароля');

    await fill('разовый', 'три слова подряд', 'три слова подряд');

    // Карточка перечитывается: без этого экран остался бы закрытым
    // с точки зрения фронта, хотя сервер уже пустил.
    expect(await screen.findByText(/Вошли как/)).toBeInTheDocument();
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
      current: 'разовый',
      new: 'три слова подряд',
    });
  });

  it('отказ сервера показывается целиком', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: NEWCOMER },
      'POST /api/auth/password': { status: 400, body: { detail: 'Старый пароль не подошёл' } },
    });
    renderWith(<AppRoutes />, '/password');
    await screen.findByText('Смена пароля');

    await fill('мимо', 'три слова подряд', 'три слова подряд');

    expect(await screen.findByText('Старый пароль не подошёл')).toBeInTheDocument();
  });
});
