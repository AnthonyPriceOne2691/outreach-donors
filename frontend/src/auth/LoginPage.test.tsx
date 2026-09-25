/**
 * Вход глазами человека: что он вводит и что видит в ответ.
 *
 * Проверяется не «сервер умеет», а «показано и отправлено» — сервер
 * заменён записанными ответами.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, HOME_ROUTES, NEWCOMER, TOKEN_KEY, signedIn } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

async function enter(email: string, password: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Почта'), email);
  await user.type(screen.getByLabelText('Пароль'), password);
  await user.click(screen.getByRole('button', { name: 'Войти' }));
}

describe('вход', () => {
  it('пускает и запоминает пропуск', async () => {
    const recorded = serve({ 'POST /api/auth/login': { body: signedIn(ADMIN) }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/login');

    await enter('админ@site.com', 'пароль-для-теста');

    expect(await screen.findByText(/Вошли как/)).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBe(signedIn(ADMIN).token);
    expect(recorded.calls[0]?.body).toEqual({
      email: 'админ@site.com',
      password: 'пароль-для-теста',
    });
  });

  it('показывает отказ сервера тем же текстом', async () => {
    serve({
      'POST /api/auth/login': { status: 401, body: { detail: 'Неверная почта или пароль' } },
    });
    renderWith(<AppRoutes />, '/login');

    await enter('админ@site.com', 'мимо');

    expect(await screen.findByText('Неверная почта или пароль')).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it('перебор — это «подождите», а не «неверный пароль»', async () => {
    serve({
      'POST /api/auth/login': {
        status: 429,
        body: { detail: 'Слишком много попыток входа. Повторите через 59 с.' },
        headers: { 'Retry-After': '59' },
      },
    });
    renderWith(<AppRoutes />, '/login');

    await enter('админ@site.com', 'мимо');

    expect(await screen.findByText('Подождите')).toBeInTheDocument();
    expect(screen.getByText(/Повторите через 59/)).toBeInTheDocument();
  });

  it('с разовым паролем ведёт сразу на смену, а не на обзор', async () => {
    serve({ 'POST /api/auth/login': { body: signedIn(NEWCOMER) } });
    renderWith(<AppRoutes />, '/login');

    await enter('новичок@site.com', 'разовый');

    await waitFor(() => {
      expect(screen.getByText('Смена пароля')).toBeInTheDocument();
    });
    expect(screen.getByText(/Пока он не сменён, остальное закрыто/)).toBeInTheDocument();
  });
});
